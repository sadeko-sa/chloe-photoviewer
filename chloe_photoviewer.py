#!/usr/bin/env python3
"""
chloe_photoviewer.py - Chloe Photoviewer, a small cross platform image viewer
(Windows, Linux, macOS). Needs Python 3.8+, Tkinter and Pillow.

    python chloe_photoviewer.py [image or folder]

Mouse (FastStone style):
    click                    zoom to 100% at the clicked point; drag to move around
    click again              back to "fit to window"
    click and hold           zoom to 200% while the button is down; moving the mouse
                             over the fitted picture moves the zoomed view the same
                             way (mouse at the bottom left of the picture = bottom
                             left of the original); release returns to "fit"
    wheel                    does nothing by default; Settings: previous / next image
                             (Ctrl + wheel = zoom) or zoom (Ctrl + wheel = previous / next)
    right click              context menu (open with, open file location, copy path ...)

Keys:
    Left / Right / Space / BackSpace / PageUp / PageDown   previous / next
    Home / End      first / last image          R / L   rotate right / left
    F               fit width / height          0       fit to window
    1 / 2           100% / 200%
    + / -           zoom in / out               I       show / hide info
    F11             full screen (Esc leaves it)
    Ctrl+O          open image                  Ctrl+,  settings
    Ctrl+W          close (also Ctrl+Q)

Only one program process runs (Settings > General > single instance): starting it
again sends the file to the running program, which opens it in a new window, and
exits, so all windows share one Python / Tk / Pillow memory footprint.

Rotating only changes how the picture is shown. No file is ever written or
modified by this program.

Settings (Ctrl+, or the gear button) has the tabs General, Programs (the
"Open with" list of the context menu) and Appearance (language and theme).
Language and theme work like in the Firmware Manager: lang/<code>.json and
theme/<code>.json next to this script, used from the next start (see
chloe_lang.py and chloe_theme.py).

The config is saved per user:
    Windows  %APPDATA%\\ChloePhotoviewer\\chloe_photoviewer.json
    Linux    ~/.config/chloe-photoviewer/chloe_photoviewer.json
    macOS    ~/Library/Application Support/ChloePhotoviewer/chloe_photoviewer.json
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
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client, Listener
from tkinter import ttk, messagebox, filedialog

try:
    from PIL import Image, ImageOps, ImageTk
except ImportError:
    sys.exit("Chloe Photoviewer needs Pillow:  pip install pillow\n"
             "(Debian/Ubuntu also:  sudo apt install python3-tk python3-pil.imagetk)")

try:                                   # optional: drop an image on the window to open it
    import tkinterdnd2
except ImportError:
    tkinterdnd2 = None

from chloe_lang import t, list_languages, resolve_language, set_language
from chloe_theme import (ThemeDialog, apply_theme, list_themes, resolve_color,
                         resolve_theme, set_theme)

APP_NAME = "Chloe Photoviewer"
VERSION = "1.2"

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"

IMAGE_EXTS = {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".bmp", ".dib",
              ".webp", ".tif", ".tiff", ".ico", ".ppm", ".pgm", ".pbm", ".tga",
              ".avif", ".heic", ".heif"}
"""File extensions shown. What really opens depends on the installed Pillow
(.heic / .avif need the pillow-heif plugin, see README)."""

# Pillow 9.1 moved the constants into enums; support both.
_TR = getattr(Image, "Transpose", Image)
_RS = getattr(Image, "Resampling", Image)
ROTATE_CW = {1: _TR.ROTATE_270, 2: _TR.ROTATE_180, 3: _TR.ROTATE_90}
"""Quarter turns clockwise -> Pillow transpose constant."""

MIN_SCALE, MAX_SCALE = 0.02, 32.0
DRAG_PX = 4
"""Mouse movement (pixels) that turns a click into a drag."""

CONFIG_NAME = "chloe_photoviewer.json"

ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "image", "icon.png")
"""Program icon (square PNG, transparent background, 256 px or more). Missing = Tk default."""

INSTANCE_ADDR = ("127.0.0.1", 47615)
"""Local address the running viewer listens on; later launches send their file here."""

INSTANCE_KEY = b"chloe-photoviewer"
"""Auth key of that connection (keeps other programs and other users out)."""

DEFAULTS = {
    "language": "", "theme": "", "apps": None,
    "geometry": "1100x760", "zoomed": False,
    "enlarge_small": False, "show_info": False, "toolbar": True,
    "click_zoom": 100, "hold_zoom": 200, "hold_ms": 100,
    "wheel": "none", "last_path": "", "cfg_version": 2,
    "auto_rotate": "", "auto_dir": "cw", "single_instance": True,
}
"""Config entries and their defaults ("apps": None = seed the open-with list;
"wheel": none / navigate / zoom)."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def detach_console():
    """
    Windows: when started with python.exe (which opens a console window),
    start the same program again with pythonw.exe (no console) and exit.
    Set CHLOE_KEEP_CONSOLE=1 to keep the console, e.g. to read error messages.
    Starting chloe_viewer.pyw never opens a console in the first place.
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


def enable_hidpi():
    """Windows: make the process DPI aware so the window is not blurry. Call before Tk()."""
    if not IS_WIN:
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def set_app_id():
    """Windows: own taskbar identity, so the taskbar shows our icon and not the Python one. Call before Tk()."""
    if not IS_WIN: return
    try:
        import ctypes
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


def config_dir():
    """@return the per-user folder of the config file (platform specific)"""
    if IS_WIN:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "ChloePhotoviewer")
    if IS_MAC:
        return os.path.join(os.path.expanduser("~/Library/Application Support"),
                            "ChloePhotoviewer")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "chloe-photoviewer")


def config_path():
    """@return full path of the config file"""
    return os.path.join(config_dir(), CONFIG_NAME)


def claim_instance(path):
    """
    Make this process the only viewer. The first process listens on a local
    port; any later one sends its file to it and reports that it can exit, so
    all images share one process (one Python / Tk / Pillow memory footprint).
    Binding the port is the lock: nothing to clean up after a crash.

    @param path  image or folder of the command line, or None
    @return      (forwarded, listener): forwarded True = a running viewer got the
                 path, exit now; listener = socket to serve, None if the port is
                 not available (then run as a normal stand-alone window)
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


def clamp_int(value, lo, hi, default):
    """@return int(value) limited to lo..hi, or default if it is not a number"""
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def default_apps():
    """
    Seed for the "Open with" list on the first start: well known editors that
    are actually installed.

    @return  list of {"name", "cmd"}
    """
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
    if isinstance(apps, list):
        for a in apps:
            if isinstance(a, dict) and isinstance(a.get("name"), str) \
                    and isinstance(a.get("cmd"), str) and a["name"].strip() and a["cmd"].strip():
                out.append({"name": a["name"].strip(), "cmd": a["cmd"].strip()})
    return out


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
    if IS_WIN:
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
    return shlex.split(cmd)


def build_argv(cmd, path):
    """
    Command line of an "Open with" program for one image.

    @param cmd   command with optional {file} and {dir} placeholders
    @param path  image path
    @return      argument list; the path is appended if the command has no {file}
    @raise ValueError  if the command cannot be split
    """
    parts = split_command(cmd)
    if not parts:
        raise ValueError("empty command")
    has_file = any("{file}" in p for p in parts)
    argv = [p.replace("{file}", path).replace("{dir}", os.path.dirname(path)) for p in parts]
    if not has_file:
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


def _select_in_explorer(path):
    """
    Windows: open the folder of a file in Explorer with the file selected, using
    the shell API (no command line quoting, so every file name works).

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
                ["dbus-send", "--session", "--print-reply",
                 "--dest=org.freedesktop.FileManager1", "--type=method_call",
                 "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1.ShowItems",
                 "array:string:" + Path(path).as_uri(), "string:"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
            if done.returncode == 0:
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
        subprocess.Popen(["xdg-open", os.path.dirname(path)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() - 30
        self.win.wm_geometry("+%d+%d" % (x, max(y, 0)))

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


def center_on(win, parent):
    """Place a dialog in the middle of its parent window."""
    win.update_idletasks()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - h) // 2
    win.geometry("+%d+%d" % (max(x, 0), max(y, 0)))


def make_modal(win):
    """Grab input for a dialog (when it is visible) and wait until it closes."""
    try:
        win.wait_visibility()
        win.grab_set()
    except tk.TclError:
        pass
    win.focus_set()
    win.wait_window(win)


class AppDialog(tk.Toplevel):
    """
    Add / edit one "Open with" program (name + command).
    After wait_window(), .result is {"name", "cmd"} or None if cancelled.
    """

    def __init__(self, parent, app=None, taken=()):
        """
        @param parent  owner window
        @param app     {"name", "cmd"} to edit, or None to add
        @param taken   names already used by the other programs
        """
        super().__init__(parent)
        self.withdraw()
        self.title(t("dlg.edit_app" if app else "dlg.add_app"))
        self.transient(parent)
        self.result = None
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
        ttk.Entry(frm, textvariable=self.cmd_var, width=56).grid(
            row=1, column=1, sticky="ew", padx=(8, 0))
        ttk.Button(frm, text=t("btn.pick"), command=self._pick).grid(row=1, column=2, padx=(6, 0))
        ttk.Label(frm, text=t("hint.app_cmd"), wraplength=480, justify="left").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(btns, text=t("btn.ok"), command=self._ok).pack(side="left", padx=3)
        ttk.Button(btns, text=t("btn.cancel"), command=self.destroy).pack(side="left")

        self.bind("<Return>", lambda e: self._ok())
        for seq in ("<Escape>", "<Control-w>", "<Control-W>"):
            self.bind(seq, lambda e: self.destroy())
        center_on(self, parent)
        self.deiconify()
        name.focus_set()
        make_modal(self)

    def _pick(self):
        """Choose a program file; the command becomes: "<program>" "{file}"."""
        path = filedialog.askopenfilename(parent=self, title=t("btn.pick"))
        if path:
            self.cmd_var.set('"%s" "{file}"' % path)
            if not self.name_var.get().strip():
                self.name_var.set(os.path.splitext(os.path.basename(path))[0])

    def _ok(self):
        """Validate, then close with the result."""
        name, cmd = self.name_var.get().strip(), self.cmd_var.get().strip()
        if not name:
            messagebox.showerror(t("dlg.error"), t("msg.name_required"), parent=self)
            return
        if not cmd:
            messagebox.showerror(t("dlg.error"), t("msg.cmd_required"), parent=self)
            return
        if name.lower() in self.taken:
            messagebox.showerror(t("dlg.error"), t("msg.name_exists"), parent=self)
            return
        try:
            if not split_command(cmd):
                raise ValueError(t("msg.cmd_required"))
        except ValueError as exc:
            messagebox.showerror(t("dlg.error"), t("msg.bad_command", error=exc), parent=self)
            return
        self.result = {"name": name, "cmd": cmd}
        self.destroy()


class SettingsDialog(tk.Toplevel):
    """
    Settings window with the tabs General, Programs, Appearance and About.
    OK writes everything to the app config (and saves it); Cancel drops it.
    """

    def __init__(self, app, tab=0):
        """
        @param app  the main window (App)
        @param tab  tab to show first: 0 General, 1 Programs, 2 Appearance, 3 About
        """
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.title(t("dlg.settings"))
        self.transient(app)
        cfg = app.cfg
        self.apps = [dict(a) for a in cfg["apps"]]
        self.theme_code = cfg["theme"]

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        btns = ttk.Frame(frm)
        btns.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Button(btns, text=t("btn.cancel"), command=self.destroy).pack(side="right")
        ttk.Button(btns, text=t("btn.ok"), command=self._ok).pack(side="right", padx=3)
        self.nb = ttk.Notebook(frm)
        self.nb.pack(fill="both", expand=True)
        self._tab_general(cfg)
        self._tab_programs()
        self._tab_appearance(cfg)
        self._tab_about()
        self.nb.select(tab)

        self.bind("<Escape>", lambda e: self.destroy())
        for seq in ("<Control-w>", "<Control-W>"):
            self.bind(seq, lambda e: self.destroy())
        center_on(self, app)
        self.deiconify()
        make_modal(self)

    # -- General ------------------------------------------------------------ #

    def _tab_general(self, cfg):
        """Zoom behavior, wheel, on-screen options."""
        page = ttk.Frame(self.nb, padding=14)
        self.nb.add(page, text=t("tab.general"))
        self.v_enlarge = tk.BooleanVar(value=cfg["enlarge_small"])
        self.v_info = tk.BooleanVar(value=cfg["show_info"])
        self.v_toolbar = tk.BooleanVar(value=cfg["toolbar"])
        self.v_single = tk.BooleanVar(value=cfg["single_instance"])
        self.v_click = tk.StringVar(value=str(cfg["click_zoom"]))
        self.v_hold = tk.StringVar(value=str(cfg["hold_zoom"]))
        self.v_ms = tk.StringVar(value=str(cfg["hold_ms"]))
        self.v_wheel = tk.StringVar(value=cfg["wheel"])
        for var, key in ((self.v_enlarge, "opt.enlarge_small"), (self.v_info, "opt.show_info"),
                         (self.v_toolbar, "opt.show_toolbar"),
                         (self.v_single, "opt.single_instance")):
            ttk.Checkbutton(page, text=t(key), variable=var).pack(anchor="w", pady=2)
        grid = ttk.Frame(page)
        grid.pack(anchor="w", pady=(12, 0))
        for row, (key, var, lo, hi, step) in enumerate((
                ("field.click_zoom", self.v_click, 10, 3200, 25),
                ("field.hold_zoom", self.v_hold, 10, 3200, 25),
                ("field.hold_ms", self.v_ms, 100, 2000, 50))):
            ttk.Label(grid, text=t(key)).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Spinbox(grid, textvariable=var, from_=lo, to=hi, increment=step,
                        width=8).grid(row=row, column=1, padx=(12, 0))
        ttk.Label(page, text=t("field.wheel")).pack(anchor="w", pady=(12, 2))
        ttk.Radiobutton(page, text=t("opt.wheel_none"), value="none",
                        variable=self.v_wheel).pack(anchor="w")
        ttk.Radiobutton(page, text=t("opt.wheel_navigate"), value="navigate",
                        variable=self.v_wheel).pack(anchor="w")
        ttk.Radiobutton(page, text=t("opt.wheel_zoom"), value="zoom",
                        variable=self.v_wheel).pack(anchor="w")

    # -- About -------------------------------------------------------------- #

    def _tab_about(self):
        """Program name, version and the versions it runs on."""
        import PIL
        page = ttk.Frame(self.nb, padding=14)
        self.nb.add(page, text=t("tab.about"))
        ttk.Label(page, text="%s %s" % (APP_NAME, VERSION),
                  font=("TkDefaultFont", 14, "bold")).pack(anchor="w")
        ttk.Label(page, text=t("about.description"), wraplength=380,
                  justify="left").pack(anchor="w", pady=(4, 12))
        grid = ttk.Frame(page)
        grid.pack(anchor="w")
        rows = ((t("about.python"), sys.version.split()[0]),
                (t("about.pillow"), PIL.__version__),
                (t("about.tk"), str(tk.TkVersion)),
                (t("about.dnd"), t("about.dnd_on") if self.app.dnd_ok else t("about.dnd_off")),
                (t("about.config"), config_path()))
        for row, (name, value) in enumerate(rows):
            ttk.Label(grid, text=name).grid(row=row, column=0, sticky="nw", pady=2)
            ttk.Label(grid, text=value, wraplength=300, justify="left").grid(
                row=row, column=1, sticky="w", padx=(12, 0), pady=2)

    # -- Programs ----------------------------------------------------------- #

    def _tab_programs(self):
        """The list behind Open with in the context menu."""
        page = ttk.Frame(self.nb, padding=10)
        self.nb.add(page, text=t("tab.programs"))
        side = ttk.Frame(page)
        side.pack(side="right", fill="y", padx=(10, 0))
        for key, cmd in (("btn.add", self._add), ("btn.edit", self._edit),
                         ("btn.remove", self._remove), ("btn.up", lambda: self._move(-1)),
                         ("btn.down", lambda: self._move(1))):
            ttk.Button(side, text=t(key), command=cmd).pack(fill="x", pady=2)
        box = ttk.Frame(page)
        box.pack(side="left", fill="both", expand=True)
        self.tree = ttk.Treeview(box, columns=("name", "cmd"), show="headings",
                                 height=10, selectmode="extended")
        self.tree.heading("name", text=t("col.app.name"))
        self.tree.heading("cmd", text=t("col.app.cmd"))
        self.tree.column("name", width=150, stretch=False)
        self.tree.column("cmd", width=380)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self._edit())
        self._fill_apps()

    def _fill_apps(self, select=()):
        """Show self.apps in the list; `select` = indexes to select afterwards."""
        self.tree.delete(*self.tree.get_children())
        for i, a in enumerate(self.apps):
            self.tree.insert("", "end", iid=str(i), values=(a["name"], a["cmd"]))
        for i in select:
            if 0 <= i < len(self.apps):
                self.tree.selection_add(str(i))

    def _selected(self):
        """@return the selected indexes, ascending"""
        return sorted(int(i) for i in self.tree.selection())

    def _add(self):
        """Add a program."""
        dlg = AppDialog(self, None, [a["name"] for a in self.apps])
        if dlg.result:
            self.apps.append(dlg.result)
            self._fill_apps([len(self.apps) - 1])

    def _edit(self):
        """Edit the first selected program."""
        sel = self._selected()
        if not sel:
            return
        i = sel[0]
        taken = [a["name"] for j, a in enumerate(self.apps) if j != i]
        dlg = AppDialog(self, self.apps[i], taken)
        if dlg.result:
            self.apps[i] = dlg.result
            self._fill_apps([i])

    def _remove(self):
        """Remove the selected programs (after asking)."""
        sel = self._selected()
        if sel and messagebox.askyesno(t("dlg.remove"), t("msg.remove_apps", count=len(sel)),
                                       parent=self):
            self.apps = [a for i, a in enumerate(self.apps) if i not in sel]
            self._fill_apps()

    def _move(self, step):
        """Move the selected programs up (-1) or down (+1)."""
        sel = self._selected()
        if not sel or sel[0] + step < 0 or sel[-1] + step >= len(self.apps):
            return
        order = sel if step < 0 else sel[::-1]
        for i in order:
            self.apps[i], self.apps[i + step] = self.apps[i + step], self.apps[i]
        self._fill_apps([i + step for i in sel])

    # -- Appearance --------------------------------------------------------- #

    def _tab_appearance(self, cfg):
        """Language and theme."""
        page = ttk.Frame(self.nb, padding=14)
        self.nb.add(page, text=t("tab.appearance"))
        page.columnconfigure(1, weight=1)
        self.langs = list_languages()
        self.lang_var = tk.StringVar()
        self.lang_code = cfg["language"]
        ttk.Label(page, text=t("field.language")).grid(row=0, column=0, sticky="w", pady=6)
        self.lang_box = ttk.Combobox(page, textvariable=self.lang_var, state="readonly",
                                     values=[n for _c, n in self.langs], width=30)
        self.lang_box.grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.lang_box.bind("<<ComboboxSelected>>", self._on_lang)
        self.theme_var = tk.StringVar()
        ttk.Label(page, text=t("field.theme")).grid(row=1, column=0, sticky="w", pady=6)
        self.theme_box = ttk.Combobox(page, textvariable=self.theme_var, state="readonly", width=30)
        self.theme_box.grid(row=1, column=1, sticky="w", padx=(12, 0))
        self.theme_box.bind("<<ComboboxSelected>>", self._on_theme)
        ttk.Button(page, text=t("btn.theme_edit"), command=self._edit_theme).grid(
            row=2, column=1, sticky="w", padx=(12, 0), pady=(8, 0))
        self._show_lang()
        self._show_themes()

    def _show_lang(self):
        """Show the display name of the chosen language code."""
        names = dict(self.langs)
        self.lang_var.set(names.get(self.lang_code, names.get("en", self.lang_code)))

    def _on_lang(self, _event=None):
        """Remember the language picked in the box (as its code)."""
        idx = self.lang_box.current()
        if 0 <= idx < len(self.langs):
            self.lang_code = self.langs[idx][0]

    def _show_themes(self):
        """Fill the theme box and select the chosen theme."""
        self.themes = list_themes()
        self.theme_box.configure(values=[n for _c, n in self.themes])
        names = dict(self.themes)
        self.theme_var.set(names.get(self.theme_code, self.theme_code))

    def _on_theme(self, _event=None):
        """Remember the theme picked in the box (as its code)."""
        idx = self.theme_box.current()
        if 0 <= idx < len(self.themes):
            self.theme_code = self.themes[idx][0]

    def _edit_theme(self):
        """Open the theme editor; select the theme it saved."""
        dlg = ThemeDialog(self, self.theme_code)
        if dlg.result:
            self.theme_code = dlg.result
            self._show_themes()

    # -- OK ----------------------------------------------------------------- #

    def _ok(self):
        """Copy everything into the app config, save, close."""
        cfg = self.app.cfg
        old = (cfg["language"], cfg["theme"])
        cfg["enlarge_small"] = self.v_enlarge.get()
        cfg["show_info"] = self.v_info.get()
        cfg["toolbar"] = self.v_toolbar.get()
        cfg["single_instance"] = self.v_single.get()
        cfg["click_zoom"] = clamp_int(self.v_click.get(), 10, 3200, cfg["click_zoom"])
        cfg["hold_zoom"] = clamp_int(self.v_hold.get(), 10, 3200, cfg["hold_zoom"])
        cfg["hold_ms"] = clamp_int(self.v_ms.get(), 100, 2000, cfg["hold_ms"])
        cfg["wheel"] = self.v_wheel.get() if self.v_wheel.get() in ("none", "navigate", "zoom") else "none"
        cfg["apps"] = self.apps
        cfg["language"] = resolve_language(self.lang_code)
        cfg["theme"] = resolve_theme(self.theme_code)
        self.app.save_config()
        self.app.apply_settings()
        parent = self.app
        changed = old != (cfg["language"], cfg["theme"])
        self.destroy()
        if changed:
            messagebox.showinfo(t("dlg.restart"), t("msg.restart"), parent=parent)


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #


class App(tk.Toplevel):
    """
    One viewer window: a canvas that draws the current image, an on-screen
    button bar, the context menu and the mouse / key controls.

    View state:
        mode "fit"   whole image, centered (scale from fit_scale)
        mode "zoom"  scale / ox / oy: image pixel (0, 0) is drawn at canvas (ox, oy)
    Rotation is only part of the view: self.rotations maps a file to its
    quarter turns (clockwise); the image on disk is never touched.
    """

    def __init__(self, host, start_path=None):
        """
        @param host        the Viewers object that owns the Tk root, the config and all windows
        @param start_path  image file or folder to show first (optional)
        """
        super().__init__(host.root)
        self.withdraw()
        self.host = host
        self.cfg = host.cfg                # shared by all windows
        self.title(t("app.title", version=VERSION))
        self.minsize(360, 260)
        try:
            self.geometry(self.cfg["geometry"])
        except tk.TclError:
            self.geometry(DEFAULTS["geometry"])

        self.folder = ""
        self.files = []
        self.index = -1
        self.path = ""
        self.fmt = ""
        self.base = None          # decoded image, EXIF orientation applied
        self.img = None           # base with the view rotation applied
        self.error = ""
        self.rotations = {}
        self.mode = "fit"
        self.scale, self.ox, self.oy = 1.0, 0.0, 0.0
        self.fullscreen = False
        self._photo = None
        self._fit_key = None
        self._fit_photo = None
        self._press = None
        self._hold_job = None
        self._tb_job = None
        self._render_job = None
        self._cursor = None
        self._closing = False

        if not start_path and self.cfg["last_path"] and os.path.isfile(self.cfg["last_path"]):
            start_path = self.cfg["last_path"]     # started without a file: reopen the last one
        self.var_info = tk.BooleanVar(value=self.cfg["show_info"])
        self.var_enlarge = tk.BooleanVar(value=self.cfg["enlarge_small"])
        self.var_auto_mode = tk.StringVar(value=self.cfg["auto_rotate"])
        self.var_auto_dir = tk.StringVar(value=self.cfg["auto_dir"])

        self.cv = tk.Canvas(self, highlightthickness=0, bd=0, takefocus=1)
        self.cv.pack(fill="both", expand=True)
        self._build_toolbar()
        self._init_dnd()
        self.apply_settings()
        self._bind_all()
        self.protocol("WM_DELETE_WINDOW", self.close)

        self.deiconify()
        if self.cfg["zoomed"]:
            self.after(60, lambda: self._set_zoomed(True))
        self.cv.focus_set()
        if start_path:
            self.after(30, lambda: self.open_path(start_path))
        else:
            self.render()

    # -- config ------------------------------------------------------------- #

    @staticmethod
    def load_config():
        """@return the config dict: defaults, overridden by the saved file"""
        cfg = dict(DEFAULTS)
        try:
            with open(config_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
        if not isinstance(data, dict) or data.get("cfg_version", 1) < 2:
            # Version 2 changed the defaults: wheel does nothing, hold delay 100,
            # info off. Applied once to configs saved by version 1.
            cfg["wheel"], cfg["hold_ms"], cfg["show_info"] = "none", 100, False
        cfg["cfg_version"] = 2
        if not isinstance(cfg["last_path"], str):
            cfg["last_path"] = ""
        cfg["apps"] = default_apps() if cfg["apps"] is None else clean_apps(cfg["apps"])
        cfg["click_zoom"] = clamp_int(cfg["click_zoom"], 10, 3200, 100)
        cfg["hold_zoom"] = clamp_int(cfg["hold_zoom"], 10, 3200, 200)
        cfg["hold_ms"] = clamp_int(cfg["hold_ms"], 100, 2000, 100)
        if cfg["wheel"] not in ("none", "navigate", "zoom"):
            cfg["wheel"] = "none"
        if cfg["auto_rotate"] not in ("", "landscape", "portrait"):
            cfg["auto_rotate"] = ""
        if cfg["auto_dir"] not in ("cw", "ccw"):
            cfg["auto_dir"] = "cw"
        for key in ("enlarge_small", "show_info", "toolbar", "zoomed", "single_instance"):
            cfg[key] = bool(cfg[key])
        if not isinstance(cfg["geometry"], str):
            cfg["geometry"] = DEFAULTS["geometry"]
        return cfg

    def save_config(self):
        """Write the config (temporary file first). Shows an error box on failure."""
        path = config_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, path)
        except OSError as exc:
            messagebox.showerror(t("dlg.error"), t("msg.save_failed", error=exc), parent=self)

    def apply_settings(self):
        """Take the values of self.cfg into the running window."""
        self.var_info.set(self.cfg["show_info"])
        self.var_enlarge.set(self.cfg["enlarge_small"])
        self.col_bg = resolve_color(self, "viewer", "canvas_bg") or "#000000"
        self.col_info = resolve_color(self, "viewer", "info_text") or "#e6e6e6"
        self.col_err = resolve_color(self, "viewer", "error_text") or "#ff6b6b"
        self.cv.configure(background=self.col_bg)
        if not self.cfg["toolbar"]:
            self.tb.place_forget()
        self.render()

    # -- widgets ------------------------------------------------------------ #

    def _build_toolbar(self):
        """The floating on-screen buttons (shown when the mouse moves)."""
        self.tb = ttk.Frame(self, padding=4, relief="solid", borderwidth=1)
        self.var_auto = tk.StringVar()
        items = (("◀", "tip.prev", lambda: self.go(-1)),
                 ("▶", "tip.next", lambda: self.go(1)),
                 ("↺", "tip.rotate_left", lambda: self.rotate(-1)),
                 ("↻", "tip.rotate_right", lambda: self.rotate(1)),
                 (t("btn.fit"), "tip.fit", self.toggle_fit),
                 ("50%", "tip.zoom50", lambda: self.zoom_to(50)),
                 ("75%", "tip.zoom75", lambda: self.zoom_to(75)),
                 (t("btn.actual"), "tip.actual", lambda: self.zoom_to(100)))
        for text, tip, cmd in items:
            btn = ttk.Button(self.tb, text=text, width=max(3, len(text) + 1),
                             command=cmd, takefocus=False)
            btn.pack(side="left", padx=2)
            Tip(btn, tip)
        btn = ttk.Button(self.tb, textvariable=self.var_auto, width=19,
                         command=self.cycle_auto_rotate, takefocus=False)
        btn.pack(side="left", padx=2)
        Tip(btn, "tip.auto")
        btn = ttk.Button(self.tb, text="⚙", width=3, command=self.open_settings, takefocus=False)
        btn.pack(side="left", padx=2)
        Tip(btn, "tip.settings")
        self._update_auto_text()

    def _bind_all(self):
        """Mouse and key bindings."""
        cv = self.cv
        cv.bind("<ButtonPress-1>", self.on_press)
        cv.bind("<B1-Motion>", self.on_drag)
        cv.bind("<ButtonRelease-1>", self.on_release)
        cv.bind("<Motion>", self.poke_toolbar)
        cv.bind("<Configure>", self.on_resize)
        ctx = ("<Button-2>", "<Control-Button-1>") if IS_MAC else ("<Button-3>",)
        for seq in ctx:
            cv.bind(seq, self.show_context)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            cv.bind(seq, self.on_wheel)
        self.bind("<Key>", self.on_key)
        self.bind("<Control-w>", self.close)       # also fired when Caps Lock is on:
        self.bind("<Control-W>", self.close)
        if IS_MAC:
            try:
                self.bind("<Command-w>", self.close)
            except tk.TclError:
                pass

    # -- opening files ------------------------------------------------------ #

    def raise_window(self):
        """Restore the window if minimized and bring it to the front."""
        if self.state() == "iconic": self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(100, lambda: self.attributes("-topmost", False))
        self.focus_force()

    def _init_dnd(self):
        """Accept files dropped on the window (needs the optional tkinterdnd2; silently off without it)."""
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
        """
        Drop handler: open the first dropped image file or folder.

        @param data  Tcl list of dropped paths
        @return      the drop action for tkdnd
        """
        for p in self.tk.splitlist(data):
            if os.path.isdir(p) or (os.path.isfile(p)
                                    and os.path.splitext(p)[1].lower() in IMAGE_EXTS):
                self.after(0, lambda p=p: self.open_path(p))
                break
        return "copy"

    def open_path(self, path):
        """
        Show an image and load its folder for navigation. A folder shows its
        first image.

        @param path  image file or folder
        """
        path = os.path.abspath(path)
        if os.path.isdir(path):
            files = scan_folder(path)
            if not files:
                messagebox.showinfo(APP_NAME, t("msg.no_folder_images"), parent=self)
                return
            path = files[0]
        folder = os.path.dirname(path)
        if not same_file(folder, self.folder or "") or not self.files:
            self.rotations.clear()
        self.folder = folder
        self.files = scan_folder(folder)
        for i, f in enumerate(self.files):
            if same_file(f, path):
                self.index = i
                break
        else:                                # unusual extension: still show it
            self.files.append(path)
            self.index = len(self.files) - 1
        self.load(self.files[self.index])

    def ask_open(self):
        """File > Open: pick an image with the system dialog."""
        pats = " ".join("*" + e for e in sorted(IMAGE_EXTS))
        path = filedialog.askopenfilename(
            parent=self, title=t("dlg.open"), initialdir=self.folder or None,
            filetypes=[(t("filetype.images"), pats), (t("filetype.all"), "*.*")])
        if path:
            self.open_path(path)

    def load(self, path):
        """
        Decode one image and show it fitted. A failure shows the error text in
        the window instead.

        @param path  image file
        """
        self.path = path
        self.error = ""
        self.fmt = ""
        self.mode = "fit"
        try:
            with Image.open(path) as im:
                self.fmt = im.format or ""
                im.load()
                im = ImageOps.exif_transpose(im)
                if im.mode not in ("RGB", "RGBA"):
                    alpha = im.mode in ("LA", "PA") or "transparency" in im.info
                    im = im.convert("RGBA" if alpha else "RGB")
                self.base = im
            self.cfg["last_path"] = os.path.abspath(path)
        except Exception as exc:             # Pillow raises many different types
            self.base = None
            self.error = t("msg.cannot_open", name=os.path.basename(path), error=exc)
        self._rebuild_rotated()
        self._update_title()
        self.render()

    def _rebuild_rotated(self):
        """Make self.img from self.base and the view rotation of this file."""
        quarter = self._quarter()
        if self.base is None:
            self.img = None
        elif quarter:
            self.img = self.base.transpose(ROTATE_CW[quarter])
        else:
            self.img = self.base
        self._fit_key = None

    def _quarter(self):
        """@return quarter turns clockwise shown now: auto rotation + the manual one"""
        auto = 0
        mode = self.cfg["auto_rotate"]
        if self.base is not None and mode:
            w, h = self.base.size
            if (mode == "landscape" and h > w) or (mode == "portrait" and w > h):
                auto = 3 if self.cfg["auto_dir"] == "ccw" else 1
        return (auto + self.rotations.get(self.path, 0)) % 4

    def _update_auto_text(self):
        """Button text and menu variable of the auto rotate mode."""
        mode = self.cfg["auto_rotate"]
        arrow = "" if not mode else (" \u21ba" if self.cfg["auto_dir"] == "ccw" else " \u21bb")
        self.var_auto.set(t("btn.auto_" + (mode or "off")) + arrow)
        self.var_auto_mode.set(mode)
        self.var_auto_dir.set(self.cfg["auto_dir"])

    def set_auto_rotate(self, mode):
        """
        Set the auto rotate mode and redraw.

        @param mode  "" = off, "landscape" or "portrait"
        """
        self.cfg["auto_rotate"] = mode
        self._update_auto_text()
        self.mode = "fit"
        self._rebuild_rotated()
        self.render()

    def set_auto_dir(self, direction):
        """
        Set the auto rotate direction and redraw.

        @param direction  "cw" (clockwise) or "ccw" (counter clockwise)
        """
        self.cfg["auto_dir"] = direction
        self._update_auto_text()
        self.mode = "fit"
        self._rebuild_rotated()
        self.render()

    def cycle_auto_rotate(self):
        """Toolbar button: off -> landscape -> portrait -> off."""
        order = ("", "landscape", "portrait")
        self.set_auto_rotate(order[(order.index(self.cfg["auto_rotate"]) + 1) % 3])

    def _update_title(self):
        """Window title: file name and position in the folder."""
        if self.path:
            self.title(t("app.title_file", name=os.path.basename(self.path),
                         index=self.index + 1, count=len(self.files)))
        else:
            self.title(t("app.title", version=VERSION))

    # -- navigation / rotation / zoom commands ------------------------------ #

    def go(self, step):
        """Show the previous (-1) / next (+1) image of the folder, wrapping around."""
        if self.files:
            self.index = (self.index + step) % len(self.files)
            self.load(self.files[self.index])

    def go_to(self, index):
        """Show the image at a list position (negative counts from the end)."""
        if self.files:
            self.index = index % len(self.files)
            self.load(self.files[self.index])

    def rotate(self, quarter):
        """
        Rotate the VIEW of the current image (the file is not changed).

        @param quarter  +1 = 90 degrees clockwise, -1 = counter clockwise,
                        0 = reset
        """
        if self.base is None:
            return
        cur = self.rotations.get(self.path, 0)
        new = 0 if quarter == 0 else (cur + quarter) % 4
        if new:
            self.rotations[self.path] = new
        else:
            self.rotations.pop(self.path, None)
        self.mode = "fit"
        self._rebuild_rotated()
        self.render()

    def view_params(self):
        """
        @return  (scale, ox, oy) of what is drawn now; in zoom mode the offset
                 is first limited so the image never leaves a gap
        """
        cw, ch = self.cv.winfo_width(), self.cv.winfo_height()
        iw, ih = self.img.size
        if self.mode == "fit":
            s = min(cw / iw, ch / ih)
            if not self.cfg["enlarge_small"]:
                s = min(s, 1.0)
            return s, (cw - iw * s) / 2, (ch - ih * s) / 2
        s = self.scale
        for axis in (0, 1):
            size = (iw, ih)[axis] * s
            room = (cw, ch)[axis]
            off = (self.ox, self.oy)[axis]
            off = (room - size) / 2 if size <= room else min(0.0, max(room - size, off))
            if axis == 0:
                self.ox = off
            else:
                self.oy = off
        return s, self.ox, self.oy

    def set_fit(self):
        """Back to "fit to window"."""
        if self.img is not None:
            self.mode = "fit"
            self.render()

    def toggle_fit(self):
        """Fit width <-> fit height. From any other view: the side "fit to window" does not use."""
        if self.img is None:
            return
        cw, ch = self.cv.winfo_width(), self.cv.winfo_height()
        iw, ih = self.img.size
        sw, sh = cw / iw, ch / ih
        if self.mode == "zoom" and abs(self.scale - sw) < 1e-4:
            axis = "h"
        elif self.mode == "zoom" and abs(self.scale - sh) < 1e-4:
            axis = "w"
        else:
            axis = "h" if sw < sh else "w"
        s = min(max(sw if axis == "w" else sh, MIN_SCALE), MAX_SCALE)
        self.mode = "zoom"
        self.scale = s
        self.ox, self.oy = (cw - iw * s) / 2, 0.0
        self.render()

    def zoom_to(self, percent, cx=None, cy=None):
        """
        Zoom to a fixed scale, keeping the image point under (cx, cy) in place.

        @param percent  100 = actual size
        @param cx, cy   canvas point to zoom around (default: window center)
        """
        if self.img is None:
            return
        cw, ch = self.cv.winfo_width(), self.cv.winfo_height()
        cx = cw / 2 if cx is None else cx
        cy = ch / 2 if cy is None else cy
        s0, ox0, oy0 = self.view_params()
        iw, ih = self.img.size
        px = min(max((cx - ox0) / s0, 0), iw)      # image point under the cursor
        py = min(max((cy - oy0) / s0, 0), ih)
        s = min(max(percent / 100.0, MIN_SCALE), MAX_SCALE)
        self.mode = "zoom"
        self.scale = s
        self.ox, self.oy = cx - px * s, cy - py * s
        self.render()

    def zoom_step(self, factor, cx=None, cy=None):
        """Zoom in / out by a factor around a point."""
        if self.img is not None:
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

    # -- mouse -------------------------------------------------------------- #

    def on_press(self, e):
        """
        Button down. In fit mode a timer starts: if the button is still down
        after hold_ms the view zooms to hold_zoom ("click and hold").
        """
        self.cv.focus_set()
        self._press = {"x": e.x, "y": e.y, "lx": e.x, "ly": e.y,
                       "moved": False, "hold": False, "fit": None}
        self._cancel_hold()
        if self.img is not None and self.mode == "fit":
            self._hold_job = self.after(self.cfg["hold_ms"], self._begin_hold)

    def _cancel_hold(self):
        """Stop the click-and-hold timer."""
        if self._hold_job:
            self.after_cancel(self._hold_job)
            self._hold_job = None

    def _begin_hold(self):
        """The button was held long enough: zoom to hold_zoom at the press point."""
        self._hold_job = None
        p = self._press
        if p and not p["moved"] and self.img is not None and self.mode == "fit":
            p["hold"] = True
            p["fit"] = self.view_params()          # where the fitted picture is
            self.hold_view(p, p["x"], p["y"])

    def hold_view(self, p, x, y):
        """
        Click-and-hold view: the mouse position inside the FITTED picture picks
        the same relative position of the zoomed picture, so moving the mouse
        to the bottom left of the picture shows the bottom left of the original.

        @param p     the press state (p["fit"] = (scale, ox, oy) of the fitted view)
        @param x, y  mouse position on the canvas
        """
        s0, fx, fy = p["fit"]
        iw, ih = self.img.size
        u = min(max((x - fx) / (iw * s0), 0.0), 1.0)
        v = min(max((y - fy) / (ih * s0), 0.0), 1.0)
        cw, ch = self.cv.winfo_width(), self.cv.winfo_height()
        s = min(max(self.cfg["hold_zoom"] / 100.0, MIN_SCALE), MAX_SCALE)
        self.mode = "zoom"
        self.scale = s
        self.ox = u * (cw - iw * s)        # u = 0: left edges line up, u = 1: right edges
        self.oy = v * (ch - ih * s)        # (view_params centers an axis that fits)
        self.render()

    def on_drag(self, e):
        """Mouse moved with the button down: drag the picture when it is zoomed."""
        p = self._press
        if not p:
            return
        dx, dy = e.x - p["lx"], e.y - p["ly"]
        p["lx"], p["ly"] = e.x, e.y
        if p["hold"]:
            self.hold_view(p, e.x, e.y)
            return
        if not p["moved"] and (abs(e.x - p["x"]) > DRAG_PX or abs(e.y - p["y"]) > DRAG_PX):
            p["moved"] = True
            self._cancel_hold()
        if p["moved"] and self.mode == "zoom" and self.img is not None:
            self.ox += dx
            self.oy += dy
            self.render()

    def on_release(self, _e):
        """
        Button up:
          - after a click-and-hold zoom: back to fit
          - after a drag: nothing (the picture was only moved)
          - plain click: fit -> click_zoom at the point, zoomed -> fit
        """
        p, self._press = self._press, None
        self._cancel_hold()
        if not p or self.img is None:
            return
        if p["hold"]:
            self.set_fit()
        elif not p["moved"]:
            if self.mode == "fit":
                self.zoom_to(self.cfg["click_zoom"], p["x"], p["y"])
            else:
                self.set_fit()

    def on_wheel(self, e):
        """Wheel: nothing (default), or previous / next image / zoom (swapped with Ctrl)."""
        if self.cfg["wheel"] == "none":
            return
        if e.num == 4:
            up = True
        elif e.num == 5:
            up = False
        else:
            up = e.delta > 0
        ctrl = bool(e.state & 0x4)
        if ctrl != (self.cfg["wheel"] == "zoom"):
            self.zoom_step(1.25 if up else 0.8, e.x, e.y)
        else:
            self.go(-1 if up else 1)

    # -- keys --------------------------------------------------------------- #

    def on_key(self, e):
        """Keyboard shortcuts (see the module docstring)."""
        ctrl = bool(e.state & 0x4) or (IS_MAC and bool(e.state & 0x8))
        key = e.keysym
        if ctrl:
            low = key.lower()
            if low in ("w", "q", "cyrillic_tse", "cyrillic_shorti"):
                return self.close()
            if low in ("o", "cyrillic_shcha"):
                self.ask_open()
            elif key == "comma":
                self.open_settings()
            return
        if key in ("Left", "Prior", "BackSpace"):
            self.go(-1)
        elif key in ("Right", "Next", "space"):
            self.go(1)
        elif key == "Home":
            self.go_to(0)
        elif key == "End":
            self.go_to(-1)
        elif key == "F11":
            self.toggle_fullscreen()
        elif key == "Escape":
            if self.fullscreen:
                self.toggle_fullscreen(False)
        elif key == "r":
            self.rotate(1)
        elif key in ("R", "l", "L"):
            self.rotate(-1)
        elif key in ("f", "F"):
            self.toggle_fit()
        elif key == "0":
            self.set_fit()
        elif key == "1":
            self.zoom_to(100)
        elif key == "2":
            self.zoom_to(200)
        elif key in ("plus", "equal", "KP_Add"):
            self.zoom_step(1.25)
        elif key in ("minus", "KP_Subtract"):
            self.zoom_step(0.8)
        elif key in ("i", "I"):
            self.toggle_info()

    def toggle_info(self):
        """Show / hide the file info over the picture."""
        self.cfg["show_info"] = not self.cfg["show_info"]
        self.var_info.set(self.cfg["show_info"])
        self.render()

    def toggle_enlarge(self):
        """Menu check item: enlarge small images to fit the window."""
        self.cfg["enlarge_small"] = bool(self.var_enlarge.get())
        self.render()

    # -- toolbar ------------------------------------------------------------ #

    def poke_toolbar(self, _e=None):
        """Show the button bar and hide it again after a moment without movement."""
        if not self.cfg["toolbar"]:
            return
        if not self.tb.winfo_ismapped():
            self.tb.place(relx=0.5, rely=1.0, anchor="s", y=-18)
        self.tb.lift()
        if self._tb_job:
            self.after_cancel(self._tb_job)
        self._tb_job = self.after(2200, self._hide_toolbar)

    def _hide_toolbar(self):
        """Hide the button bar unless the pointer is on it."""
        self._tb_job = None
        try:
            w = self.winfo_containing(*self.winfo_pointerxy())
        except (KeyError, tk.TclError):
            w = None
        if w is not None and str(w).startswith(str(self.tb)):
            self._tb_job = self.after(1000, self._hide_toolbar)
            return
        self.tb.place_forget()

    # -- drawing ------------------------------------------------------------ #

    def on_resize(self, _e):
        """Canvas size changed: redraw after a short pause."""
        if self._render_job:
            self.after_cancel(self._render_job)
        self._render_job = self.after(40, self.render)

    def render(self):
        """Draw the current view: picture, info text or message."""
        self._render_job = None
        cv = self.cv
        cv.delete("all")
        self._photo = None
        cw, ch = cv.winfo_width(), cv.winfo_height()
        if cw < 4 or ch < 4:
            return
        if self.img is None:
            self._set_cursor("")
            msg = self.error or t("msg.no_image")
            cv.create_text(cw // 2, ch // 2, text=msg, width=max(cw - 60, 100), justify="center",
                           fill=self.col_err if self.error else self.col_info)
            return
        iw, ih = self.img.size
        s, ox, oy = self.view_params()
        x0 = max(0, int(math.floor(-ox / s)))
        y0 = max(0, int(math.floor(-oy / s)))
        x1 = min(iw, int(math.ceil((cw - ox) / s)))
        y1 = min(ih, int(math.ceil((ch - oy) / s)))
        if x1 > x0 and y1 > y0:
            key = (self.path, self._quarter(), self.mode, round(s, 5), cw, ch)
            if self.mode == "fit" and self._fit_key == key:
                photo = self._fit_photo
            else:
                crop = self.img.crop((x0, y0, x1, y1))
                dw = max(1, int(round((x1 - x0) * s)))
                dh = max(1, int(round((y1 - y0) * s)))
                if s < 1:
                    factor = int(1 / s)
                    if factor >= 2:           # cheap pre-shrink, then a good resize
                        crop = crop.reduce(factor)
                    crop = crop.resize((dw, dh), _RS.LANCZOS)
                elif s > 1:
                    crop = crop.resize((dw, dh), _RS.BICUBIC)
                photo = ImageTk.PhotoImage(crop)
                if self.mode == "fit":
                    self._fit_key, self._fit_photo = key, photo
            self._photo = photo
            cv.create_image(int(round(ox + x0 * s)), int(round(oy + y0 * s)),
                            anchor="nw", image=photo)
        self._set_cursor("fleur" if self.mode == "zoom" else "")
        if self.cfg["show_info"]:
            text = "%s   %d/%d   %d\u00d7%d   %d%%" % (
                os.path.basename(self.path), self.index + 1, len(self.files),
                iw, ih, round(s * 100))
            cv.create_text(11, 9, text=text, anchor="nw", fill=self.col_bg)
            cv.create_text(10, 8, text=text, anchor="nw", fill=self.col_info)

    def _set_cursor(self, cursor):
        """Change the canvas cursor only when it differs."""
        if cursor != self._cursor:
            self._cursor = cursor
            self.cv.configure(cursor=cursor)

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
        """
        @return  the context menu: open with, file location, copy path,
                 properties, rotate, zoom, navigation, settings, close
        """
        m = tk.Menu(self, tearoff=0)
        have = bool(self.path) and os.path.exists(self.path)
        state = "normal" if have else "disabled"
        shown = "normal" if self.img is not None else "disabled"

        sub = tk.Menu(m, tearoff=0)
        for a in self.cfg["apps"]:
            sub.add_command(label=a["name"], state=state,
                            command=lambda a=a: self.run_app(a))
        if not self.cfg["apps"]:
            sub.add_command(label=t("menu.no_programs"), state="disabled")
        sub.add_separator()
        sub.add_command(label=t("menu.manage_programs"), command=lambda: self.open_settings(1))
        m.add_cascade(label=t("menu.open_with"), menu=sub, state=state)
        m.add_command(label=t("menu.open_location"), state=state,
                      command=lambda: self._safely(reveal_in_folder, self.path,
                                                   "msg.location_failed"))
        m.add_separator()
        m.add_command(label=t("menu.copy_path"), state=state,
                      command=lambda: self.copy_text(os.path.abspath(self.path)))
        m.add_command(label=t("menu.copy_name"), state=state,
                      command=lambda: self.copy_text(os.path.basename(self.path)))
        m.add_command(label=t("menu.copy_folder"), state=state,
                      command=lambda: self.copy_text(os.path.dirname(os.path.abspath(self.path))))
        m.add_command(label=t("menu.properties"), state=state, command=self.show_properties)
        m.add_separator()
        m.add_command(label=t("menu.rotate_right"), state=shown, accelerator="R",
                      command=lambda: self.rotate(1))
        m.add_command(label=t("menu.rotate_left"), state=shown, accelerator="L",
                      command=lambda: self.rotate(-1))
        m.add_command(label=t("menu.rotate_reset"), state=shown,
                      command=lambda: self.rotate(0))
        m.add_separator()
        m.add_command(label=t("menu.fit_toggle"), state=shown, accelerator="F",
                      command=self.toggle_fit)
        m.add_command(label=t("menu.fit"), state=shown, accelerator="0", command=self.set_fit)
        m.add_command(label=t("menu.actual"), state=shown, accelerator="1",
                      command=lambda: self.zoom_to(100))
        m.add_command(label=t("menu.zoom200"), state=shown, accelerator="2",
                      command=lambda: self.zoom_to(200))
        auto = tk.Menu(m, tearoff=0)
        for val, key in (("", "off"), ("landscape", "landscape"), ("portrait", "portrait")):
            auto.add_radiobutton(label=t("menu.auto_" + key), variable=self.var_auto_mode,
                                 value=val, command=lambda v=val: self.set_auto_rotate(v))
        auto.add_separator()
        for val in ("cw", "ccw"):
            auto.add_radiobutton(label=t("menu.auto_" + val), variable=self.var_auto_dir,
                                 value=val, command=lambda v=val: self.set_auto_dir(v))
        m.add_cascade(label=t("menu.auto_rotate"), menu=auto, state=shown)
        m.add_checkbutton(label=t("menu.enlarge_small"), variable=self.var_enlarge,
                          command=self.toggle_enlarge)
        m.add_checkbutton(label=t("menu.show_info"), variable=self.var_info,
                          accelerator="I", command=self.toggle_info_from_menu)
        m.add_command(label=t("menu.fullscreen"), accelerator="F11",
                      command=self.toggle_fullscreen)
        m.add_separator()
        many = "normal" if len(self.files) > 1 else "disabled"
        m.add_command(label=t("menu.prev"), state=many, accelerator="\u2190",
                      command=lambda: self.go(-1))
        m.add_command(label=t("menu.next"), state=many, accelerator="\u2192",
                      command=lambda: self.go(1))
        m.add_separator()
        m.add_command(label=t("menu.open_image"), accelerator="Ctrl+O", command=self.ask_open)
        m.add_command(label=t("menu.settings"), accelerator="Ctrl+,", command=self.open_settings)
        m.add_command(label=t("menu.close"), accelerator="Ctrl+W", command=self.close)
        return m

    def toggle_info_from_menu(self):
        """Menu check item: the variable was already flipped by the menu."""
        self.cfg["show_info"] = bool(self.var_info.get())
        self.render()

    def copy_text(self, text):
        """Put text on the clipboard."""
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()

    def _safely(self, func, arg, msg_key="msg.launch_failed"):
        """Call func(arg); show a message box if it raises OSError."""
        try:
            func(arg)
        except OSError as exc:
            messagebox.showerror(t("dlg.error"), t(msg_key, error=exc), parent=self)

    def run_app(self, app):
        """
        Open the current image with an "Open with" program.

        @param app  {"name", "cmd"} entry of the Programs list
        """
        if not self.path:
            return
        try:
            argv = build_argv(app["cmd"], os.path.abspath(self.path))
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
        path = os.path.abspath(self.path)
        try:
            st = os.stat(path)
            size = "%s (%d B)" % (size_text(st.st_size), st.st_size)
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
        except OSError:
            size = when = "?"
        dims = "%d \u00d7 %d" % self.base.size if self.base is not None else "?"
        lines = [(t("prop.name"), os.path.basename(path)), (t("prop.folder"), os.path.dirname(path)),
                 (t("prop.size"), size), (t("prop.dimensions"), dims),
                 (t("prop.format"), self.fmt or "?"), (t("prop.modified"), when),
                 (t("prop.rotation"), "%d\u00b0" % (90 * self._quarter()))]
        messagebox.showinfo(t("dlg.properties"), "\n".join("%s: %s" % kv for kv in lines),
                            parent=self)

    def open_settings(self, tab=0):
        """Open the settings window (tab 0 General, 1 Programs, 2 Appearance, 3 About)."""
        SettingsDialog(self, tab)
        self.cv.focus_set()

    # -- closing ------------------------------------------------------------ #

    def save_ui(self):
        """Remember window size / position and maximized state."""
        zoomed = self._is_zoomed()
        self.cfg["zoomed"] = zoomed
        if not zoomed and not self.fullscreen:
            self.cfg["geometry"] = self.winfo_geometry()
        self.save_config()

    def close(self, _e=None):
        """Ctrl+W / window close: save the window state and close this window."""
        if self._closing:
            return "break"
        self._closing = True
        try:
            self.save_ui()
        finally:
            self.destroy()
            self.host.closed(self)
        return "break"


class Viewers:
    """
    The one program process: a hidden Tk root, the shared config and every
    viewer window. With a listener (see claim_instance) it also opens the files
    of later launches as new windows, so all windows share one Python / Tk /
    Pillow footprint. The program ends with its last window.
    """

    def __init__(self, cfg, listener=None):
        """
        @param cfg       config dict (App.load_config()), shared by all windows
        @param listener  socket of claim_instance() that receives the files of later launches
        """
        self.cfg = cfg
        self.listener = listener
        self.windows = []
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

        @param path  image or folder of the command line (optional)
        """
        self.open(path)
        if self.listener:
            threading.Thread(target=self._serve, daemon=True).start()
            self._poll_inbox()
        self.root.mainloop()

    def open(self, path=None):
        """
        Show an image in a new window; if a window already shows it, raise that
        one. No path: raise the newest window (or open an empty one).

        @param path  image file or folder, or empty
        """
        if path:
            for w in self.windows:
                if w.path and same_file(w.path, path):
                    w.raise_window()
                    return
        elif self.windows:
            self.windows[-1].raise_window()
            return
        win = App(self, path or None)
        self.windows.append(win)
        if self.windows[1:]: win.raise_window()

    def closed(self, win):
        """
        A window was closed; the program ends with the last one.

        @param win  the closed App
        """
        if win in self.windows: self.windows.remove(win)
        if self.windows: return
        self.closing = True
        if self.listener: self.listener.close()
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
            if isinstance(path, str): self.inbox.put(path)

    def _poll_inbox(self):
        """Tk thread: open the paths that later launches sent."""
        while True:
            try:
                path = self.inbox.get_nowait()
            except queue.Empty:
                break
            self.open(path)
        if not self.closing: self.root.after(150, self._poll_inbox)


def main(argv):
    """
    Start the viewer.

    @param argv  command line without the program name; an optional image or folder
    """
    detach_console()
    path = argv[0] if argv else None
    cfg = App.load_config()
    listener = None
    if cfg["single_instance"]:
        forwarded, listener = claim_instance(path)
        if forwarded: return
    enable_hidpi()
    set_app_id()
    Viewers(cfg, listener).run(path)


if __name__ == "__main__":
    main(sys.argv[1:])
