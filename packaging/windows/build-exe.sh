#!/bin/bash
# build-exe.sh — Build a standalone SSH Client Manager .exe bundle for Windows
#
# This script MUST be run inside an MSYS2 MINGW64 shell:
#   1. Open "MSYS2 MINGW64" from the Start Menu
#   2. cd /path/to/ssh-client-manager
#   3. ./packaging/windows/build-exe.sh
#
# Produces: dist/SSHClientManager/ (directory with .exe and all DLLs)

set -e

echo "Building SSH Client Manager for Windows..."

# ── Verify environment ────────────────────────────────────────────────────────
if [ -z "$MSYSTEM" ] || [ "$MSYSTEM" != "MINGW64" ]; then
    echo "Error: This script must be run in an MSYS2 MINGW64 shell."
    echo "Open 'MSYS2 MINGW64' from the Start Menu, then run this script."
    exit 1
fi

if [ ! -f "run.py" ] || [ ! -d "src" ]; then
    echo "Error: Run this script from the project root directory."
    echo "  cd /path/to/ssh-client-manager && ./packaging/windows/build-exe.sh"
    exit 1
fi

# ── Check required packages ──────────────────────────────────────────────────
echo ""
echo "Checking MSYS2 MINGW64 packages..."

MISSING=()

check_pkg() {
    pacman -Q "$1" &>/dev/null || MISSING+=("$1")
}

check_pkg mingw-w64-x86_64-python
check_pkg mingw-w64-x86_64-python-gobject
check_pkg mingw-w64-x86_64-python-cairo
check_pkg mingw-w64-x86_64-gtk4
check_pkg mingw-w64-x86_64-libadwaita
check_pkg mingw-w64-x86_64-vte3
check_pkg mingw-w64-x86_64-python-cryptography
check_pkg mingw-w64-x86_64-python-paramiko
check_pkg mingw-w64-x86_64-python-psutil
check_pkg mingw-w64-x86_64-python-pyinstaller

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "Missing packages: ${MISSING[*]}"
    echo ""
    echo "Install them with:"
    echo "  pacman -S ${MISSING[*]}"
    exit 1
fi

echo "All MSYS2 packages present."

# ── Detect paths ─────────────────────────────────────────────────────────────
MINGW_PREFIX="/mingw64"
MINGW_BIN="$MINGW_PREFIX/bin"
MINGW_LIB="$MINGW_PREFIX/lib"
MINGW_SHARE="$MINGW_PREFIX/share"
GIR_DIR="$MINGW_LIB/girepository-1.0"

echo "MINGW prefix : $MINGW_PREFIX"
echo "GIR typelibs : $GIR_DIR"

# ── Collect DLLs ─────────────────────────────────────────────────────────────
echo ""
echo "Collecting GTK4 / libadwaita / VTE DLLs..."

# List of DLL patterns needed for the GTK4+libadwaita+VTE stack
DLL_PATTERNS=(
    "libgtk-4-*.dll"
    "libadwaita-1-*.dll"
    "libvte-2.91-gtk4-*.dll"
    "libgdk_pixbuf-2.0-*.dll"
    "libgobject-2.0-*.dll"
    "libglib-2.0-*.dll"
    "libgio-2.0-*.dll"
    "libgmodule-2.0-*.dll"
    "libpango-1.0-*.dll"
    "libpangocairo-1.0-*.dll"
    "libpangowin32-1.0-*.dll"
    "libcairo-2.dll"
    "libcairo-gobject-2.dll"
    "libharfbuzz-*.dll"
    "libfribidi-*.dll"
    "libgraphene-1.0-*.dll"
    "libintl-*.dll"
    "libffi-*.dll"
    "libepoxy-*.dll"
    "libpixman-1-*.dll"
    "libpng16-*.dll"
    "libjpeg-*.dll"
    "libtiff-*.dll"
    "libpcre2-8-*.dll"
    "zlib1.dll"
    "libxml2-*.dll"
    "libiconv-*.dll"
    "libssp-*.dll"
    "libwinpthread-*.dll"
    "libstdc++-*.dll"
    "libgcc_s_seh-*.dll"
)

# Build --add-binary arguments
ADD_BINARIES=""
for pat in "${DLL_PATTERNS[@]}"; do
    for dll in $MINGW_BIN/$pat; do
        if [ -f "$dll" ]; then
            ADD_BINARIES="$ADD_BINARIES --add-binary $(cygpath -w "$dll");."
        fi
    done
done

# ── Collect GI typelibs ──────────────────────────────────────────────────────
echo "Collecting GObject Introspection typelibs..."
ADD_DATA="--add-data $(cygpath -w "$GIR_DIR");girepository-1.0"

# ── Collect shared data (schemas, icons, gtk assets) ─────────────────────────
echo "Collecting shared data..."

# GLib schemas
SCHEMAS_DIR="$MINGW_SHARE/glib-2.0/schemas"
if [ -d "$SCHEMAS_DIR" ]; then
    ADD_DATA="$ADD_DATA --add-data $(cygpath -w "$SCHEMAS_DIR");share/glib-2.0/schemas"
fi

# Adwaita icon theme
ADWAITA_ICONS="$MINGW_SHARE/icons/Adwaita"
if [ -d "$ADWAITA_ICONS" ]; then
    ADD_DATA="$ADD_DATA --add-data $(cygpath -w "$ADWAITA_ICONS");share/icons/Adwaita"
fi

# GTK4 assets
GTK4_SHARE="$MINGW_SHARE/gtk-4.0"
if [ -d "$GTK4_SHARE" ]; then
    ADD_DATA="$ADD_DATA --add-data $(cygpath -w "$GTK4_SHARE");share/gtk-4.0"
fi

# libadwaita share data
LIBADWAITA_SHARE="$MINGW_SHARE/libadwaita-1"
if [ -d "$LIBADWAITA_SHARE" ]; then
    ADD_DATA="$ADD_DATA --add-data $(cygpath -w "$LIBADWAITA_SHARE");share/libadwaita-1"
fi

# GDK-Pixbuf loaders
PIXBUF_DIR="$MINGW_LIB/gdk-pixbuf-2.0/2.10.0"
if [ -d "$PIXBUF_DIR" ]; then
    ADD_DATA="$ADD_DATA --add-data $(cygpath -w "$PIXBUF_DIR");lib/gdk-pixbuf-2.0/2.10.0"
fi

# Application source
ADD_DATA="$ADD_DATA --add-data src;src"

# ── Run PyInstaller ──────────────────────────────────────────────────────────
echo ""
echo "Running PyInstaller..."

python -m PyInstaller \
    --clean \
    --noconfirm \
    --name SSHClientManager \
    --windowed \
    --icon packaging/windows/ssh-client-manager.ico \
    --runtime-hook packaging/windows/hook-gtk_runtime_win.py \
    $ADD_BINARIES \
    $ADD_DATA \
    --hidden-import gi \
    --hidden-import gi._gi \
    --hidden-import gi._gi_cairo \
    --hidden-import gi.repository.Gtk \
    --hidden-import gi.repository.Adw \
    --hidden-import gi.repository.Vte \
    --hidden-import gi.repository.GLib \
    --hidden-import gi.repository.Gio \
    --hidden-import gi.repository.GObject \
    --hidden-import gi.repository.Gdk \
    --hidden-import gi.repository.Pango \
    --hidden-import gi.repository.GdkPixbuf \
    --hidden-import cairo \
    --hidden-import paramiko \
    --hidden-import cryptography \
    --hidden-import cryptography.fernet \
    --hidden-import cryptography.hazmat.primitives \
    --hidden-import psutil \
    --collect-submodules gi \
    run.py

# ── Post-build check ────────────────────────────────────────────────────────
if [ ! -f "dist/SSHClientManager/SSHClientManager.exe" ]; then
    echo "Build failed! Executable not found."
    exit 1
fi

echo ""
echo "Build successful!"
echo "  Output: dist/SSHClientManager/"
echo "  Exe   : dist/SSHClientManager/SSHClientManager.exe"
echo ""
echo "To create a Windows installer, install NSIS and run:"
echo "  makensis packaging/windows/ssh-client-manager.nsi"
