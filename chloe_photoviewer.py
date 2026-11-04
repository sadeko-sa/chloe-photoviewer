#!/usr/bin/env python3
"""
chloe_photoviewer.py - Chloe Photoviewer, a small cross platform image viewer
(Windows, Linux, macOS). Needs Python 3.8+, Tkinter and Pillow.

    python chloe_photoviewer.py [image or folder]

Every image opens as a tab of one window; the tab bar shows with two or more tabs.
Each tab keeps its own zoom, position and rotation. Ctrl+N moves the current tab
into a new window (with a single tab it opens an empty window). Windows do not
share tabs or sessions.

Mouse (FastStone style):
    click                    zoom to 100% at the clicked point; drag to move around
    click again              back to "fit to window"
    click and hold           zoom to 200% while the button is down; the mouse position
                             over the fitted picture picks the same relative spot of
                             the zoomed one; release returns to "fit"
    wheel                    nothing by default; Settings: previous / next image
                             (Ctrl + wheel = zoom) or zoom (Ctrl + wheel = previous / next)
    middle click on a tab    close the tab
    right click              context menu

Keys:
    Left / Right / Space / BackSpace / PageUp / PageDown   previous / next image
    Home / End      first / last image          R / L   rotate right / left
    F               fit width / height          0       fit to window
    1 / 2           100% / 200%                 + / -   zoom in / out
    I               show / hide info            F11     full screen (Esc leaves it)
    Ctrl+Tab        next tab (Shift: previous)  Ctrl+N  new window
    Ctrl+O          open image(s)               Ctrl+,  settings
    Ctrl+W          close tab                   Ctrl+Q  close window
    F5              quick save: the tabs of this window (session "quicksave")

Only one program process runs (Settings > General > single instance): starting it
again sends the file to the running program, which opens it as a tab of the window
used last, and exits.

Rotating only changes how the picture is shown. No file is ever written or modified.

Language and theme: lang/<code>.json and theme/<code>.json next to this script, used
from the next start (see chloe_lang.py and chloe_theme.py).

The config is saved next to this script (chloe_photoviewer.json). For shared installs
turn on Settings > General > "Save the config per user"; it is then saved per user:
    Windows  %APPDATA%\\ChloePhotoviewer\\chloe_photoviewer.json
    Linux    ~/.config/chloe-photoviewer/chloe_photoviewer.json
    macOS    ~/Library/Application Support/ChloePhotoviewer/chloe_photoviewer.json
If the program folder is not writable the per-user location is used as well.
"""

import json
import math
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client, Listener
from tkinter import ttk, messagebox, filedialog

try:
    from PIL import Image, ImageOps, ImageTk
except ImportError:
    sys.exit("Chloe Photoviewer needs Pillow:  pip install pillow\n"
             "(Debian/Ubuntu also:  sudo apt install python3-tk python3-pil.imagetk)")

try:                                   # optional: drop images on the window to open them
    import tkinterdnd2
except ImportError:
    tkinterdnd2 = None

from chloe_lang import t, list_languages, resolve_language, set_language
from chloe_theme import (ThemeDialog, apply_theme, list_themes, resolve_color,
                         resolve_theme, set_theme)

APP_NAME = "Chloe Photoviewer"
VERSION = "1.10.1"

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"

IMAGE_EXTS = {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".bmp", ".dib",
              ".webp", ".tif", ".tiff", ".ico", ".ppm", ".pgm", ".pbm", ".tga",
              ".avif", ".heic", ".heif"}
"""Extensions shown. What really opens depends on Pillow (.heic / .avif need pillow-heif)."""

_TR = getattr(Image, "Transpose", Image)        # Pillow 9.1 moved the constants into enums
_RS = getattr(Image, "Resampling", Image)
ROTATE_CW = {1: _TR.ROTATE_270, 2: _TR.ROTATE_180, 3: _TR.ROTATE_90}
"""Quarter turns clockwise -> Pillow transpose constant."""

MIN_SCALE, MAX_SCALE = 0.02, 32.0
DRAG_PX = 4
"""Mouse movement (pixels) that turns a click into a drag."""

CONFIG_NAME = "chloe_photoviewer.json"
ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "image", "icon.png")
"""Program icon (square PNG, transparent, 256 px or more). Missing = Tk default."""

INSTANCE_ADDR = ("127.0.0.1", 47615)
"""Local address the running viewer listens on; later launches send their file here."""
INSTANCE_KEY = b"chloe-photoviewer"
"""Auth key of that connection (keeps other programs and other users out)."""

QUICK_NAME = "quicksave"
"""Name of the session F5 writes; it is a normal session (Settings > Sessions)."""

LAST_NAME = "lastsession"
"""Name of the session written when the last window closes; it is reopened at the next start without a file."""

DEFAULTS = {
    "language": "", "theme": "", "apps": None, "geometry": "1100x760", "zoomed": False,
    "enlarge_small": False, "show_info": False, "toolbar": True,
    "click_zoom": 100, "hold_zoom": 200, "hold_ms": 100, "wheel": "none",
    "last_path": "", "auto_rotate": "", "auto_dir": "cw", "single_instance": True,
    "sessions": [], "per_user_config": False,
}
"""Config entries and defaults ("apps": None = seed the open-with list;
"wheel": none / navigate / zoom; "sessions": [{"name", "paths"}];
"per_user_config": True = config in the per-user folder, the file next to the script then
only holds this switch)."""

RANGES = {"click_zoom": (10, 3200), "hold_zoom": (10, 3200), "hold_ms": (100, 2000)}
"""Allowed (min, max) of the numeric settings."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def detach_console():
    """
    Windows: when started with python.exe (console window), start the same
    program with pythonw.exe (no console) and exit. CHLOE_KEEP_CONSOLE=1 keeps
    the console, e.g. to read error messages.
    """
    if not IS_WIN or os.environ.get("CHLOE_KEEP_CONSOLE"):
        return
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.basename(exe).lower() != "python.exe" or not os.path.exists(pyw):
        return
    try:
        subprocess.Popen([pyw, os.path.abspath(__file__)] + sys.argv[1:],
                         creationflags=0x00000008, close_fds=True)   # DETACHED_PROCESS
    except OSError:
        return
    sys.exit(0)


def win_setup():
    """Windows: DPI aware (sharp window) and an own taskbar identity. Call before Tk()."""
    if not IS_WIN:
        return
    import ctypes
    try:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ChloePhotoviewer")
    except Exception:
        pass


def load_icons(root):
    """
    Set ICON_PATH as the icon of the root and of every window opened later.

    @param root  the Tk root window
    @return      the PhotoImage list; keep it alive as long as the program runs
    """
    try:
        with Image.open(ICON_PATH) as im:
            im = im.convert("RGBA")
            icons = [ImageTk.PhotoImage(ImageOps.pad(im, (n, n), _RS.LANCZOS, color=(0, 0, 0, 0)))
                     for n in (256, 64, 48, 32, 16)]
        root.iconphoto(True, *icons)
    except (OSError, ValueError, tk.TclError):
        return []
    return icons


def user_config_path():
    """@return full path of the per-user config file (platform specific)"""
    if IS_WIN:
        base = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "ChloePhotoviewer")
    elif IS_MAC:
        base = os.path.expanduser("~/Library/Application Support/ChloePhotoviewer")
    else:
        base = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                            "chloe-photoviewer")
    return os.path.join(base, CONFIG_NAME)


def local_config_path():
    """@return full path of the config file in the program folder (next to this script)"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_NAME)


def read_json(path):
    """@return the dict stored in a JSON file; empty if it is missing or invalid"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def local_writable():
    """@return True if the config can be written in the program folder"""
    path = local_config_path()
    if os.path.exists(path):
        return os.access(path, os.W_OK)
    return os.access(os.path.dirname(path), os.W_OK)


def per_user_config():
    """@return True if the file next to the script says the config lives in the per-user folder"""
    return read_json(local_config_path()).get("per_user_config") is True


def config_path(per_user=None):
    """
    @param per_user  True = per-user folder, False = program folder, None = as saved
    @return          full path of the config file in use; the per-user one also when the program
                     folder is not writable
    """
    if per_user is None:
        per_user = per_user_config()
    return user_config_path() if per_user or not local_writable() else local_config_path()


def clamp_int(value, lo, hi, default):
    """@return int(value) limited to lo..hi, or default if it is not a number"""
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def default_apps():
    """@return the "Open with" seed: well known editors that are installed, as {"name", "cmd"}"""
    apps = []
    if IS_WIN:
        apps.append({"name": "Paint", "cmd": 'mspaint.exe "{file}"'})
    elif IS_MAC:
        apps.append({"name": "Preview", "cmd": 'open -a Preview "{file}"'})
    for name, exe in (("GIMP", "gimp"), ("Krita", "krita"), ("Inkscape", "inkscape"),
                      ("Darktable", "darktable"), ("Pinta", "pinta")):
        if shutil.which(exe):
            apps.append({"name": name, "cmd": '%s "{file}"' % exe})
    return apps


def clean_apps(apps):
    """@return only the valid {"name", "cmd"} entries of a list read from the config"""
    out = []
    for a in apps if isinstance(apps, list) else []:
        if isinstance(a, dict) and isinstance(a.get("name"), str) and isinstance(a.get("cmd"), str) \
                and a["name"].strip() and a["cmd"].strip():
            out.append({"name": a["name"].strip(), "cmd": a["cmd"].strip()})
    return out


def clean_sessions(sessions):
    """@return only the valid {"name", "paths"} entries (unique names) of a list read from the config"""
    out, seen = [], set()
    for s in sessions if isinstance(sessions, list) else []:
        if not (isinstance(s, dict) and isinstance(s.get("name"), str) and s["name"].strip()):
            continue
        name = s["name"].strip()
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        paths = s.get("paths")
        out.append({"name": name, "paths": [p for p in paths if isinstance(p, str) and p.strip()]
                    if isinstance(paths, list) else []})
    return out


def upsert_session(sessions, name, paths):
    """@return `sessions` with the entry called `name` (case-insensitive) replaced by the new one, last"""
    out = [s for s in sessions if s["name"].lower() != name.lower()]
    out.append({"name": name, "paths": list(paths)})
    return out


def default_session_name(paths):
    """@return suggested session name: today's date + folder names, e.g. "2026-11-05 Holiday, Scans" """
    folders = []
    for p in paths:
        name = os.path.basename(os.path.dirname(os.path.abspath(p)))
        if name and name not in folders:
            folders.append(name)
    text = time.strftime("%Y-%m-%d")
    if folders:
        text += " " + ", ".join(folders[:3]) + (", ..." if len(folders) > 3 else "")
    return text


def load_config():
    """@return the config dict: defaults, overridden by the saved file (values checked)"""
    cfg = dict(DEFAULTS)
    per_user = per_user_config()
    path = config_path(per_user)
    data = read_json(path)
    if not data and path == local_config_path() and not os.path.exists(path):
        data = read_json(user_config_path())             # first start: take over an older per-user config
    cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    cfg["per_user_config"] = per_user
    cfg["apps"] = default_apps() if cfg["apps"] is None else clean_apps(cfg["apps"])
    cfg["sessions"] = clean_sessions(cfg["sessions"])
    for key, (lo, hi) in RANGES.items():
        cfg[key] = clamp_int(cfg[key], lo, hi, DEFAULTS[key])
    for key, ok in (("wheel", ("none", "navigate", "zoom")), ("auto_rotate", ("", "landscape", "portrait")),
                    ("auto_dir", ("cw", "ccw"))):
        if cfg[key] not in ok:
            cfg[key] = DEFAULTS[key]
    for key in ("enlarge_small", "show_info", "toolbar", "zoomed", "single_instance", "per_user_config"):
        cfg[key] = bool(cfg[key])
    for key in ("last_path", "geometry"):
        if not isinstance(cfg[key], str):
            cfg[key] = DEFAULTS[key]
    return cfg


def write_json(path, data):
    """Write a dict as JSON (temporary file first); raises OSError on failure."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(path + ".tmp", path)


def save_config(cfg, parent=None):
    """
    Write the config; show an error box on failure. Per-user mode writes the per-user file and
    leaves only the switch in the file next to the script, so the next start finds it.
    """
    per_user = bool(cfg.get("per_user_config"))
    try:
        write_json(config_path(per_user), cfg)
    except OSError as exc:
        messagebox.showerror(t("dlg.error"), t("msg.save_failed", error=exc), parent=parent)
        return
    if per_user:
        try:
            write_json(local_config_path(), {"per_user_config": True})
        except OSError:
            pass                                    # read-only program folder: the per-user file is used anyway


def claim_instance(path):
    """
    Make this process the only viewer. The first process listens on a local
    port; a later one sends its file to it. Binding the port is the lock, so
    nothing needs cleaning up after a crash.

    @param path  image or folder of the command line, or None
    @return      (forwarded, listener): forwarded True = a running viewer got the
                 path, exit now; listener None = port unavailable, run stand-alone
    """
    msg = os.path.abspath(path) if path else ""
    for _ in range(3):
        try:
            return False, Listener(INSTANCE_ADDR, authkey=INSTANCE_KEY)
        except OSError:
            pass
        try:
            with Client(INSTANCE_ADDR, authkey=INSTANCE_KEY) as conn:
                conn.send(msg)
            return True, None
        except (OSError, EOFError, AuthenticationError):
            time.sleep(0.1)
    return False, None


def split_command(cmd):
    """
    Split a command line into arguments the way the platform does.

    @param cmd  command line, e.g. 'gimp "{file}"'
    @return     list of arguments
    @raise ValueError  if the quoting is broken
    """
    cmd = cmd.strip()
    if not cmd:
        return []
    if not IS_WIN:
        return shlex.split(cmd)
    import ctypes
    from ctypes import wintypes
    fn = ctypes.windll.shell32.CommandLineToArgvW
    fn.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    fn.restype = ctypes.POINTER(wintypes.LPWSTR)
    count = ctypes.c_int(0)
    ptr = fn(cmd, ctypes.byref(count))
    if not ptr:
        raise ValueError("CommandLineToArgvW failed")
    try:
        return [ptr[i] for i in range(count.value)]
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free(ptr)


def build_argv(cmd, path):
    """
    Command line of an "Open with" program for one image.

    @param cmd   command with optional {file} and {dir} placeholders
    @param path  image path
    @return      argument list; the path is appended if the command has no {file}
    @raise ValueError  if the command is empty or cannot be split
    """
    parts = split_command(cmd)
    if not parts:
        raise ValueError("empty command")
    argv = [p.replace("{file}", path).replace("{dir}", os.path.dirname(path)) for p in parts]
    if not any("{file}" in p for p in parts):
        argv.append(path)
    return argv


def natural_key(name):
    """@return sort key that orders img2.jpg before img10.jpg (case-insensitive)"""
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]


def scan_folder(folder):
    """@return the image files of a folder (full paths), naturally sorted"""
    found = []
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if os.path.splitext(entry.name)[1].lower() in IMAGE_EXTS and entry.is_file():
                    found.append(entry.path)
    except OSError:
        pass
    found.sort(key=lambda p: natural_key(os.path.basename(p)))
    return found


def same_file(a, b):
    """@return True if two paths name the same file (case rules of the platform)"""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def size_text(num):
    """@return a byte count as text, e.g. 1.2 MB"""
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return "%d B" % value if unit == "B" else "%.1f %s" % (value, unit)
        value /= 1024


def cascade(geometry):
    """@return "WxH+X+Y" moved 30 px right and down, or None if it is not in that form"""
    m = re.match(r"(\d+x\d+)\+(-?\d+)\+(-?\d+)$", geometry)
    return "%s+%d+%d" % (m[1], int(m[2]) + 30, int(m[3]) + 30) if m else None


def _select_in_explorer(path):
    """
    Windows: open the folder of a file in Explorer with the file selected, via the
    shell API (no command line quoting, so every file name works).

    @param path  absolute path with backslashes
    @raise OSError  if the shell call fails
    """
    import ctypes
    from ctypes import wintypes
    ctypes.windll.ole32.CoInitialize(None)
    shell = ctypes.windll.shell32
    shell.ILCreateFromPathW.argtypes = [wintypes.LPCWSTR]
    shell.ILCreateFromPathW.restype = ctypes.c_void_p
    shell.ILFree.argtypes = [ctypes.c_void_p]
    shell.SHOpenFolderAndSelectItems.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                                 ctypes.c_void_p, ctypes.c_ulong]
    pidl = shell.ILCreateFromPathW(path)
    if not pidl:
        raise OSError("path not found: " + path)
    try:
        if shell.SHOpenFolderAndSelectItems(pidl, 0, None, 0) != 0:
            raise OSError("SHOpenFolderAndSelectItems failed")
    finally:
        shell.ILFree(pidl)


def reveal_in_folder(path):
    """
    Show a file in the system file manager, selected if the manager can do it.

    @param path  file path
    @raise OSError  if no file manager could be started
    """
    path = os.path.abspath(path)
    quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if IS_WIN:
        try:
            _select_in_explorer(os.path.normpath(path))
        except OSError:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif IS_MAC:
        subprocess.Popen(["open", "-R", path])
    else:
        try:
            from pathlib import Path
            done = subprocess.run(
                ["dbus-send", "--session", "--print-reply", "--dest=org.freedesktop.FileManager1",
                 "--type=method_call", "/org/freedesktop/FileManager1",
                 "org.freedesktop.FileManager1.ShowItems",
                 "array:string:" + Path(path).as_uri(), "string:"], timeout=4, **quiet)
            if done.returncode == 0:
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
        subprocess.Popen(["xdg-open", os.path.dirname(path)], start_new_session=True, **quiet)


def pick_images(parent, initialdir=None):
    """@return the absolute paths picked in the system "open images" dialog (maybe empty)"""
    picked = filedialog.askopenfilenames(
        parent=parent, title=t("dlg.open"), initialdir=initialdir or None,
        filetypes=[(t("filetype.images"), " ".join("*" + e for e in sorted(IMAGE_EXTS))),
                   (t("filetype.all"), "*.*")])
    return [os.path.abspath(p) for p in parent.tk.splitlist(picked)]


class Tip:
    """Small tooltip for a widget; the text is the language entry `key`."""

    def __init__(self, widget, key):
        self.widget, self.key, self.job, self.win = widget, key, None, None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _enter(self, _event):
        self._hide()
        self.job = self.widget.after(600, self._show)

    def _show(self):
        self.job = None
        self.win = tk.Toplevel(self.widget)
        self.win.wm_overrideredirect(True)
        tk.Label(self.win, text=t(self.key), relief="solid", bd=1, padx=5, pady=2).pack()
        self.win.wm_geometry("+%d+%d" % (self.widget.winfo_rootx(),
                                         max(self.widget.winfo_rooty() - 30, 0)))

    def _hide(self, _event=None):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.win:
            self.win.destroy()
            self.win = None


# --------------------------------------------------------------------------- #
# Dialogs
# --------------------------------------------------------------------------- #


def shorten_middle(text, font, width):
    """
    Shorten a text in the middle with "..." until it fits a pixel width.

    @param text   text to shorten, e.g. a file path
    @param font   tkinter.font.Font used to measure the text
    @param width  available width in pixels
    @return       text unchanged if it fits, else start + "..." + end
    """
    if width <= 0 or font.measure(text) <= width:
        return text

    def cut(keep):
        head = (keep + 1) // 2
        return text[:head] + "..." + text[len(text) - (keep - head):]

    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(cut(mid)) <= width:
            lo = mid
        else:
            hi = mid - 1
    return cut(lo)


def center_on(win, parent):
    """Place a dialog in the middle of its parent window."""
    win.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_reqwidth()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_reqheight()) // 2
    win.geometry("+%d+%d" % (max(x, 0), max(y, 0)))


def ok_cancel(parent, ok, cancel):
    """@return a frame (not yet placed) with the OK and Cancel buttons at its right edge"""
    row = ttk.Frame(parent)
    ttk.Button(row, text=t("btn.cancel"), command=cancel).pack(side="right")
    ttk.Button(row, text=t("btn.ok"), command=ok).pack(side="right", padx=3)
    return row


class Dialog(tk.Toplevel):
    """Modal dialog base. Build the widgets, then call show(); .result is None if cancelled."""

    def __init__(self, parent, title_key):
        super().__init__(parent)
        self.withdraw()
        self.title(t(title_key))
        self.transient(parent)
        self.parent, self.result = parent, None

    def show(self, focus=None, ok=None):
        """
        Center, show and wait until the dialog closes.

        @param focus  widget to focus first
        @param ok     function for the Return key
        """
        if ok:
            self.bind("<Return>", lambda e: ok())
        for seq in ("<Escape>", "<Control-w>", "<Control-W>"):
            self.bind(seq, lambda e: self.destroy())
        center_on(self, self.parent)
        self.deiconify()
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        (focus or self).focus_set()
        self.wait_window(self)


class ListBox(ttk.Frame):
    """A table with Add / Edit / Remove / Up / Down buttons that edits the list `items` in place."""

    def __init__(self, parent, items, cols, row, add, edit=None, confirm=None, add_key="btn.add"):
        """
        @param items    the list to edit (changed in place)
        @param cols     [(heading key, width, stretch)]
        @param row      item -> tuple of cell texts
        @param add      () -> list of new items (empty = nothing added)
        @param edit     index -> replacement item or None; None = no Edit button
        @param confirm  language key asked (with {count}) before removing; None = no question
        @param add_key  language key of the Add button
        Call refresh() once the owner is ready to deliver rows.
        """
        super().__init__(parent)
        self.items, self.row, self.confirm, self._add, self._edit = items, row, confirm, add, edit
        side = ttk.Frame(self)
        side.pack(side="right", fill="y", padx=(10, 0))
        buttons = [(add_key, self.add)] + ([("btn.edit", self.edit)] if edit else []) + [
            ("btn.remove", self.remove), ("btn.up", lambda: self.move(-1)),
            ("btn.down", lambda: self.move(1))]
        for key, cmd in buttons:
            ttk.Button(side, text=t(key), command=cmd).pack(fill="x", pady=2)
        box = ttk.Frame(self)
        box.pack(side="left", fill="both", expand=True)
        ids = [str(i) for i in range(len(cols))]
        self.tree = ttk.Treeview(box, columns=ids, show="headings", height=10, selectmode="extended")
        for cid, (key, width, stretch) in zip(ids, cols):
            self.tree.heading(cid, text=t(key), anchor="w")
            self.tree.column(cid, width=width, stretch=stretch)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        if edit:
            self.tree.bind("<Double-1>", lambda e: self.edit())

    def selected(self):
        """@return the selected indexes, ascending"""
        return sorted(int(i) for i in self.tree.selection())

    def refresh(self, select=()):
        """Show the items; `select` = indexes to select afterwards."""
        self.tree.delete(*self.tree.get_children())
        for i, item in enumerate(self.items):
            self.tree.insert("", "end", iid=str(i), values=self.row(item))
        for i in select:
            if 0 <= i < len(self.items):
                self.tree.selection_add(str(i))

    def add(self):
        new = self._add()
        if new:
            first = len(self.items)
            self.items.extend(new)
            self.refresh(range(first, len(self.items)))

    def edit(self):
        sel = self.selected()
        new = self._edit(sel[0]) if sel else None
        if new:
            self.items[sel[0]] = new
            self.refresh([sel[0]])

    def remove(self):
        sel = self.selected()
        if sel and (not self.confirm or messagebox.askyesno(
                t("dlg.remove"), t(self.confirm, count=len(sel)), parent=self)):
            self.items[:] = [x for i, x in enumerate(self.items) if i not in sel]
            self.refresh()

    def move(self, step):
        """Move the selected rows up (-1) or down (+1)."""
        sel = self.selected()
        if not sel or sel[0] + step < 0 or sel[-1] + step >= len(self.items):
            return
        for i in (sel if step < 0 else sel[::-1]):
            self.items[i], self.items[i + step] = self.items[i + step], self.items[i]
        self.refresh([i + step for i in sel])


class AppDialog(Dialog):
    """Add / edit one "Open with" program; .result = {"name", "cmd"} or None."""

    def __init__(self, parent, app=None, taken=()):
        """
        @param app    {"name", "cmd"} to edit, or None to add
        @param taken  names used by the other programs
        """
        super().__init__(parent, "dlg.edit_app" if app else "dlg.add_app")
        self.taken = {n.lower() for n in taken}
        self.name_var = tk.StringVar(value=app["name"] if app else "")
        self.cmd_var = tk.StringVar(value=app["cmd"] if app else "")
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        ttk.Label(frm, text=t("field.name")).grid(row=0, column=0, sticky="w", pady=4)
        name = ttk.Entry(frm, textvariable=self.name_var, width=56)
        name.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(8, 0))
        ttk.Label(frm, text=t("field.cmd")).grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.cmd_var, width=56).grid(row=1, column=1, sticky="ew", padx=(8, 0))
        ttk.Button(frm, text=t("btn.pick"), command=self._pick).grid(row=1, column=2, padx=(6, 0))
        ttk.Label(frm, text=t("hint.app_cmd"), wraplength=480, justify="left").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ok_cancel(frm, self._ok, self.destroy).grid(row=3, column=0, columnspan=3, sticky="e", pady=(12, 0))
        self.show(name, self._ok)

    def _pick(self):
        """Choose a program file; the command becomes: "<program>" "{file}"."""
        path = filedialog.askopenfilename(parent=self, title=t("btn.pick"))
        if path:
            self.cmd_var.set('"%s" "{file}"' % path)
            if not self.name_var.get().strip():
                self.name_var.set(os.path.splitext(os.path.basename(path))[0])

    def _ok(self):
        name, cmd = self.name_var.get().strip(), self.cmd_var.get().strip()
        try:
            if not name:
                raise ValueError(t("msg.name_required"))
            if not cmd:
                raise ValueError(t("msg.cmd_required"))
            if name.lower() in self.taken:
                raise ValueError(t("msg.name_exists"))
            split_command(cmd)
        except ValueError as exc:
            messagebox.showerror(t("dlg.error"), str(exc), parent=self)
            return
        self.result = {"name": name, "cmd": cmd}
        self.destroy()


class SessionDialog(Dialog):
    """Create / edit one session (a name and a list of images); .result = {"name", "paths"} or None."""

    def __init__(self, parent, session=None, paths=None, taken=(), overwrite=False):
        """
        @param session    {"name", "paths"} to edit, or None for a new one
        @param paths      images of a new session (the tabs of the window)
        @param taken      names of the other sessions
        @param overwrite  True = an existing name may be replaced after asking; False = error
        """
        super().__init__(parent, "dlg.edit_session" if session else "dlg.add_session")
        self.taken, self.overwrite = {n.lower() for n in taken}, overwrite
        items = list(session["paths"] if session else paths or [])
        self.name_var = tk.StringVar(value=session["name"] if session else default_session_name(items))
        self.font = tkfont.nametofont("TkDefaultFont")
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        top = ttk.Frame(frm)
        top.pack(fill="x", pady=(0, 8))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text=t("field.name")).grid(row=0, column=0, sticky="w")
        name = ttk.Entry(top, textvariable=self.name_var, width=56)
        name.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.box = ListBox(frm, items, [("prop.path", 560, True)], self._row, self._add,
                           add_key="btn.add_images")
        self.box.pack(fill="both", expand=True)
        self.box.tree.bind("<Configure>", lambda e: self.box.refresh(self.box.selected()))
        self.box.refresh()
        ok_cancel(frm, self._ok, self.destroy).pack(fill="x", pady=(12, 0))
        name.selection_range(0, "end")
        self.show(name, self._ok)

    def _row(self, path):
        """Full path, shortened in the middle if the column is too narrow; "(missing)" if gone."""
        suffix = "" if os.path.isfile(path) else "  (%s)" % t("val.missing")
        room = self.box.tree.column("0", "width") - 16 - self.font.measure(suffix)
        return (shorten_middle(path, self.font, room) + suffix,)

    def _add(self):
        have = {os.path.normcase(p) for p in self.box.items}
        new = []
        for p in pick_images(self):
            if os.path.normcase(p) not in have:
                have.add(os.path.normcase(p))
                new.append(p)
        return new

    def _ok(self):
        name = self.name_var.get().strip()
        if not name or not self.box.items:
            messagebox.showerror(t("dlg.error"),
                                 t("msg.name_required" if not name else "msg.session_no_images"),
                                 parent=self)
            return
        if name.lower() in self.taken:
            if not self.overwrite:
                messagebox.showerror(t("dlg.error"), t("msg.name_exists"), parent=self)
                return
            if not messagebox.askyesno(t("dlg.session_overwrite"),
                                       t("msg.session_overwrite", name=name), parent=self):
                return
        self.result = {"name": name, "paths": list(self.box.items)}
        self.destroy()


class SettingsDialog(Dialog):
    """
    Settings window with the tabs General, Programs, Sessions, Appearance and About.
    OK writes everything to the config (and saves it); Cancel drops it.
    """

    def __init__(self, app, tab=0):
        """
        @param app  the Window that opened it
        @param tab  tab to show first: 0 General, 1 Programs, 2 Sessions, 3 Appearance, 4 About
        """
        super().__init__(app, "dlg.settings")
        self.app, cfg = app, app.cfg
        self.apps = [dict(a) for a in cfg["apps"]]
        self.sessions = [{"name": s["name"], "paths": list(s["paths"])} for s in cfg["sessions"]]
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        ok_cancel(frm, self._ok, self.destroy).pack(side="bottom", fill="x", pady=(10, 0))
        self.nb = ttk.Notebook(frm)
        self.nb.pack(fill="both", expand=True)
        self._tab_general(cfg)
        self._tab_programs()
        self._tab_sessions()
        self._tab_appearance(cfg)
        self._tab_about()
        self.nb.select(tab)
        self.show()

    def _page(self, key, padding=14):
        """@return a new tab page of the notebook"""
        page = ttk.Frame(self.nb, padding=padding)
        self.nb.add(page, text=t(key))
        return page

    def _tab_general(self, cfg):
        page = self._page("tab.general")
        self.flags = {k: tk.BooleanVar(value=cfg[k]) for k in
                      ("enlarge_small", "show_info", "toolbar", "single_instance", "per_user_config")}
        for key, text in (("enlarge_small", "opt.enlarge_small"), ("show_info", "opt.show_info"),
                          ("toolbar", "opt.show_toolbar"), ("single_instance", "opt.single_instance"),
                          ("per_user_config", "opt.per_user_config")):
            ttk.Checkbutton(page, text=t(text), variable=self.flags[key]).pack(anchor="w", pady=2)
        grid = ttk.Frame(page)
        grid.pack(anchor="w", pady=(12, 0))
        self.nums = {k: tk.StringVar(value=str(cfg[k])) for k in RANGES}
        for row, (key, (lo, hi)) in enumerate(RANGES.items()):
            ttk.Label(grid, text=t("field." + key)).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Spinbox(grid, textvariable=self.nums[key], from_=lo, to=hi, width=8,
                        increment=50 if key == "hold_ms" else 25).grid(row=row, column=1, padx=(12, 0))
        self.wheel = tk.StringVar(value=cfg["wheel"])
        ttk.Label(page, text=t("field.wheel")).pack(anchor="w", pady=(12, 2))
        for text, value in (("opt.wheel_none", "none"), ("opt.wheel_navigate", "navigate"),
                            ("opt.wheel_zoom", "zoom")):
            ttk.Radiobutton(page, text=t(text), value=value, variable=self.wheel).pack(anchor="w")

    def _tab_programs(self):
        page = self._page("tab.programs", 10)
        self.prog = ListBox(page, self.apps,
                            [("col.app.name", 150, False), ("col.app.cmd", 380, True)],
                            lambda a: (a["name"], a["cmd"]), self._app_add, self._app_edit,
                            "msg.remove_apps")
        self.prog.pack(fill="both", expand=True)
        self.prog.refresh()

    def _app_add(self):
        dlg = AppDialog(self, None, [a["name"] for a in self.apps])
        return [dlg.result] if dlg.result else []

    def _app_edit(self, i):
        taken = [a["name"] for j, a in enumerate(self.apps) if j != i]
        return AppDialog(self, self.apps[i], taken).result

    def _tab_sessions(self):
        page = self._page("tab.sessions", 10)
        self.sess = ListBox(page, self.sessions,
                            [("col.session.name", 380, True), ("col.session.count", 90, False)],
                            lambda s: (s["name"], len(s["paths"])), self._session_add,
                            self._session_edit, "msg.remove_sessions")
        self.sess.pack(fill="both", expand=True)
        self.sess.refresh()

    def _session_add(self):
        dlg = SessionDialog(self, None, self.app.paths(), [s["name"] for s in self.sessions])
        return [dlg.result] if dlg.result else []

    def _session_edit(self, i):
        taken = [s["name"] for j, s in enumerate(self.sessions) if j != i]
        return SessionDialog(self, self.sessions[i], taken=taken).result

    def _tab_appearance(self, cfg):
        page = self._page("tab.appearance")
        page.columnconfigure(1, weight=1)
        self.langs = list_languages()
        self.lang_var, self.theme_var = tk.StringVar(), tk.StringVar()
        ttk.Label(page, text=t("field.language")).grid(row=0, column=0, sticky="w", pady=6)
        self.lang_box = ttk.Combobox(page, textvariable=self.lang_var, state="readonly", width=30,
                                     values=[n for _c, n in self.langs])
        self.lang_box.grid(row=0, column=1, sticky="w", padx=(12, 0))
        names = dict(self.langs)
        self.lang_var.set(names.get(cfg["language"], names.get("en", cfg["language"])))
        ttk.Label(page, text=t("field.theme")).grid(row=1, column=0, sticky="w", pady=6)
        self.theme_box = ttk.Combobox(page, textvariable=self.theme_var, state="readonly", width=30)
        self.theme_box.grid(row=1, column=1, sticky="w", padx=(12, 0))
        ttk.Button(page, text=t("btn.theme_edit"), command=self._edit_theme).grid(
            row=2, column=1, sticky="w", padx=(12, 0), pady=(8, 0))
        self._show_themes(cfg["theme"])

    def _show_themes(self, code):
        """Fill the theme box and select the theme with this code."""
        self.themes = list_themes()
        self.theme_box.configure(values=[n for _c, n in self.themes])
        self.theme_var.set(dict(self.themes).get(code, code))

    def _picked(self, box, items, default):
        """@return the code chosen in a combobox, or `default` if nothing matches"""
        i = box.current()
        return items[i][0] if 0 <= i < len(items) else default

    def _edit_theme(self):
        """Open the theme editor and select the theme it saved."""
        code = self._picked(self.theme_box, self.themes, self.app.cfg["theme"])
        dlg = ThemeDialog(self, code)
        if dlg.result:
            self._show_themes(dlg.result)

    def _tab_about(self):
        import PIL
        page = self._page("tab.about")
        ttk.Label(page, text="%s %s" % (APP_NAME, VERSION),
                  font=("TkDefaultFont", 14, "bold")).pack(anchor="w")
        ttk.Label(page, text=t("about.description"), wraplength=380, justify="left").pack(
            anchor="w", pady=(4, 12))
        grid = ttk.Frame(page)
        grid.pack(anchor="w")
        rows = ((t("about.python"), sys.version.split()[0]), (t("about.pillow"), PIL.__version__),
                (t("about.tk"), str(tk.TkVersion)),
                (t("about.dnd"), t("about.dnd_on") if self.app.dnd_ok else t("about.dnd_off")),
                (t("about.config"), config_path(self.app.cfg["per_user_config"])))
        for row, (name, value) in enumerate(rows):
            ttk.Label(grid, text=name).grid(row=row, column=0, sticky="nw", pady=2)
            ttk.Label(grid, text=value, wraplength=300, justify="left").grid(
                row=row, column=1, sticky="w", padx=(12, 0), pady=2)

    def _ok(self):
        """Copy everything into the config, save, apply to all windows, close."""
        cfg = self.app.cfg
        old = (cfg["language"], cfg["theme"])
        cfg.update({k: v.get() for k, v in self.flags.items()})
        for key, (lo, hi) in RANGES.items():
            cfg[key] = clamp_int(self.nums[key].get(), lo, hi, cfg[key])
        cfg["wheel"] = self.wheel.get()
        cfg["apps"], cfg["sessions"] = self.apps, self.sessions
        cfg["language"] = resolve_language(self._picked(self.lang_box, self.langs, cfg["language"]))
        cfg["theme"] = resolve_theme(self._picked(self.theme_box, self.themes, cfg["theme"]))
        self.app.save_config()
        self.app.viewer.apply_all()
        parent = self.app
        self.destroy()
        if old != (cfg["language"], cfg["theme"]):
            messagebox.showinfo(t("dlg.restart"), t("msg.restart"), parent=parent)


# --------------------------------------------------------------------------- #
# Tab, window, program
# --------------------------------------------------------------------------- #


class Tab:
    """
    One open image: its folder list, view state and rotations. It holds no
    widgets, so a tab can move between windows. The decoded picture is only
    kept while the tab is shown (unload() frees it); the view state stays.

    View state: mode "fit" = whole image centered; mode "zoom" = scale / ox / oy,
    image pixel (0, 0) is drawn at canvas (ox, oy). Rotation is only part of the
    view: rot maps a file to its quarter turns clockwise.
    """

    def __init__(self, path):
        self.path, self.folder = path, os.path.dirname(path)
        self.files, self.index = None, -1       # folder list; scanned when first shown
        self.fmt = self.error = ""
        self.base = self.img = None             # decoded (EXIF applied) / with view rotation
        self.rot = {}
        self.mode, self.scale, self.ox, self.oy = "fit", 1.0, 0.0, 0.0
        self.fit_key = self.fit_photo = None

    def label(self):
        """@return the text of the tab: the file name, shortened"""
        name = os.path.basename(self.path)
        return name if len(name) <= 24 else name[:21] + "..."

    def ensure(self, cfg):
        """Scan the folder and decode the picture if that has not happened yet."""
        if self.files is None:
            self.files = scan_folder(self.folder)
            for i, f in enumerate(self.files):
                if same_file(f, self.path):
                    self.index = i
                    break
            else:                                # unusual extension: still show it
                self.files.append(self.path)
                self.index = len(self.files) - 1
        if self.base is None and not self.error:
            self.load(cfg)

    def load(self, cfg):
        """Decode self.path. A failure is kept in self.error and shown instead of the picture."""
        self.base = self.img = None
        self.error = self.fmt = ""
        try:
            with Image.open(self.path) as im:
                self.fmt = im.format or ""
                im.load()
                im = ImageOps.exif_transpose(im)
                if im.mode not in ("RGB", "RGBA"):
                    alpha = im.mode in ("LA", "PA") or "transparency" in im.info
                    im = im.convert("RGBA" if alpha else "RGB")
                self.base = im
            cfg["last_path"] = os.path.abspath(self.path)
        except Exception as exc:                 # Pillow raises many different types
            self.error = t("msg.cannot_open", name=os.path.basename(self.path), error=exc)
        self.rebuild(cfg)

    def unload(self):
        """Free the decoded picture (it is decoded again when the tab is shown)."""
        self.base = self.img = self.fit_photo = self.fit_key = None

    def go(self, cfg, step=None, index=None):
        """Show another image of the folder: `step` from here (wraps), or the list position `index`."""
        if self.files:
            self.index = (self.index + step if index is None else index) % len(self.files)
            self.path = self.files[self.index]
            self.mode = "fit"
            self.load(cfg)

    def quarter(self, cfg):
        """@return quarter turns clockwise shown now: auto rotation + the manual one"""
        auto = 0
        if self.base is not None and cfg["auto_rotate"]:
            w, h = self.base.size
            if (cfg["auto_rotate"] == "landscape" and h > w) or (cfg["auto_rotate"] == "portrait" and w > h):
                auto = 3 if cfg["auto_dir"] == "ccw" else 1
        return (auto + self.rot.get(self.path, 0)) % 4

    def rebuild(self, cfg):
        """Make self.img from self.base and the rotation."""
        q = self.quarter(cfg)
        self.img = None if self.base is None else (self.base.transpose(ROTATE_CW[q]) if q else self.base)
        self.fit_key = None


class Window(tk.Toplevel):
    """
    One viewer window: a tab bar (two or more tabs), a canvas that draws the
    current tab, the on-screen buttons, the context menu and the controls.
    """

    TB_NEAR = 20
    """Pixels around the button bar where the pointer still counts as "near"."""

    def __init__(self, viewer, tabs=(), geometry=None):
        """
        @param viewer    the Viewer that owns the Tk root and the config
        @param tabs      Tab objects to show (the first one is selected)
        @param geometry  "WxH+X+Y" of the window; default = the saved one
        """
        super().__init__(viewer.root)
        self.withdraw()
        self.viewer, self.cfg = viewer, viewer.cfg
        self.title(t("app.title", version=VERSION))
        self.minsize(360, 260)
        try:
            self.geometry(geometry or self.cfg["geometry"])
        except tk.TclError:
            self.geometry(DEFAULTS["geometry"])
        self.tabs, self.tab = [], None
        self.fullscreen = self._closing = False
        self._photo = self._press = self._hold_job = self._render_job = self._toast = None
        self._cursor = None
        self.var_info, self.var_enlarge = tk.BooleanVar(), tk.BooleanVar()
        self.var_auto, self.var_auto_mode, self.var_auto_dir = tk.StringVar(), tk.StringVar(), tk.StringVar()

        self.nb = ttk.Notebook(self, height=1, takefocus=False)    # tab bar only, no pages
        self.nb.bind("<<NotebookTabChanged>>", self._tab_changed)
        self.nb.bind("<Button-2>", self._tab_middle_click)
        self.cv = tk.Canvas(self, highlightthickness=0, bd=0, takefocus=1)
        self.cv.pack(fill="both", expand=True)
        self._build_toolbar()
        self._init_dnd()
        self._bind_all()
        self.apply_settings()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.deiconify()
        if geometry is None and self.cfg["zoomed"]:
            self.after(60, lambda: self._set_zoomed(True))
        self.cv.focus_set()
        for tb in tabs:
            self.add_tab(tb)
        if tabs:
            self.show_tab(tabs[0])

    # -- settings ----------------------------------------------------------- #

    def save_config(self):
        save_config(self.cfg, self)

    def apply_settings(self):
        """Take the values of the config into this window and redraw."""
        c = self.cfg
        self.var_info.set(c["show_info"])
        self.var_enlarge.set(c["enlarge_small"])
        self.col_bg = resolve_color(self, "viewer", "canvas_bg") or "#000000"
        self.col_info = resolve_color(self, "viewer", "info_text") or "#e6e6e6"
        self.col_err = resolve_color(self, "viewer", "error_text") or "#ff6b6b"
        self.cv.configure(background=self.col_bg)
        self._auto_text()
        if not c["toolbar"]:
            self.tb.place_forget()
        self.render()

    def open_settings(self, tab=0):
        """Open the settings window (0 General, 1 Programs, 2 Sessions, 3 Appearance, 4 About)."""
        SettingsDialog(self, tab)
        self.cv.focus_set()

    # -- widgets ------------------------------------------------------------ #

    def _build_toolbar(self):
        """The floating on-screen buttons (shown while the mouse is near them)."""
        self.tb = ttk.Frame(self, padding=4, relief="solid", borderwidth=1)
        items = (("◀", "tip.prev", lambda: self.go(-1)), ("▶", "tip.next", lambda: self.go(1)),
                 ("↺", "tip.rotate_left", lambda: self.rotate(-1)),
                 ("↻", "tip.rotate_right", lambda: self.rotate(1)),
                 (t("btn.fit"), "tip.fit", self.toggle_fit),
                 ("50%", "tip.zoom50", lambda: self.zoom_to(50)),
                 ("75%", "tip.zoom75", lambda: self.zoom_to(75)),
                 (t("btn.actual"), "tip.actual", lambda: self.zoom_to(100)),
                 (None, "tip.auto", self.cycle_auto_rotate),
                 ("⧉", "tip.new_window", self.new_window),
                 ("⚙", "tip.settings", self.open_settings))
        for text, tip, cmd in items:
            if text is None:
                btn = ttk.Button(self.tb, textvariable=self.var_auto, width=19, command=cmd, takefocus=False)
            else:
                btn = ttk.Button(self.tb, text=text, width=max(3, len(text) + 1), command=cmd,
                                 takefocus=False)
            btn.pack(side="left", padx=2)
            Tip(btn, tip)

    def _bind_all(self):
        """Mouse and key bindings."""
        cv = self.cv
        cv.bind("<ButtonPress-1>", self.on_press)
        cv.bind("<B1-Motion>", self.on_drag)
        cv.bind("<ButtonRelease-1>", self.on_release)
        cv.bind("<Configure>", self.on_resize)
        # bound on the window so motion over the canvas AND over the buttons is seen
        self.bind("<Motion>", self.poke_toolbar, add="+")
        self.bind("<Leave>", self.poke_toolbar, add="+")
        self.bind("<FocusIn>", lambda e: setattr(self.viewer, "active", self))
        for seq in (("<Button-2>", "<Control-Button-1>") if IS_MAC else ("<Button-3>",)):
            cv.bind(seq, self.show_context)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            cv.bind(seq, self.on_wheel)
        self.bind("<Key>", self.on_key)
        if IS_MAC:
            self.bind("<Command-w>", self.close_tab)

    def _init_dnd(self):
        """Accept files dropped on the window (needs the optional tkinterdnd2; off without it)."""
        self.dnd_ok = False
        if tkinterdnd2 is None:
            return
        try:
            tkinterdnd2.TkinterDnD._require(self)
            cmd = self.register(self._on_drop)
            for w in (self, self.cv):
                self.tk.call("tkdnd::drop_target", "register", w._w, "DND_Files")
                self.tk.call("bind", w._w, "<<Drop>>", cmd + " %D")
            self.dnd_ok = True
        except (tk.TclError, RuntimeError, OSError):
            pass

    def _on_drop(self, data):
        """Drop handler: open the dropped image files and folders as tabs."""
        paths = [p for p in self.tk.splitlist(data)
                 if os.path.isdir(p) or os.path.splitext(p)[1].lower() in IMAGE_EXTS]
        self.after(0, lambda: self.open_paths(paths))
        return "copy"

    def raise_window(self):
        """Restore the window if minimized and bring it to the front."""
        if self.state() == "iconic":
            self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(100, lambda: self.attributes("-topmost", False))
        self.focus_force()

    # -- tabs --------------------------------------------------------------- #

    def find_tab(self, path):
        """@return the tab that shows this file, or None"""
        return next((tb for tb in self.tabs if same_file(tb.path, path)), None)

    def paths(self):
        """@return the files of the tabs of this window (existing files only)"""
        return [os.path.abspath(tb.path) for tb in self.tabs if os.path.isfile(tb.path)]

    def add_tab(self, tb):
        """Append a tab (not selected)."""
        self.tabs.append(tb)
        self.nb.add(ttk.Frame(self.nb), text=tb.label())
        self._sync_bar()

    def _sync_bar(self):
        """The tab bar is only visible with two or more tabs."""
        if len(self.tabs) > 1:
            self.nb.pack(side="top", fill="x", before=self.cv)
        else:
            self.nb.pack_forget()

    def show_tab(self, tb):
        """Select a tab."""
        self.nb.select(self.tabs.index(tb))
        self._activate(tb)

    def _activate(self, tb):
        """Make `tb` the shown tab: free the previous picture, load this one, redraw."""
        if self.tab is not None and self.tab is not tb:
            self.tab.unload()
        self.tab = tb
        tb.ensure(self.cfg)
        self._title()
        self.render()

    def _tab_changed(self, _e=None):
        """The user clicked a tab."""
        i = self.nb.index("current") if self.tabs else -1
        if 0 <= i < len(self.tabs) and self.tabs[i] is not self.tab:
            self._activate(self.tabs[i])
        self.cv.focus_set()

    def _tab_middle_click(self, e):
        """Middle click on a tab closes it."""
        try:
            i = self.nb.index("@%d,%d" % (e.x, e.y))
        except tk.TclError:
            return
        self.close_tab(self.tabs[i])

    def cycle_tab(self, step):
        """Select the next (+1) / previous (-1) tab."""
        if len(self.tabs) > 1:
            self.show_tab(self.tabs[(self.tabs.index(self.tab) + step) % len(self.tabs)])

    def _remove_tab(self, tb):
        """Take a tab out of this window (the window stays, even without tabs)."""
        i, was_shown = self.tabs.index(tb), tb is self.tab
        tb.unload()
        self.tabs.pop(i)
        self.nb.forget(i)
        self._sync_bar()
        if not self.tabs:
            self.tab = None
        elif was_shown:
            self.show_tab(self.tabs[min(i, len(self.tabs) - 1)])

    def close_tab(self, tb=None):
        """Ctrl+W: close a tab (default: the shown one); the last tab closes the window."""
        tb = tb if isinstance(tb, Tab) else self.tab
        if tb is None or len(self.tabs) <= 1:
            self.close()
        else:
            self._remove_tab(tb)
        return "break"

    def new_window(self):
        """Ctrl+N: move the shown tab into a new window; with a single tab: open an empty window."""
        tb = self.tab if len(self.tabs) > 1 else None
        if tb:
            self._remove_tab(tb)
        self.viewer.new_window([tb] if tb else [], cascade(self.winfo_geometry()))

    def _title(self):
        """Window title and tab text of the shown tab."""
        tb = self.tab
        if tb is None:
            self.title(t("app.title", version=VERSION))
            return
        self.title(t("app.title_file", name=os.path.basename(tb.path), index=tb.index + 1,
                     count=len(tb.files)))
        self.nb.tab(self.tabs.index(tb), text=tb.label())

    # -- opening files ------------------------------------------------------ #

    def open_path(self, path, select=True):
        """
        Open an image as a tab (an image that is already open just gets its tab).
        A folder opens its first image.

        @param path    image file or folder
        @param select  show the tab
        @return        the Tab, or None if a folder has no images
        """
        path = os.path.abspath(path)
        if os.path.isdir(path):
            files = scan_folder(path)
            if not files:
                messagebox.showinfo(APP_NAME, t("msg.no_folder_images"), parent=self)
                return None
            path = files[0]
        tb = self.find_tab(path)
        if tb is None:
            tb = Tab(path)
            self.add_tab(tb)
        if select:
            self.show_tab(tb)
        return tb

    def open_paths(self, paths):
        """Open several images / folders as tabs and show the first."""
        first = None
        for p in paths:
            tb = self.open_path(p, select=False)
            first = first or tb
        if first:
            self.show_tab(first)

    def ask_open(self):
        """Ctrl+O: pick images with the system dialog."""
        self.open_paths(pick_images(self, self.tab.folder if self.tab else None))

    # -- navigation / rotation / zoom commands ------------------------------ #

    def _shown(self):
        """@return the tab if it has a picture to show, else None"""
        return self.tab if self.tab is not None and self.tab.img is not None else None

    def go(self, step=None, index=None):
        """Previous (-1) / next (+1) image of the folder, or the list position `index`."""
        if self.tab:
            self.tab.go(self.cfg, step, index)
            self._title()
            self.render()

    def rotate(self, quarter):
        """
        Rotate the VIEW of the shown image (the file is not changed).

        @param quarter  +1 = clockwise, -1 = counter clockwise, 0 = reset
        """
        tb = self._shown()
        if not tb:
            return
        new = 0 if quarter == 0 else (tb.rot.get(tb.path, 0) + quarter) % 4
        if new:
            tb.rot[tb.path] = new
        else:
            tb.rot.pop(tb.path, None)
        tb.mode = "fit"
        tb.rebuild(self.cfg)
        self.render()

    def _auto_text(self):
        """Button text and menu variables of the auto rotate mode."""
        mode = self.cfg["auto_rotate"]
        arrow = "" if not mode else (" \u21ba" if self.cfg["auto_dir"] == "ccw" else " \u21bb")
        self.var_auto.set(t("btn.auto_" + (mode or "off")) + arrow)
        self.var_auto_mode.set(mode)
        self.var_auto_dir.set(self.cfg["auto_dir"])

    def set_auto(self, mode=None, direction=None):
        """
        Set the auto rotate mode and / or direction and redraw.

        @param mode       "" = off, "landscape" or "portrait"
        @param direction  "cw" or "ccw"
        """
        if mode is not None:
            self.cfg["auto_rotate"] = mode
        if direction:
            self.cfg["auto_dir"] = direction
        self._auto_text()
        if self.tab:
            self.tab.mode = "fit"
            self.tab.rebuild(self.cfg)
        self.render()

    def cycle_auto_rotate(self):
        """Toolbar button: off -> landscape -> portrait -> off."""
        order = ("", "landscape", "portrait")
        self.set_auto(order[(order.index(self.cfg["auto_rotate"]) + 1) % 3])

    def view_params(self):
        """
        @return  (scale, ox, oy) of what is drawn now; in zoom mode the offset
                 is first limited so the image never leaves a gap
        """
        tb = self.tab
        cw, ch = self.cv.winfo_width(), self.cv.winfo_height()
        iw, ih = tb.img.size
        if tb.mode == "fit":
            s = min(cw / iw, ch / ih)
            if not self.cfg["enlarge_small"]:
                s = min(s, 1.0)
            return s, (cw - iw * s) / 2, (ch - ih * s) / 2
        s = tb.scale
        offs = []
        for size, room, off in ((iw * s, cw, tb.ox), (ih * s, ch, tb.oy)):
            offs.append((room - size) / 2 if size <= room else min(0.0, max(room - size, off)))
        tb.ox, tb.oy = offs
        return s, tb.ox, tb.oy

    def set_fit(self):
        """Back to "fit to window"."""
        tb = self._shown()
        if tb:
            tb.mode = "fit"
            self.render()

    def toggle_fit(self):
        """Fit width <-> fit height. From any other view: the side "fit to window" does not use."""
        tb = self._shown()
        if not tb:
            return
        cw, ch = self.cv.winfo_width(), self.cv.winfo_height()
        iw, ih = tb.img.size
        sw, sh = cw / iw, ch / ih
        if tb.mode == "zoom" and abs(tb.scale - sw) < 1e-4:
            axis = "h"
        elif tb.mode == "zoom" and abs(tb.scale - sh) < 1e-4:
            axis = "w"
        else:
            axis = "h" if sw < sh else "w"
        s = min(max(sw if axis == "w" else sh, MIN_SCALE), MAX_SCALE)
        tb.mode, tb.scale = "zoom", s
        tb.ox, tb.oy = (cw - iw * s) / 2, 0.0
        self.render()

    def zoom_to(self, percent, cx=None, cy=None):
        """
        Zoom to a fixed scale, keeping the image point under (cx, cy) in place.

        @param percent  100 = actual size
        @param cx, cy   canvas point to zoom around (default: window center)
        """
        tb = self._shown()
        if not tb:
            return
        cw, ch = self.cv.winfo_width(), self.cv.winfo_height()
        cx = cw / 2 if cx is None else cx
        cy = ch / 2 if cy is None else cy
        s0, ox0, oy0 = self.view_params()
        iw, ih = tb.img.size
        px = min(max((cx - ox0) / s0, 0), iw)          # image point under the cursor
        py = min(max((cy - oy0) / s0, 0), ih)
        s = min(max(percent / 100.0, MIN_SCALE), MAX_SCALE)
        tb.mode, tb.scale = "zoom", s
        tb.ox, tb.oy = cx - px * s, cy - py * s
        self.render()

    def zoom_step(self, factor, cx=None, cy=None):
        """Zoom in / out by a factor around a point."""
        if self._shown():
            self.zoom_to(self.view_params()[0] * factor * 100, cx, cy)

    def toggle_fullscreen(self, force=None):
        """Switch full screen on / off (force = True / False sets it)."""
        self.fullscreen = (not self.fullscreen) if force is None else force
        self.attributes("-fullscreen", self.fullscreen)

    def _is_zoomed(self):
        """@return True if the window is maximized"""
        try:
            if IS_WIN or IS_MAC:
                return self.state() == "zoomed"
            return bool(self.attributes("-zoomed"))
        except tk.TclError:
            return False

    def _set_zoomed(self, on):
        """Maximize / restore the window."""
        try:
            if IS_WIN or IS_MAC:
                self.state("zoomed" if on else "normal")
            else:
                self.attributes("-zoomed", on)
        except tk.TclError:
            pass

    def toggle_info(self):
        """Menu check item / I key: the variable already holds the new value."""
        self.cfg["show_info"] = bool(self.var_info.get())
        self.viewer.apply_all()

    def toggle_enlarge(self):
        """Menu check item: enlarge small images to fit the window."""
        self.cfg["enlarge_small"] = bool(self.var_enlarge.get())
        self.viewer.apply_all()

    # -- mouse -------------------------------------------------------------- #

    def on_press(self, e):
        """Button down. In fit mode a timer starts: held for hold_ms = zoom to hold_zoom."""
        self.cv.focus_set()
        self._press = {"x": e.x, "y": e.y, "lx": e.x, "ly": e.y, "moved": False, "hold": False, "fit": None}
        self._cancel_hold()
        tb = self._shown()
        if tb and tb.mode == "fit":
            self._hold_job = self.after(self.cfg["hold_ms"], self._begin_hold)

    def _cancel_hold(self):
        if self._hold_job:
            self.after_cancel(self._hold_job)
            self._hold_job = None

    def _begin_hold(self):
        """The button was held long enough: zoom to hold_zoom at the press point."""
        self._hold_job = None
        p, tb = self._press, self._shown()
        if p and tb and not p["moved"] and tb.mode == "fit":
            p["hold"] = True
            p["fit"] = self.view_params()              # where the fitted picture is
            self.hold_view(p, p["x"], p["y"])

    def hold_view(self, p, x, y):
        """
        Click-and-hold view: the mouse position inside the FITTED picture picks
        the same relative position of the zoomed picture (mouse at the bottom
        left of the picture = bottom left of the original).

        @param p     the press state (p["fit"] = (scale, ox, oy) of the fitted view)
        @param x, y  mouse position on the canvas
        """
        tb = self.tab
        s0, fx, fy = p["fit"]
        iw, ih = tb.img.size
        u = min(max((x - fx) / (iw * s0), 0.0), 1.0)
        v = min(max((y - fy) / (ih * s0), 0.0), 1.0)
        cw, ch = self.cv.winfo_width(), self.cv.winfo_height()
        s = min(max(self.cfg["hold_zoom"] / 100.0, MIN_SCALE), MAX_SCALE)
        tb.mode, tb.scale = "zoom", s
        tb.ox, tb.oy = u * (cw - iw * s), v * (ch - ih * s)     # view_params centers a fitting axis
        self.render()

    def on_drag(self, e):
        """Mouse moved with the button down: drag the picture when it is zoomed."""
        p, tb = self._press, self._shown()
        if not p or not tb:
            return
        dx, dy = e.x - p["lx"], e.y - p["ly"]
        p["lx"], p["ly"] = e.x, e.y
        if p["hold"]:
            self.hold_view(p, e.x, e.y)
            return
        if not p["moved"] and (abs(e.x - p["x"]) > DRAG_PX or abs(e.y - p["y"]) > DRAG_PX):
            p["moved"] = True
            self._cancel_hold()
        if p["moved"] and tb.mode == "zoom":
            tb.ox += dx
            tb.oy += dy
            self.render()

    def on_release(self, _e):
        """Button up: after a hold -> fit; after a drag -> nothing; plain click: fit <-> click_zoom."""
        p, self._press = self._press, None
        self._cancel_hold()
        tb = self._shown()
        if not p or not tb:
            return
        if p["hold"]:
            self.set_fit()
        elif not p["moved"]:
            if tb.mode == "fit":
                self.zoom_to(self.cfg["click_zoom"], p["x"], p["y"])
            else:
                self.set_fit()

    def on_wheel(self, e):
        """Wheel: nothing (default), or previous / next image / zoom (swapped with Ctrl)."""
        if self.cfg["wheel"] == "none":
            return
        up = e.num == 4 or (e.num != 5 and e.delta > 0)
        if bool(e.state & 0x4) != (self.cfg["wheel"] == "zoom"):
            self.zoom_step(1.25 if up else 0.8, e.x, e.y)
        else:
            self.go(-1 if up else 1)

    # -- keys --------------------------------------------------------------- #

    def on_key(self, e):
        """Keyboard shortcuts (see the module docstring)."""
        key = e.keysym
        if e.state & 0x4 or (IS_MAC and e.state & 0x8):
            low = key.lower()                # the cyrillic names keep the shortcuts working on a Russian layout
            if low in ("w", "cyrillic_tse"):
                self.close_tab()
            elif low in ("q", "cyrillic_shorti"):
                self.close()
            elif low in ("o", "cyrillic_shcha"):
                self.ask_open()
            elif low in ("n", "cyrillic_te"):
                self.new_window()
            elif key == "comma":
                self.open_settings()
            elif key in ("Tab", "ISO_Left_Tab"):
                self.cycle_tab(-1 if key == "ISO_Left_Tab" or e.state & 0x1 else 1)
            return
        steps = {"Left": -1, "Prior": -1, "BackSpace": -1, "Right": 1, "Next": 1, "space": 1}
        zooms = {"1": 100, "2": 200}
        if key in steps:
            self.go(steps[key])
        elif key == "Home":
            self.go(index=0)
        elif key == "End":
            self.go(index=-1)
        elif key == "F5":
            self.quick_save()
        elif key == "F11":
            self.toggle_fullscreen()
        elif key == "Escape" and self.fullscreen:
            self.toggle_fullscreen(False)
        elif key == "r":
            self.rotate(1)
        elif key in ("R", "l", "L"):
            self.rotate(-1)
        elif key in ("f", "F"):
            self.toggle_fit()
        elif key == "0":
            self.set_fit()
        elif key in zooms:
            self.zoom_to(zooms[key])
        elif key in ("plus", "equal", "KP_Add"):
            self.zoom_step(1.25)
        elif key in ("minus", "KP_Subtract"):
            self.zoom_step(0.8)
        elif key in ("i", "I"):
            self.var_info.set(not self.var_info.get())
            self.toggle_info()

    # -- toolbar ------------------------------------------------------------ #

    def _pointer_near_toolbar(self):
        """@return True if the pointer is inside the window, on or near the bar's place (bottom centre)"""
        try:
            px, py = self.winfo_pointerxy()
        except tk.TclError:
            return False
        x, y = px - self.winfo_rootx(), py - self.winfo_rooty()
        w, h = self.winfo_width(), self.winfo_height()
        if not (0 <= x < w and 0 <= y < h):
            return False
        bw, bh = self.tb.winfo_reqwidth(), self.tb.winfo_reqheight()
        near = self.TB_NEAR
        return (w - bw) / 2 - near <= x <= (w + bw) / 2 + near and y >= h - 18 - bh - near

    def poke_toolbar(self, _e=None):
        """Show the button bar while the pointer is near it, hide it at once otherwise."""
        if self.cfg["toolbar"] and self._pointer_near_toolbar():
            if not self.tb.winfo_ismapped():
                self.tb.place(relx=0.5, rely=1.0, anchor="s", y=-18)
            self.tb.lift()
        elif self.tb.winfo_ismapped():
            self.tb.place_forget()

    # -- drawing ------------------------------------------------------------ #

    def on_resize(self, _e):
        """Canvas size changed: redraw after a short pause."""
        if self._render_job:
            self.after_cancel(self._render_job)
        self._render_job = self.after(40, self.render)

    def render(self):
        """Draw the shown tab: picture, info text or message."""
        self._render_job = None
        cv, tb = self.cv, self.tab
        cv.delete("all")
        self._photo = None
        cw, ch = cv.winfo_width(), cv.winfo_height()
        if cw < 4 or ch < 4:
            return
        if tb is None or tb.img is None:
            self._set_cursor("")
            err = tb.error if tb else ""
            cv.create_text(cw // 2, ch // 2, text=err or t("msg.no_image"), width=max(cw - 60, 100),
                           justify="center", fill=self.col_err if err else self.col_info)
            return
        iw, ih = tb.img.size
        s, ox, oy = self.view_params()
        x0, y0 = max(0, int(math.floor(-ox / s))), max(0, int(math.floor(-oy / s)))
        x1, y1 = min(iw, int(math.ceil((cw - ox) / s))), min(ih, int(math.ceil((ch - oy) / s)))
        if x1 > x0 and y1 > y0:
            key = (tb.path, tb.quarter(self.cfg), tb.mode, round(s, 5), cw, ch)
            if tb.mode == "fit" and tb.fit_key == key:
                photo = tb.fit_photo
            else:
                crop = tb.img.crop((x0, y0, x1, y1))
                dw, dh = max(1, int(round((x1 - x0) * s))), max(1, int(round((y1 - y0) * s)))
                if s < 1:
                    factor = int(1 / s)
                    if factor >= 2:                   # cheap pre-shrink, then a good resize
                        crop = crop.reduce(factor)
                    crop = crop.resize((dw, dh), _RS.LANCZOS)
                elif s > 1:
                    crop = crop.resize((dw, dh), _RS.BICUBIC)
                photo = ImageTk.PhotoImage(crop)
                if tb.mode == "fit":
                    tb.fit_key, tb.fit_photo = key, photo
            self._photo = photo
            cv.create_image(int(round(ox + x0 * s)), int(round(oy + y0 * s)), anchor="nw", image=photo)
        self._set_cursor("fleur" if tb.mode == "zoom" else "")
        if self.cfg["show_info"]:
            text = "%s   %d/%d   %d\u00d7%d   %d%%" % (os.path.basename(tb.path), tb.index + 1,
                                                       len(tb.files), iw, ih, round(s * 100))
            cv.create_text(11, 9, text=text, anchor="nw", fill=self.col_bg)
            cv.create_text(10, 8, text=text, anchor="nw", fill=self.col_info)

    def _set_cursor(self, cursor):
        """Change the canvas cursor only when it differs."""
        if cursor != self._cursor:
            self._cursor = cursor
            self.cv.configure(cursor=cursor)

    # -- sessions (the tabs of this window only) ---------------------------- #

    def toast(self, text, ms=1800):
        """Show a short message at the bottom of the picture."""
        if self._toast is not None:
            self._toast.destroy()
        lbl = tk.Label(self, text=text, relief="solid", bd=1, padx=12, pady=5)
        lbl.place(relx=0.5, rely=0.92, anchor="center")
        lbl.lift()
        self._toast = lbl
        lbl.after(ms, lambda: lbl.destroy() if self._toast is lbl else None)

    def quick_save(self):
        """F5: save the tabs of this window as the session "quicksave"."""
        paths = self.paths()
        if paths:
            self.cfg["sessions"] = upsert_session(self.cfg["sessions"], QUICK_NAME, paths)
            self.save_config()
            self.toast(t("msg.quick_saved", count=len(paths)))

    def save_session(self):
        """Context menu: save the tabs of this window as a named session (the dialog lets you edit it)."""
        names = [s["name"] for s in self.cfg["sessions"]]
        res = SessionDialog(self, None, self.paths(), names, overwrite=True).result
        self.cv.focus_set()
        if res:
            self.cfg["sessions"] = upsert_session(self.cfg["sessions"], res["name"], res["paths"])
            self.save_config()
            self.toast(t("msg.session_saved", name=res["name"]))

    def open_session(self, paths):
        """Open the images of a session as tabs of this window; missing files are skipped and counted."""
        found = [p for p in paths if os.path.isfile(p)]
        self.open_paths(found)
        if len(found) < len(paths):
            messagebox.showinfo(APP_NAME, t("msg.session_missing", count=len(paths) - len(found)),
                                parent=self)

    # -- context menu ------------------------------------------------------- #

    def show_context(self, e):
        """Right click: build and show the context menu."""
        self.poke_toolbar()
        menu = self.build_context_menu()
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def build_context_menu(self):
        """@return the context menu (open with, location, copy, rotate, zoom, session, windows)"""
        tb = self.tab
        path = os.path.abspath(tb.path) if tb else ""
        have = "normal" if path and os.path.exists(path) else "disabled"
        shown = "normal" if self._shown() else "disabled"
        many = "normal" if tb and tb.files and len(tb.files) > 1 else "disabled"

        def item(menu, key, cmd, state="normal", accel=""):
            menu.add_command(label=t(key), command=cmd, state=state, accelerator=accel)

        m = tk.Menu(self, tearoff=0)
        sub = tk.Menu(m, tearoff=0)
        for a in self.cfg["apps"]:
            sub.add_command(label=a["name"], state=have, command=lambda a=a: self.run_app(a))
        if not self.cfg["apps"]:
            sub.add_command(label=t("menu.no_programs"), state="disabled")
        sub.add_separator()
        item(sub, "menu.manage_programs", lambda: self.open_settings(1))
        m.add_cascade(label=t("menu.open_with"), menu=sub, state=have)
        item(m, "menu.open_location", lambda: self._safely(reveal_in_folder, path), have)
        m.add_separator()
        for key, fn in (("menu.copy_path", lambda p: p), ("menu.copy_name", os.path.basename),
                        ("menu.copy_folder", os.path.dirname)):
            item(m, key, lambda fn=fn: self.copy_text(fn(path)), have)
        item(m, "menu.properties", self.show_properties, have)
        m.add_separator()
        for key, arg, accel in (("menu.rotate_right", 1, "R"), ("menu.rotate_left", -1, "L"),
                                ("menu.rotate_reset", 0, "")):
            item(m, key, lambda arg=arg: self.rotate(arg), shown, accel)
        m.add_separator()
        item(m, "menu.fit_toggle", self.toggle_fit, shown, "F")
        item(m, "menu.fit", self.set_fit, shown, "0")
        item(m, "menu.actual", lambda: self.zoom_to(100), shown, "1")
        item(m, "menu.zoom200", lambda: self.zoom_to(200), shown, "2")
        auto = tk.Menu(m, tearoff=0)
        for val, key in (("", "off"), ("landscape", "landscape"), ("portrait", "portrait")):
            auto.add_radiobutton(label=t("menu.auto_" + key), variable=self.var_auto_mode, value=val,
                                 command=lambda v=val: self.set_auto(mode=v))
        auto.add_separator()
        for val in ("cw", "ccw"):
            auto.add_radiobutton(label=t("menu.auto_" + val), variable=self.var_auto_dir, value=val,
                                 command=lambda v=val: self.set_auto(direction=v))
        m.add_cascade(label=t("menu.auto_rotate"), menu=auto, state=shown)
        m.add_checkbutton(label=t("menu.enlarge_small"), variable=self.var_enlarge,
                          command=self.toggle_enlarge)
        m.add_checkbutton(label=t("menu.show_info"), variable=self.var_info, accelerator="I",
                          command=self.toggle_info)
        item(m, "menu.fullscreen", self.toggle_fullscreen, accel="F11")
        m.add_separator()
        item(m, "menu.prev", lambda: self.go(-1), many, "\u2190")
        item(m, "menu.next", lambda: self.go(1), many, "\u2192")
        m.add_separator()
        ses = tk.Menu(m, tearoff=0)
        item(ses, "menu.quick_save", self.quick_save, accel="F5")
        ses.add_separator()
        item(ses, "menu.session_save", self.save_session)
        opn = tk.Menu(ses, tearoff=0)
        for s in self.cfg["sessions"]:
            opn.add_command(label="%s (%d)" % (s["name"], len(s["paths"])),
                            command=lambda s=s: self.open_session(s["paths"]))
        if not self.cfg["sessions"]:
            opn.add_command(label=t("menu.no_sessions"), state="disabled")
        ses.add_cascade(label=t("menu.session_open"), menu=opn)
        item(ses, "menu.session_manage", lambda: self.open_settings(2))
        m.add_cascade(label=t("menu.session"), menu=ses)
        m.add_separator()
        item(m, "menu.new_window", self.new_window, accel="Ctrl+N")
        item(m, "menu.open_image", self.ask_open, accel="Ctrl+O")
        item(m, "menu.settings", self.open_settings, accel="Ctrl+,")
        item(m, "menu.close", self.close_tab, accel="Ctrl+W")
        return m

    def copy_text(self, text):
        """Put text on the clipboard."""
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()

    def _safely(self, func, arg):
        """Call func(arg); show a message box if it raises OSError."""
        try:
            func(arg)
        except OSError as exc:
            messagebox.showerror(t("dlg.error"), t("msg.location_failed", error=exc), parent=self)

    def run_app(self, app):
        """
        Open the shown image with an "Open with" program.

        @param app  {"name", "cmd"} entry of the Programs list
        """
        if not self.tab:
            return
        try:
            argv = build_argv(app["cmd"], os.path.abspath(self.tab.path))
            kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                      "stderr": subprocess.DEVNULL}
            if IS_WIN:
                kwargs["creationflags"] = 0x08000000          # CREATE_NO_WINDOW
            else:
                kwargs["start_new_session"] = True
            subprocess.Popen(argv, **kwargs)
        except (OSError, ValueError) as exc:
            messagebox.showerror(t("dlg.error"), t("msg.launch_failed", error=exc), parent=self)

    def show_properties(self):
        """Show file facts: name, folder, size, dimensions, format, date, rotation."""
        tb = self.tab
        path = os.path.abspath(tb.path)
        try:
            st = os.stat(path)
            size = "%s (%d B)" % (size_text(st.st_size), st.st_size)
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
        except OSError:
            size = when = "?"
        dims = "%d \u00d7 %d" % tb.base.size if tb.base is not None else "?"
        lines = [(t("prop.name"), os.path.basename(path)), (t("prop.folder"), os.path.dirname(path)),
                 (t("prop.size"), size), (t("prop.dimensions"), dims), (t("prop.format"), tb.fmt or "?"),
                 (t("prop.modified"), when), (t("prop.rotation"), "%d\u00b0" % (90 * tb.quarter(self.cfg)))]
        messagebox.showinfo(t("dlg.properties"), "\n".join("%s: %s" % kv for kv in lines), parent=self)

    # -- closing ------------------------------------------------------------ #

    def close(self, _e=None):
        """Close this window (all its tabs) and remember size / position and maximized state."""
        if self._closing:
            return "break"
        self._closing = True
        zoomed = self._is_zoomed()
        self.cfg["zoomed"] = zoomed
        if not zoomed and not self.fullscreen:
            self.cfg["geometry"] = self.winfo_geometry()
        paths = self.paths()
        if paths and len(self.viewer.windows) <= 1:      # the last window: remember its tabs
            self.cfg["sessions"] = upsert_session(self.cfg["sessions"], LAST_NAME, paths)
            if self.tab and os.path.isfile(self.tab.path):
                self.cfg["last_path"] = os.path.abspath(self.tab.path)
        self.save_config()
        self.destroy()
        self.viewer.closed(self)
        return "break"


class Viewer:
    """
    The one program process: a hidden Tk root, the config and every window. With
    a listener (see claim_instance) it also opens the files of later launches.
    The program ends with its last window.
    """

    def __init__(self, cfg, listener=None):
        """
        @param cfg       config dict (load_config()), shared by all windows
        @param listener  socket of claim_instance() that receives the files of later launches
        """
        self.cfg, self.listener = cfg, listener
        self.windows, self.active = [], None
        self.inbox = queue.Queue()
        self.closing = False
        self.root = tk.Tk()
        self.root.withdraw()
        cfg["language"] = set_language(cfg.get("language"))
        cfg["theme"] = set_theme(cfg.get("theme"))
        apply_theme(self.root)             # before any widget exists
        self.icons = load_icons(self.root)

    def run(self, path=None):
        """
        Open the first window and run until the last one is closed.

        @param path  image or folder of the command line; none = reopen the tabs of the
                     last session (the last window closed), else the last image
        """
        if path:
            self.open(path)
        else:
            self._restore_last()
        if self.listener:
            threading.Thread(target=self._serve, daemon=True).start()
            self._poll_inbox()
        self.root.mainloop()

    def _restore_last(self):
        """Open the tabs of the session "lastsession" in one window and show the tab that was shown."""
        last = self.cfg["last_path"]
        saved = next((s["paths"] for s in self.cfg["sessions"] if s["name"].lower() == LAST_NAME.lower()), [])
        found = [p for p in saved if os.path.isfile(p)]
        if not found:
            self.open(last if os.path.isfile(last) else None)
            return
        win = self.new_window()
        win.open_paths(found)
        tb = win.find_tab(last) if last else None
        if tb:
            win.show_tab(tb)

    def new_window(self, tabs=(), geometry=None):
        """@return a new Window showing the given tabs"""
        win = Window(self, tabs, geometry)
        self.windows.append(win)
        self.active = win
        return win

    def open(self, path=None):
        """
        Open an image (or folder) as a tab of the window used last; an image
        that is already open in any window just gets its tab selected. No path:
        raise that window.

        @param path  image file or folder, or empty
        """
        if path:
            path = os.path.abspath(path)
            for w in self.windows:
                tb = w.find_tab(path)
                if tb:
                    w.show_tab(tb)
                    w.raise_window()
                    return
        win = self.active or (self.windows[-1] if self.windows else None)
        fresh = win is None
        if fresh:
            win = self.new_window()
        if path:
            win.open_path(path)
        if not fresh:
            win.raise_window()

    def apply_all(self):
        """Apply the config (settings, info, enlarge) to every window."""
        for w in self.windows:
            w.apply_settings()

    def closed(self, win):
        """A window was closed; the program ends with the last one."""
        if win in self.windows:
            self.windows.remove(win)
        if self.active is win:
            self.active = None
        if self.windows:
            return
        self.closing = True
        if self.listener:
            self.listener.close()
        self.root.destroy()

    def _serve(self):
        """Thread: receive the paths sent by later launches and queue them for the Tk thread."""
        while not self.closing:
            try:
                conn = self.listener.accept()
            except AuthenticationError:
                continue
            except (OSError, EOFError):
                return
            try:
                with conn:
                    path = conn.recv()
            except (OSError, EOFError):
                continue
            if isinstance(path, str):
                self.inbox.put(path)

    def _poll_inbox(self):
        """Tk thread: open the paths that later launches sent."""
        while True:
            try:
                self.open(self.inbox.get_nowait())
            except queue.Empty:
                break
        if not self.closing:
            self.root.after(150, self._poll_inbox)


def main(argv):
    """
    Start the viewer.

    @param argv  command line without the program name; an optional image or folder
    """
    detach_console()
    path = argv[0] if argv else None
    cfg = load_config()
    listener = None
    if cfg["single_instance"]:
        forwarded, listener = claim_instance(path)
        if forwarded:
            return
    win_setup()
    Viewer(cfg, listener).run(path)


if __name__ == "__main__":
    main(sys.argv[1:])
