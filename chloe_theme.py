#!/usr/bin/env python3
"""
chloe_theme.py - Themes for chloe_photoviewer.py.

Same idea as the Firmware Manager theme module: one theme = one JSON file in
theme/ next to this script (theme/<code>.json). A theme holds EVERY color the
program uses plus the ttk theme it is drawn with. Nothing is hardcoded in the
program except last-resort fallbacks. An empty or invalid value means "not
set" and the system / Tk draws that part itself.

Theme file layout (any entry may be missing, it then counts as empty):
    {
      "theme_name": "Dark OS",            display name in Settings
      "ttk_theme": "clam",                ttk theme to switch to, "" = keep the system one
      "palette": {name: color, ...},      window and widget colors (see GROUPS)
      "viewer":  {name: color, ...}       image area, info text, error text
    }
Colors are #rrggbb or a Tk color name. Only the names listed in GROUPS are
read and written.

The selected theme code is saved in the config as "theme". A theme is used from
the next program start. A theme file is edited in Settings > Appearance >
Edit theme... or by hand while the program is closed.

The names of the colors and groups are the entries "<group>.<name>" and
"group.<group>" of lang/<code>.json (see chloe_lang.py).
"""

import json
import os
import re
import tkinter as tk
from tkinter import ttk, messagebox, colorchooser
import tkinter.font as tkfont

from chloe_lang import t

GROUPS = {
    # Window and widget colors. Used when a theme sets any of them; if every
    # entry is empty the system look is left completely alone.
    "palette": ("bg", "surface", "surface_hi", "surface_press", "field",
                "fg", "fg_disabled", "border", "select_bg", "select_fg",
                "thumb"),
    # The picture area.
    "viewer": ("canvas_bg", "info_text", "error_text"),
}
"""Every color of a theme: group -> color names. These are ids, not colors."""

THEME_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "theme")
"""Folder with one JSON file per theme, named <code>.json."""

DEFAULT_THEME = "dark_os"
"""Theme code used when the config has none (or its theme file is gone)."""

NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
"""Allowed theme codes (file names without .json)."""

_active = {"code": "", "data": None}
"""The theme in use: its code and its normalized data (see set_theme)."""


def _text(value):
    """@return the stripped string, or "" if value is not a string"""
    return value.strip() if isinstance(value, str) else ""


def _normalize(data):
    """
    Complete a theme read from a file: every entry of GROUPS exists and every
    value is a string ("" = not set).

    @param data  parsed JSON (anything but a dict counts as empty)
    @return      {"theme_name", "ttk_theme", <group>: {<name>: color}}
    """
    if not isinstance(data, dict):
        data = {}
    out = {"theme_name": _text(data.get("theme_name")),
           "ttk_theme": _text(data.get("ttk_theme"))}
    for group, names in GROUPS.items():
        sub = data.get(group)
        if not isinstance(sub, dict):
            sub = {}
        out[group] = {name: _text(sub.get(name)) for name in names}
    return out


_active["data"] = _normalize({})


def theme_path(code):
    """@return path of <code>.json inside THEME_DIR"""
    return os.path.join(THEME_DIR, code + ".json")


def _read(code):
    """@return the parsed dict of a theme file; empty if missing or invalid"""
    try:
        with open(theme_path(code), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_theme(code):
    """@return the normalized theme of a file (all "" if missing or invalid)"""
    return _normalize(_read(code))


def save_theme(code, data):
    """
    Write a theme to theme/<code>.json (created if needed). Written to a
    temporary name first, so a failed write never leaves a half written theme.

    @param code  theme code (letters, digits, _ and -)
    @param data  theme in the layout of _normalize
    @raise OSError  if the file cannot be written
    """
    os.makedirs(THEME_DIR, exist_ok=True)
    path = theme_path(code)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_normalize(data), f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def list_themes():
    """
    @return  list of (code, display name) sorted by display name; the name is
             the "theme_name" entry of the file, or the code if it has none
    """
    found = []
    try:
        files = sorted(os.listdir(THEME_DIR))
    except OSError:
        return found
    for fn in files:
        if not fn.lower().endswith(".json"):
            continue
        code = fn[:-5]
        name = _read(code).get("theme_name")
        found.append((code, name if isinstance(name, str) and name else code))
    return sorted(found, key=lambda item: item[1].lower())


def resolve_theme(code):
    """
    Check a theme code without activating it.

    @return  code if its file is valid, else DEFAULT_THEME if valid, else the
             first valid theme, else "" (no theme files: system colors)
    """
    if isinstance(code, str) and code and _read(code):
        return code
    if _read(DEFAULT_THEME):
        return DEFAULT_THEME
    for other, _name in list_themes():
        if _read(other):
            return other
    return ""


def set_theme(code):
    """
    Make a theme the active one (the one apply_theme and resolve_color use).

    @return  the code that is now active
    """
    code = resolve_theme(code)
    _active["code"] = code
    _active["data"] = load_theme(code) if code else _normalize({})
    return code


def resolve_color(widget, group, name):
    """
    Color of the active theme if Tk accepts it.

    @param widget  any Tk widget, used to test the color
    @return        the color string, or "" if empty / rejected by Tk
    """
    value = _active["data"][group][name]
    if not value:
        return ""
    try:
        widget.winfo_rgb(value)
    except tk.TclError:
        return ""
    return value


def color_label(group, name):
    """@return the language text for "group.name", or name if there is none"""
    key = "%s.%s" % (group, name)
    text = t(key)
    return name if text == key else text


def group_label(group):
    """@return the language text for "group.<group>", or group if none"""
    key = "group." + group
    text = t(key)
    return group if text == key else text


# --------------------------------------------------------------------------- #
# Applying a theme
# --------------------------------------------------------------------------- #


def _cfg(style, name, **opts):
    """style.configure(name, ...) with only the options that have a value."""
    opts = {k: v for k, v in opts.items() if v not in ("", None)}
    if opts:
        style.configure(name, **opts)


def _map(style, name, **opts):
    """style.map(name, ...) with only the (state, color) pairs that have a color."""
    clean = {}
    for opt, pairs in opts.items():
        keep = [(state, color) for state, color in pairs if color]
        if keep:
            clean[opt] = keep
    if clean:
        style.map(name, **clean)


def _style_ttk(style, p):
    """
    Recolor every ttk widget class the program uses.

    @param style  the ttk.Style
    @param p      palette colors {name: color}; "" = not set
    """
    bg, surface, hi, press = p["bg"], p["surface"], p["surface_hi"], p["surface_press"]
    field, fg, off, border = p["field"], p["fg"], p["fg_disabled"], p["border"]
    sel_bg, sel_fg, thumb = p["select_bg"], p["select_fg"], p["thumb"]

    _cfg(style, ".", background=bg, foreground=fg, fieldbackground=field,
         bordercolor=border, darkcolor=bg, lightcolor=bg, troughcolor=field,
         selectbackground=sel_bg, selectforeground=sel_fg, insertcolor=fg,
         focuscolor=bg)
    _map(style, ".", foreground=[("disabled", off)])

    _cfg(style, "TFrame", background=bg)
    _cfg(style, "TLabel", background=bg, foreground=fg)
    _cfg(style, "TLabelframe", background=bg, bordercolor=border,
         darkcolor=bg, lightcolor=bg)
    _cfg(style, "TLabelframe.Label", background=bg, foreground=fg)
    _cfg(style, "TSeparator", background=border)

    _cfg(style, "TButton", background=surface, foreground=fg,
         bordercolor=border, darkcolor=surface, lightcolor=surface)
    _map(style, "TButton",
         background=[("disabled", bg), ("pressed", press), ("active", hi)],
         darkcolor=[("pressed", press), ("active", hi)],
         lightcolor=[("pressed", press), ("active", hi)],
         foreground=[("disabled", off)])

    for name in ("TCheckbutton", "TRadiobutton"):
        _cfg(style, name, background=bg, foreground=fg,
             indicatorbackground=field, indicatorforeground=fg)
        _map(style, name,
             background=[("active", bg)],
             indicatorbackground=[("pressed", press), ("selected", sel_bg)])

    for name in ("TEntry", "TSpinbox"):
        _cfg(style, name, fieldbackground=field, foreground=fg,
             insertcolor=fg, bordercolor=border, darkcolor=field,
             lightcolor=field, arrowcolor=fg, background=surface)
        _map(style, name,
             fieldbackground=[("disabled", bg), ("readonly", surface)],
             foreground=[("disabled", off)])

    _cfg(style, "TCombobox", fieldbackground=field, background=surface,
         foreground=fg, arrowcolor=fg, insertcolor=fg, bordercolor=border,
         darkcolor=field, lightcolor=field, selectbackground=field,
         selectforeground=fg)
    _map(style, "TCombobox",
         fieldbackground=[("readonly", field), ("disabled", bg)],
         background=[("active", hi), ("pressed", press)],
         foreground=[("disabled", off)],
         arrowcolor=[("disabled", off)],
         selectbackground=[("readonly", field)],
         selectforeground=[("readonly", fg)])

    _cfg(style, "TNotebook", background=bg, bordercolor=border,
         tabmargins=(2, 3, 2, 0))
    _cfg(style, "TNotebook.Tab", background=surface, foreground=fg,
         bordercolor=border, padding=(12, 4))
    _map(style, "TNotebook.Tab",
         background=[("selected", bg), ("active", hi)],
         foreground=[("selected", fg)])

    for name in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        _cfg(style, name, background=thumb, troughcolor=field,
             bordercolor=field, arrowcolor=fg, darkcolor=thumb,
             lightcolor=thumb)
        _map(style, name, background=[("active", press)])

    _cfg(style, "Treeview", background=field, fieldbackground=field,
         foreground=fg, bordercolor=border, darkcolor=field, lightcolor=field,
         rowheight=22)
    _cfg(style, "Treeview.Heading", background=surface, foreground=fg,
         bordercolor=border, darkcolor=surface, lightcolor=surface,
         relief="flat", padding=(6, 3))
    _map(style, "Treeview.Heading", background=[("active", hi)])
    _map(style, "Treeview",
         background=[("selected", sel_bg)],
         foreground=[("selected", sel_fg)])


def _style_classic(root, p):
    """
    Fill the Tk option database so classic (non-ttk) widgets are created with
    the palette. Only widgets created AFTER this call are affected.

    @param root  the Tk root window (also recolored directly)
    @param p     palette colors {name: color}; "" = not set
    """
    if p["bg"]:
        root.configure(bg=p["bg"])

    def opt(pattern, value):
        if value not in ("", None):
            root.option_add(pattern, value, 80)

    opt("*Toplevel.background", p["bg"])
    opt("*Menu.background", p["surface"])
    opt("*Menu.foreground", p["fg"])
    opt("*Menu.activeBackground", p["select_bg"])
    opt("*Menu.activeForeground", p["select_fg"])
    opt("*Menu.disabledForeground", p["fg_disabled"])
    opt("*Menu.borderWidth", 0)
    opt("*Menu.relief", "flat")
    for pattern in ("*TCombobox*Listbox", "*Listbox"):
        opt(pattern + ".background", p["field"])
        opt(pattern + ".foreground", p["fg"])
        opt(pattern + ".selectBackground", p["select_bg"])
        opt(pattern + ".selectForeground", p["select_fg"])
    opt("*Label.background", p["bg"])
    opt("*Label.foreground", p["fg"])
    opt("*Frame.background", p["bg"])
    opt("*Canvas.highlightThickness", 0)


def apply_theme(root):
    """
    Draw the program with the active theme. Call once, right after the root
    window exists and BEFORE any widget is created (the Tk option database is
    only read when a widget is created).

    @param root  the Tk root window
    """
    style = ttk.Style(root)
    base = _active["data"]["ttk_theme"]
    if base and base in style.theme_names():
        style.theme_use(base)
    pal = {name: resolve_color(root, "palette", name) for name in GROUPS["palette"]}
    if any(pal.values()):
        _style_ttk(style, pal)
        _style_classic(root, pal)


# --------------------------------------------------------------------------- #
# Theme editor
# --------------------------------------------------------------------------- #


class ThemeDialog(tk.Toplevel):
    """
    Modal editor for every value of a theme file: the theme file (pick an
    existing one, or type a new name to save a copy), its display name, its
    ttk theme and one section per color group. Each color row has a
    description, the id (group.name), a swatch, a text entry (#rrggbb, a color
    name, or empty = system) and a Pick button for the system color chooser,
    with a live preview on the left.
    After wait_window(), .result is the code of the saved theme, or None.
    """

    def __init__(self, parent, code):
        """
        @param parent  owner window
        @param code    theme code to edit first (the selected theme)
        """
        super().__init__(parent)
        self.withdraw()
        self.title(t("dlg.theme"))
        self.transient(parent)
        self.result = None
        self.codes = [c for c, _name in list_themes()]
        self.code = code if code in self.codes else (self.codes[0] if self.codes else "")
        self.snapshot = None
        self.vars = {}
        self.swatches = {}
        self.theme_var = tk.StringVar(value=self.code)
        self.name_var = tk.StringVar()
        self.base_var = tk.StringVar()
        self.plain = self.cget("background")
        desc_w = max(len(color_label(g, n)) for g, names in GROUPS.items() for n in names)
        id_w = max(len("%s.%s" % (g, n)) for g, names in GROUPS.items() for n in names)

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        btns = ttk.Frame(frm)
        btns.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Button(btns, text=t("btn.revert"), command=self._revert).pack(side="left")
        ttk.Button(btns, text=t("btn.cancel"), command=self.destroy).pack(side="right")
        ttk.Button(btns, text=t("btn.ok"), command=self._ok).pack(side="right", padx=3)

        self._pv = []
        left = ttk.Frame(frm)
        left.pack(side="left", fill="y", padx=(0, 10))
        self._build_preview(left)

        head = ttk.Frame(frm)
        head.pack(side="top", fill="x", pady=(0, 8))
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text=t("field.theme_file")).grid(row=0, column=0, sticky="w", pady=3)
        box = ttk.Combobox(head, textvariable=self.theme_var, values=self.codes)
        box.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        box.bind("<<ComboboxSelected>>", self._on_pick_theme)
        ttk.Label(head, text=t("field.theme_name")).grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(head, textvariable=self.name_var).grid(row=1, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(head, text=t("field.theme_base")).grid(row=2, column=0, sticky="w", pady=3)
        ttk.Combobox(head, textvariable=self.base_var,
                     values=[""] + list(ttk.Style(self).theme_names())).grid(
            row=2, column=1, sticky="ew", padx=(8, 0))

        area = ttk.Frame(frm)
        area.pack(side="top", fill="both", expand=True)
        bg = ttk.Style(self).lookup("TFrame", "background") or self.plain
        self.canvas = tk.Canvas(area, highlightthickness=0, background=bg, yscrollincrement=30)
        sb = ttk.Scrollbar(area, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>",
                  lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

        for group, names in GROUPS.items():
            tab = ttk.LabelFrame(body, text=group_label(group), padding=8)
            tab.pack(fill="x", pady=(0, 8), padx=2)
            for row, name in enumerate(names):
                key = (group, name)
                ttk.Label(tab, text=color_label(group, name), width=desc_w).grid(
                    row=row, column=0, sticky="w", pady=3)
                ttk.Label(tab, text="%s.%s" % (group, name), width=id_w).grid(
                    row=row, column=1, sticky="w", padx=(12, 0))
                box = tk.Label(tab, width=8, relief="solid", borderwidth=1)
                box.grid(row=row, column=2, padx=8)
                self.swatches[key] = box
                var = tk.StringVar()
                self.vars[key] = var
                var.trace_add("write", lambda *a, k=key: self._swatch(k))
                ttk.Entry(tab, textvariable=var, width=12).grid(row=row, column=3)
                ttk.Button(tab, text=t("btn.pick"),
                           command=lambda g=group, n=name: self._pick(g, n)
                           ).grid(row=row, column=4, padx=(5, 0))

        self._load(self.code)
        self.update_idletasks()
        self.canvas.configure(
            width=body.winfo_reqwidth(),
            height=min(body.winfo_reqheight(), int(self.winfo_screenheight() * 0.6)))
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind(seq, self._wheel)
        self.bind("<Escape>", lambda e: self.destroy())
        self._center(parent)
        self.deiconify()
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        self.wait_window(self)

    # -- preview ------------------------------------------------------------ #

    def _pcolor(self, spec):
        """
        Color for the preview: the entry value if Tk accepts it, else a system
        like fallback.

        @param spec  "<group>.<name>", e.g. "palette.bg"
        @return      a color string usable by Tk
        """
        group, name = spec.split(".", 1)
        value = self.vars[(group, name)].get().strip() if (group, name) in self.vars else ""
        if value and self._valid(value):
            return value
        style = ttk.Style(self)
        return {
            "palette.bg": self.plain, "palette.surface": self.plain,
            "palette.surface_hi": self.plain, "palette.surface_press": self.plain,
            "palette.field": style.lookup("TEntry", "fieldbackground") or "white",
            "palette.fg": style.lookup("TLabel", "foreground") or "black",
            "palette.fg_disabled": "gray50", "palette.border": "gray50",
            "palette.select_bg": "#3399ff", "palette.select_fg": "white",
            "palette.thumb": "gray60",
            "viewer.canvas_bg": "black", "viewer.info_text": "white",
            "viewer.error_text": "#ff6b6b",
        }[spec]

    def _pv_add(self, widget, **opts):
        """Register a preview widget with option -> color spec, return it."""
        self._pv.append((widget, opts))
        return widget

    def _build_preview(self, parent):
        """Tabs, buttons, input, scrollbar, list rows and the picture area."""
        add = self._pv_add
        font = tkfont.nametofont("TkDefaultFont")
        root = add(tk.Frame(parent, bd=1, relief="solid", padx=8, pady=8),
                   background="palette.bg", highlightbackground="palette.border")
        root.pack(fill="y", expand=True)

        tabs = add(tk.Frame(root), background="palette.bg")
        tabs.pack(fill="x")
        for text, bg in ((t("tab.general"), "palette.bg"), (t("tab.programs"), "palette.surface")):
            add(tk.Label(tabs, text=text, padx=10, pady=3, font=font),
                background=bg, foreground="palette.fg").pack(side="left", padx=(0, 2))

        row = add(tk.Frame(root), background="palette.bg")
        row.pack(fill="x", pady=(8, 0))
        for text, bg, fg in ((t("btn.ok"), "palette.surface", "palette.fg"),
                             (t("btn.edit"), "palette.surface_hi", "palette.fg"),
                             (t("btn.add"), "palette.surface_press", "palette.fg"),
                             (t("btn.remove"), "palette.bg", "palette.fg_disabled")):
            add(tk.Label(row, text=text, padx=8, pady=2, font=font, bd=0, highlightthickness=1),
                background=bg, foreground=fg,
                highlightbackground="palette.border").pack(side="left", padx=(0, 4))

        entry = add(tk.Entry(root, font=font, highlightthickness=1, bd=0, width=24),
                    background="palette.field", foreground="palette.fg",
                    insertbackground="palette.fg",
                    selectbackground="palette.select_bg",
                    selectforeground="palette.select_fg",
                    highlightbackground="palette.border")
        entry.insert(0, "GIMP")
        entry.select_range(0, 2)
        entry.pack(fill="x", pady=(8, 0))

        trough = add(tk.Frame(root, height=10), background="palette.field")
        trough.pack(fill="x", pady=(6, 0))
        trough.pack_propagate(False)
        add(tk.Frame(trough, width=70), background="palette.thumb").pack(side="left", fill="y")

        add(tk.Label(root, text="Krita", anchor="w", padx=6),
            background="palette.field", foreground="palette.fg").pack(fill="x", pady=(8, 0))
        add(tk.Label(root, text="GIMP", anchor="w", padx=6),
            background="palette.select_bg", foreground="palette.select_fg").pack(fill="x")

        pic = add(tk.Frame(root, height=90), background="viewer.canvas_bg")
        pic.pack(fill="x", pady=(8, 0))
        pic.pack_propagate(False)
        add(tk.Label(pic, text="photo.jpg  3/27  4000x3000  100%", anchor="w", padx=6),
            background="viewer.canvas_bg", foreground="viewer.info_text").pack(fill="x", pady=(6, 0))
        add(tk.Label(pic, text="x", anchor="w", padx=6),
            background="viewer.canvas_bg", foreground="viewer.error_text").pack(fill="x")

    def _pv_update(self):
        """Recolor the preview from the colors currently in the entries."""
        if not getattr(self, "_pv", None):
            return
        for widget, opts in self._pv:
            widget.configure(**{opt: self._pcolor(spec) for opt, spec in opts.items()})

    # -- dialog plumbing ---------------------------------------------------- #

    def _wheel(self, event):
        """Scroll the list with the mouse wheel (Button-4/5 on X11)."""
        if event.num == 4:
            step = -1
        elif event.num == 5:
            step = 1
        else:
            step = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(step, "units")

    def _center(self, parent):
        """Place the dialog in the middle of its parent window."""
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - h) // 2
        self.geometry("+%d+%d" % (max(x, 0), max(y, 0)))

    def _valid(self, color):
        """@return True if the color is empty (= system) or Tk understands it"""
        if not color:
            return True
        try:
            self.winfo_rgb(color)
        except tk.TclError:
            return False
        return True

    def _swatch(self, key):
        """Fill one swatch with its entry's color ("system" / "invalid")."""
        color = self.vars[key].get().strip()
        if not color:
            self.swatches[key].configure(background=self.plain, text=t("val.unset"))
        elif self._valid(color):
            self.swatches[key].configure(background=color, text="")
        else:
            self.swatches[key].configure(background=self.plain, text=t("val.invalid"))
        self._pv_update()

    def _pick(self, group, name):
        """Open the system color chooser and put the result in the entry."""
        current = self.vars[(group, name)].get().strip()
        opts = {"color": current} if current and self._valid(current) else {}
        _rgb, hexval = colorchooser.askcolor(parent=self, title=color_label(group, name), **opts)
        if hexval:
            self.vars[(group, name)].set(hexval)

    def _collect(self):
        """@return everything shown in the dialog as a theme dict"""
        out = {"theme_name": self.name_var.get().strip(),
               "ttk_theme": self.base_var.get().strip()}
        for group, names in GROUPS.items():
            out[group] = {n: self.vars[(group, n)].get().strip() for n in names}
        return out

    def _load(self, code):
        """Show a theme file in the entries (all empty if code is empty)."""
        data = load_theme(code) if code else _normalize({})
        self.name_var.set(data["theme_name"])
        self.base_var.set(data["ttk_theme"])
        for (group, name), var in self.vars.items():
            var.set(data[group][name])
        self.snapshot = self._collect()
        self._pv_update()

    def _dirty(self):
        """@return True if the entries differ from what was loaded"""
        return self._collect() != self.snapshot

    def _on_pick_theme(self, _event=None):
        """Load the picked theme file, after asking if there are unsaved edits."""
        new = self.theme_var.get().strip()
        if self._dirty() and not messagebox.askyesno(
                t("dlg.theme"), t("msg.discard"), parent=self):
            self.theme_var.set(self.code)
            return
        self.code = new
        self._load(new)

    def _revert(self):
        """Reload the loaded theme file from disk, dropping all edits."""
        self.theme_var.set(self.code)
        self._load(self.code)

    def _ok(self):
        """Check every entry, write the theme file, close with its code in .result."""
        code = self.theme_var.get().strip()
        if not NAME_RE.match(code):
            messagebox.showerror(t("dlg.theme"), t("msg.theme_bad_name"), parent=self)
            return
        data = self._collect()
        bad = ["%s.%s  (%s)" % (g, n, color_label(g, n))
               for g, names in GROUPS.items() for n in names
               if not self._valid(data[g][n])]
        if bad:
            messagebox.showerror(t("dlg.theme"),
                                 t("msg.theme_invalid", list="\n  ".join(bad)), parent=self)
            return
        try:
            save_theme(code, data)
        except OSError as exc:
            messagebox.showerror(t("dlg.theme"),
                                 t("msg.theme_save_failed", error=exc), parent=self)
            return
        self.result = code
        self.destroy()
