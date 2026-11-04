# Chloe Photoviewer

Small cross platform image viewer (Windows, Linux, macOS) written in Python / Tkinter.

## Install and run

    pip install -r requirements.txt
    python chloe_photoviewer.py [image or folder]

* Windows: use `pythonw chloe_photoviewer.py photo.jpg` to start without a console window.
* Debian / Ubuntu: `sudo apt install python3-tk python3-pil.imagetk`
* Fedora: `sudo dnf install python3-tkinter python3-pillow-tk`
* HEIC / AVIF files: `pip install pillow-heif` and add
  `from pillow_heif import register_heif_opener; register_heif_opener()` near the Pillow import.

## Mouse (FastStone style)

| Action | Result |
|---|---|
| click | zoom to 100% at the clicked point, drag to move around |
| click again | back to fit-to-window |
| click and hold | 200% while the button is down. The mouse position on the fitted picture picks the same relative spot of the zoomed picture (mouse at the bottom left of the picture = bottom left of the original). Release = back to fit |
| wheel | does nothing by default; Settings > General: previous / next image or zoom |
| right click | context menu |
| move the mouse | on-screen buttons appear: prev, next, rotate left/right, fit, 1:1, full screen, settings |

The click zoom (100%), hold zoom (200%) and hold delay (100 ms) can be changed in Settings > General.

## Keys

Left / Right / Space / BackSpace / PageUp / PageDown = previous / next, Home / End = first / last,
R / L = rotate right / left, F or 0 = fit, 1 / 2 = 100% / 200%, + / - = zoom, I = info,
F11 = full screen (Esc leaves), Ctrl+O = open, Ctrl+, = settings, **Ctrl+W = close** (Ctrl+Q too).

## Starting, "Open with" in Explorer

* Started without a file, it reopens the last viewed image.
* Windows: double click `chloe_viewer.pyw` (no console window). Starting `chloe_photoviewer.py` with
  python.exe also restarts itself without console (set `CHLOE_KEEP_CONSOLE=1` to keep it for debugging).
* Windows Explorer > right click > Open with: run `register_windows.bat` once (or `python register_windows.py`).
  Chloe Photoviewer then appears in Open with and in "Choose another app". `--remove` undoes it. Re-run it if you move the folder.
* Linux: run `./install_linux.sh` once.

## Rotation

Rotating only changes the view. No file is ever written or modified by the program.
The rotation is remembered per image while you stay in the same folder.

## Context menu

Open with > (your programs), Open file location, Copy file path / name /
folder path, Properties, rotate, zoom, full screen, previous / next, Open image, Settings, Close.

## Settings (Ctrl+,)

* **General** - zoom values, wheel behavior, show info / on-screen buttons, enlarge small images.
* **Programs** - add / edit / remove / reorder the programs of "Open with".
  Command: `{file}` = image path, `{dir}` = its folder; without `{file}` the path is added at the end.
  Examples: `gimp "{file}"`, `"C:\Program Files\Foo\foo.exe" "{file}"`, `flatpak run org.gimp.GIMP {file}`.
* **Appearance** - language, theme, and the theme editor (live preview).

Language and theme are used from the next start.

## Languages and themes

Same system as the Firmware Manager: `lang/<code>.json` and `theme/<code>.json`.
Drop in a new file (copy `lang/en.json`, change `language.name`, translate the values) and it shows up in
Settings > Appearance. Missing entries fall back to English. A theme with an empty palette keeps the
system look (`dark_os`, `light_os`); the other themes recolor everything.
Edit a theme in Settings > Appearance > Edit theme..., or by hand while the program is closed.

## Files

    chloe_photoviewer.py   the program
    chloe_viewer.pyw       launcher without console (Windows)
    register_windows.py/.bat, install_linux.sh   add to the Open with list
    chloe_lang.py          language loading, t("key")
    chloe_theme.py         theme loading / applying / editor
    lang/  theme/          language and theme files

Config is saved per user:
Windows `%APPDATA%\ChloePhotoviewer`, Linux `~/.config/chloe-photoviewer`, macOS `~/Library/Application Support/ChloePhotoviewer`.
