#!/usr/bin/env python3
"""
install.py - Add Chloe Photoviewer to the "Open with" list of the system.

    python install.py            install for the current user (no admin / root needed)
    python install.py --remove   undo it

Windows  registry keys under HKCU\\Software\\Classes (Open with + Choose another app)
Linux    ~/.local/share/applications/chloe-photoviewer.desktop
macOS    ~/Applications/Chloe Photoviewer.app (small launcher made with osacompile)

The program is started with the Python that runs this script; run it again after
moving the folder or switching Python. File types come from IMAGE_EXTS in
chloe_photoviewer.py.
"""

import ast
import os
import plistlib
import shlex
import shutil
import subprocess
import sys

APP_NAME = "Chloe Photoviewer"
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "chloe_photoviewer.py")

MIME_TYPES = [
    "image/jpeg", "image/png", "image/gif", "image/bmp", "image/webp", "image/tiff",
    "image/x-icon", "image/vnd.microsoft.icon", "image/x-portable-pixmap",
    "image/x-portable-graymap", "image/x-portable-bitmap", "image/x-tga",
    "image/avif", "image/heic", "image/heif",
]
"""Linux MIME types shown in the Open with list."""


def image_exts():
    """@return sorted file extensions (".jpg", ...) read from IMAGE_EXTS in chloe_photoviewer.py"""
    with open(SCRIPT, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "IMAGE_EXTS" for t in node.targets):
            return sorted(ast.literal_eval(node.value))
    sys.exit("IMAGE_EXTS not found in " + SCRIPT)


# -- Windows ------------------------------------------------------------------ #

WIN_PROGID = "ChloePhotoviewer.Image"
"""ProgID under Software\\Classes; its shell\\open\\FriendlyAppName is the name in Open with."""
WIN_OLD_KEY = "chloe_photoviewer.exe"
"""Key / exe copy of an earlier version of this script (removed if found)."""


def _win_delete_tree(winreg, root, path):
    """Delete a registry key with everything below it (missing key: nothing)."""
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    sub = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _win_delete_tree(winreg, root, path + "\\" + sub)
        winreg.DeleteKey(root, path)
    except OSError:
        pass


def windows(remove):
    """Register / unregister in the current user's registry."""
    import ctypes
    import winreg
    root = winreg.HKEY_CURRENT_USER
    classes = "Software\\Classes\\"
    exts = image_exts()

    # clean up: an earlier version of this script, and the previous registration
    old_copy = os.path.join(os.path.dirname(sys.executable), WIN_OLD_KEY)
    if os.path.exists(old_copy):
        try:
            os.remove(old_copy)
        except OSError:
            pass
    _win_delete_tree(winreg, root, classes + "Applications\\" + WIN_OLD_KEY)
    _win_delete_tree(winreg, root, classes + WIN_PROGID)
    for ext in exts:
        _win_delete_tree(winreg, root, classes + ext + "\\OpenWithList\\" + WIN_OLD_KEY)
        try:
            with winreg.OpenKey(root, classes + ext + "\\OpenWithProgids", 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, WIN_PROGID)
        except OSError:
            pass

    if not remove:
        py = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(py):
            py = sys.executable
        with winreg.CreateKey(root, classes + WIN_PROGID) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, APP_NAME)
        with winreg.CreateKey(root, classes + WIN_PROGID + "\\shell\\open") as key:
            winreg.SetValueEx(key, "FriendlyAppName", 0, winreg.REG_SZ, APP_NAME)
        with winreg.CreateKey(root, classes + WIN_PROGID + "\\shell\\open\\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, '"%s" "%s" "%%1"' % (py, SCRIPT))
        for ext in exts:
            with winreg.CreateKey(root, classes + ext + "\\OpenWithProgids") as key:
                winreg.SetValueEx(key, WIN_PROGID, 0, winreg.REG_NONE, b"")
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)   # file associations changed


# -- Linux -------------------------------------------------------------------- #


def linux(remove):
    """Write / delete the .desktop file of the current user."""
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    folder = os.path.join(data, "applications")
    path = os.path.join(folder, "chloe-photoviewer.desktop")
    if remove:
        if os.path.exists(path):
            os.remove(path)
    else:
        os.makedirs(folder, exist_ok=True)
        exe = '"%s" "%s" %%f' % (sys.executable, SCRIPT)
        with open(path, "w", encoding="utf-8") as f:
            f.write("[Desktop Entry]\n"
                    "Type=Application\n"
                    "Name=%s\n"
                    "Comment=Small image viewer\n"
                    "Exec=%s\n"
                    "Terminal=false\n"
                    "Categories=Graphics;Viewer;\n"
                    "MimeType=%s;\n" % (APP_NAME, exe, ";".join(MIME_TYPES)))
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", folder], check=False)


# -- macOS -------------------------------------------------------------------- #


def _applescript_text(text):
    """Escape text for an AppleScript string literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def macos(remove):
    """Build / delete ~/Applications/Chloe Photoviewer.app."""
    app = os.path.join(os.path.expanduser("~"), "Applications", APP_NAME + ".app")
    lsregister = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/"
                  "LaunchServices.framework/Support/lsregister")
    if os.path.isdir(app):
        shutil.rmtree(app)
    if not remove:
        cmd = "%s %s" % (shlex.quote(sys.executable), shlex.quote(SCRIPT))
        src = ('on run\n'
               '\tdo shell script "%s >/dev/null 2>&1 &"\n'
               'end run\n'
               'on open theFiles\n'
               '\trepeat with f in theFiles\n'
               '\t\tdo shell script "%s " & quoted form of POSIX path of f & " >/dev/null 2>&1 &"\n'
               '\tend repeat\n'
               'end open\n') % (_applescript_text(cmd), _applescript_text(cmd))
        os.makedirs(os.path.dirname(app), exist_ok=True)
        tmp = os.path.join(HERE, "_launcher.applescript")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(src)
        try:
            subprocess.run(["osacompile", "-o", app, tmp], check=True)
        finally:
            os.remove(tmp)
        plist_path = os.path.join(app, "Contents", "Info.plist")
        with open(plist_path, "rb") as f:
            plist = plistlib.load(f)
        plist["CFBundleIdentifier"] = "org.chloe.photoviewer.launcher"
        plist["CFBundleName"] = APP_NAME
        plist["LSUIElement"] = True              # no dock icon for the launcher itself
        plist["CFBundleDocumentTypes"] = [{
            "CFBundleTypeName": "Image",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Alternate",        # listed in Open With, never the default
            "LSItemContentTypes": ["public.image"],
        }]
        with open(plist_path, "wb") as f:
            plistlib.dump(plist, f)
    if os.path.exists(lsregister):
        subprocess.run([lsregister, "-f", app], check=False)


def main(argv):
    """Install (default) or remove (--remove) for the platform this runs on."""
    remove = "--remove" in argv
    if not os.path.isfile(SCRIPT):
        sys.exit("chloe_photoviewer.py not found next to the install folder: " + SCRIPT)
    if os.name == "nt":
        windows(remove)
    elif sys.platform == "darwin":
        macos(remove)
    else:
        linux(remove)
    print("%s %s." % (APP_NAME, "removed from Open with" if remove else "added to Open with"))


if __name__ == "__main__":
    main(sys.argv[1:])
