# hook-gtk_runtime_win.py
#
# PyInstaller runtime hook for SSH Client Manager on Windows.
# Executes inside the bundled app before any application code runs.
# Sets up environment variables required for GTK4 / libadwaita / VTE
# to locate their bundled resources.

import os
import sys
from pathlib import Path


def _setup_windows_env() -> None:
    # ── Locate bundle root ────────────────────────────────────────────────────
    if getattr(sys, "frozen", False):
        bundle_root = Path(sys._MEIPASS)
    else:
        bundle_root = Path(sys.executable).parent

    # ── GI typelib path ───────────────────────────────────────────────────────
    gi_dir = bundle_root / "girepository-1.0"
    if gi_dir.is_dir():
        os.environ["GI_TYPELIB_PATH"] = str(gi_dir)

    # ── GSettings schemas ─────────────────────────────────────────────────────
    schemas_dir = bundle_root / "share" / "glib-2.0" / "schemas"
    if schemas_dir.is_dir():
        os.environ["GSETTINGS_SCHEMA_DIR"] = str(schemas_dir)

    # ── XDG data dirs (icons, themes, gtk-4.0 assets) ────────────────────────
    share_dir = bundle_root / "share"
    if share_dir.is_dir():
        os.environ["XDG_DATA_DIRS"] = str(share_dir)

    # ── GDK-Pixbuf loaders ───────────────────────────────────────────────────
    pixbuf_dir = bundle_root / "lib" / "gdk-pixbuf-2.0" / "2.10.0"
    if pixbuf_dir.is_dir():
        loaders_dir = pixbuf_dir / "loaders"
        loaders_cache = pixbuf_dir / "loaders.cache"
        if loaders_dir.is_dir():
            os.environ["GDK_PIXBUF_MODULEDIR"] = str(loaders_dir)
        if loaders_cache.is_file():
            os.environ["GDK_PIXBUF_MODULE_FILE"] = str(loaders_cache)

    # ── GTK module path ──────────────────────────────────────────────────────
    os.environ["GTK_EXE_PREFIX"] = str(bundle_root)

    # ── PATH — ensure ssh.exe is reachable ────────────────────────────────────
    # Windows OpenSSH is typically at C:\Windows\System32\OpenSSH
    system_paths = [
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "OpenSSH"),
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"),
        str(bundle_root),
    ]
    current_path = os.environ.get("PATH", "")
    existing = set(current_path.split(";"))
    extra = [p for p in system_paths if p not in existing]
    if extra:
        os.environ["PATH"] = ";".join(extra) + ";" + current_path

    # ── XDG-style user dirs (for config and credential storage) ───────────────
    appdata = os.environ.get("APPDATA", os.path.join(os.path.expanduser("~"), "AppData", "Roaming"))
    local_appdata = os.environ.get("LOCALAPPDATA", os.path.join(os.path.expanduser("~"), "AppData", "Local"))

    os.environ.setdefault("XDG_CONFIG_HOME", os.path.join(appdata, "ssh-client-manager"))
    os.environ.setdefault("XDG_DATA_HOME", os.path.join(local_appdata, "ssh-client-manager"))
    os.environ.setdefault("XDG_CACHE_HOME", os.path.join(local_appdata, "ssh-client-manager", "cache"))

    for env_key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        os.makedirs(os.environ[env_key], exist_ok=True)


if sys.platform == "win32":
    _setup_windows_env()
