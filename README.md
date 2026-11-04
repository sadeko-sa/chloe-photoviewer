# Chloe Photoviewer

Small cross platform image viewer (Windows, Linux, macOS) written in Python / Tkinter.

## Install and run

    pip install -r requirements.txt
    python chloe_photoviewer.py [image or folder]

* Windows: use `pythonw chloe_photoviewer.py photo.jpg` to start without a console window.
* Debian / Ubuntu: `sudo apt install python3-tk python3-pil.imagetk`
* Fedora: `sudo dnf install python3-tkinter python3-pillow-tk`
* Drag and drop of an image or folder onto the window (optional): `pip install tkinterdnd2`. Without it the program works as before.
* HEIC / AVIF files: `pip install pillow-heif` and add
  `from pillow_heif import register_heif_opener; register_heif_opener()` near the Pillow import.

## Mouse (FastStone style)

| Action | Result |
|---|---|
| click | zoom to 100% at the clicked point, drag to move around |
| click again | back to fit-to-window |
| click and hold | 200% while the button is down. The mouse position on the fitted picture picks the same relative spot of the zoomed picture (mouse at the bottom left of the picture = bottom left of the original). Release = back to fit |
| wheel | does nothing by default; Settings > General: previous / next image or zoom |
| middle click on a tab | close the tab |
| right click | context menu |
| move the mouse | on-screen buttons appear: prev, next, rotate left/right, fit width/height, 50%, 75%, 100%, auto rotate (off / landscape / portrait), new window, settings |

The click zoom (100%), hold zoom (200%) and hold delay (100 ms) can be changed in Settings > General.

## Keys

Left / Right / Space / BackSpace / PageUp / PageDown = previous / next, Home / End = first / last,
R / L = rotate right / left, F = fit width / height (toggle), 0 = fit to window, 1 / 2 = 100% / 200%, + / - = zoom, I = info,
Ctrl+Tab / Ctrl+Shift+Tab = next / previous tab, Ctrl+N = new window, F5 = quick save (see Sessions), F11 = full screen (Esc leaves),
Ctrl+O = open, Ctrl+, = settings, Ctrl+W = close tab (the last tab closes the window), Ctrl+Q = close window.

## Starting, "Open with" in Explorer

* Started without a file, it reopens the last viewed image (one image, not the whole previous set of tabs; use a session to restore several).
* Windows: double click `chloe_viewer.pyw` (no console window). Starting `chloe_photoviewer.py` with
  python.exe also restarts itself without console (set `CHLOE_KEEP_CONSOLE=1` to keep it for debugging).
* "Open with": run `install/install_windows.bat`, `install/install_linux.sh` or `install/install_mac.command` once
  (macOS: if it will not start, run `chmod +x install/*.command` first).
  Chloe Photoviewer then appears in the Open with list (Windows: also in "Choose another app"; macOS: `~/Applications/Chloe Photoviewer.app`).
  It never becomes the default viewer. To undo it run the matching `uninstall_*` script.
  Re-run the install script if you move the folder or change Python.

## Single instance

Only one program process runs. Starting it again (Open with, double click, command line) sends the file to the
running program, which opens it as a new tab of the window used last (or just selects its tab if it is already open)
and exits the new process, so all windows share one memory footprint.
The program ends when the last window is closed.
Turn it off in Settings > General. It uses a local port (127.0.0.1:47615); if that port is blocked the
program simply runs as a normal separate window.

## Tabs and windows

Every image opens as a tab of the window; Open with, double click, drag and drop, Ctrl+O and sessions all add tabs.
An image that is already open, in any window, just gets its tab selected. The tab bar only shows with two or more tabs.
Each tab keeps its own zoom, position and rotation. Ctrl+W closes the tab, the last tab closes the window, middle click on a tab closes it.

**Ctrl+N** (or the on-screen button / context menu) moves the current tab into a new window; with a single tab it opens an empty
window. Windows do not share tabs. A session holds the tabs of the window it was saved from, but saved sessions are available to every window. Each window saves its own size and position when it closes (the last one closed wins).

## Rotation

Rotating only changes the view. No file is ever written or modified by the program.
The rotation is remembered per image while you stay in the same folder.

## Auto rotate

The toolbar button cycles off / landscape / portrait: landscape turns portrait pictures sideways, portrait does the opposite.
The direction (clockwise or counter-clockwise, shown by the arrow on the button) is set in the context menu > Auto rotate.

## Sessions

A session is a list of images; opening it adds them as tabs of the current window. Only the images are saved (no zoom, no window
position), and nothing is saved automatically. A session always holds the tabs of ONE window, never of all windows.

* **F5 quick save** - saves the tabs of this window as the session *Quick save (F5)* (overwrites the previous one). Open it from
  Context menu > Session > Open session.
* **Context menu > Session** - Quick save, *Save session as...* (opens a dialog with the open images and a suggested
  name: date + folder names; you can rename, add, remove and reorder images before saving), *Open session >* (the saved list) and *Manage sessions...*.
* **Settings > Sessions** - add / edit / remove / reorder your sessions. Edit = rename, add or remove images, change their order.
* Files that no longer exist are skipped and the number of skipped files is shown.

Sessions are stored in the config file (`sessions`), so they are easy to back up or edit by hand while the program is closed.

## Context menu

Open with > (your programs), Open file location, Copy file path / name / folder path, Properties,
Rotate right / left / Reset rotation, Fit width / height, Fit to window, 100%, 200%, Auto rotate >
(off / landscape / portrait, clockwise / counter-clockwise), Enlarge small images, Show info, Full screen,
Previous / Next, Session > (Quick save, Save session as..., Open session >, Manage sessions...),
New window, Open image, Settings, Close (tab).

## Settings (Ctrl+,)

* **General** - zoom values, wheel behavior, show info / on-screen buttons, enlarge small images, single instance.
* **Programs** - add / edit / remove / reorder the programs of "Open with".
  Command: `{file}` = image path, `{dir}` = its folder; without `{file}` the path is added at the end.
  Examples: `gimp "{file}"`, `"C:\Program Files\Foo\foo.exe" "{file}"`, `flatpak run org.gimp.GIMP {file}`.
* **Sessions** - add / edit / remove / reorder the saved sessions.
* **Appearance** - language, theme, and the theme editor (live preview).
* **About** - version and the Python / Pillow / Tk versions in use.

Language and theme are used from the next start.

## Languages and themes

Both are plain files: `lang/<code>.json` and `theme/<code>.json`.
Drop in a new file (copy `lang/en.json`, change `language.name`, translate the values) and it shows up in
Settings > Appearance. Missing entries fall back to English. A theme with an empty palette keeps the
system look (`dark_os`, `light_os`); the other themes recolor everything.
Edit a theme in Settings > Appearance > Edit theme..., or by hand while the program is closed.

## Files

    chloe_photoviewer.py   the program
    chloe_viewer.pyw       launcher without console (Windows)
    install/               install_* / uninstall_* scripts for windows, linux, mac (all call install.py)
    chloe_lang.py          language loading, t("key")
    chloe_theme.py         theme loading / applying / editor
    lang/  theme/          language and theme files

Config is saved per user:
Windows `%APPDATA%\ChloePhotoviewer`, Linux `~/.config/chloe-photoviewer`, macOS `~/Library/Application Support/ChloePhotoviewer`.
