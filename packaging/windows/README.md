# Windows Packaging — SSH Client Manager

Two installation methods are provided:

| Method | Best for | Output |
|--------|----------|--------|
| **`install.ps1`** | Quick local install, development | Files placed into `%LOCALAPPDATA%\ssh-client-manager` |
| **`build-exe.sh`** + **NSIS** | Distribution, sharing | Standalone `.exe` bundle + installer |

---

## Files

```
packaging/windows/
├── install.ps1                 # Direct install from source (recommended for quick setup)
├── build-exe.sh                # Build standalone .exe bundle via PyInstaller (run in MSYS2)
├── hook-gtk_runtime_win.py     # PyInstaller runtime hook for Windows
├── ssh-client-manager.nsi      # NSIS installer definition
├── ssh-client-manager.ico      # App icon — add your own .ico here (see below)
└── README.md                   # This file
```

---

## Prerequisites — MSYS2

SSH Client Manager uses GTK4, libadwaita, and VTE, which are available on Windows
through [MSYS2](https://www.msys2.org/). MSYS2 is required for **both** installation methods.

### 1 — Install MSYS2

Download and install from: **https://www.msys2.org/**

Use the default install path (`C:\msys64`). After installation, open the
**"MSYS2 MINGW64"** shell from the Start Menu and update the package database:

```bash
pacman -Syu
```

### 2 — Install GTK4 stack and Python packages

In the **MSYS2 MINGW64** shell:

```bash
pacman -S \
    mingw-w64-x86_64-python \
    mingw-w64-x86_64-python-gobject \
    mingw-w64-x86_64-python-cairo \
    mingw-w64-x86_64-gtk4 \
    mingw-w64-x86_64-libadwaita \
    mingw-w64-x86_64-vte3 \
    mingw-w64-x86_64-python-cryptography \
    mingw-w64-x86_64-python-paramiko \
    mingw-w64-x86_64-python-psutil
```

### 3 — Verify the GTK stack

In the **MSYS2 MINGW64** shell:

```bash
python3 -c "
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Vte', '3.91')
from gi.repository import Gtk, Adw, Vte
print('GTK', Gtk.get_major_version(), '— OK')
print('Adw — OK')
print('VTE — OK')
"
```

---

## Method 1 — Quick Install from Source (`install.ps1`)

Installs directly from the cloned repository without building a standalone bundle.
Ideal for development machines or personal use.

### 1. Install MSYS2 and packages

Follow the [Prerequisites](#prerequisites--msys2) section above.

### 2. Run the installer

From the **project root** in PowerShell:

```powershell
.\packaging\windows\install.ps1
```

This will:
- Copy application files to `%LOCALAPPDATA%\ssh-client-manager`
- Create a launcher batch file that invokes MSYS2 Python
- Add a Start Menu shortcut
- Add the install directory to your user PATH

### 3. Launch

```powershell
ssh-client-manager
# or find "SSH Client Manager" in the Start Menu
```

### Uninstall

```powershell
.\packaging\windows\install.ps1 -Uninstall
```

---

## Method 2 — Build a Standalone Bundle (`build-exe.sh`)

Produces a self-contained directory with `SSHClientManager.exe` and all required
DLLs bundled. Optionally creates a Windows installer using NSIS.

### 1. Install build tools

In the **MSYS2 MINGW64** shell, install PyInstaller:

```bash
pacman -S mingw-w64-x86_64-python-pyinstaller
```

For building the Windows installer, install [NSIS](https://nsis.sourceforge.io/)
on the host Windows system.

### 2. Build the bundle

In the **MSYS2 MINGW64** shell, from the project root:

```bash
chmod +x packaging/windows/build-exe.sh
./packaging/windows/build-exe.sh
```

Output: `dist/SSHClientManager/SSHClientManager.exe`

### 3. Test the bundle

```bash
./dist/SSHClientManager/SSHClientManager.exe
```

### 4. Build the installer (optional)

With [NSIS](https://nsis.sourceforge.io/) installed, run from a Windows command
prompt or PowerShell:

```powershell
makensis packaging\windows\ssh-client-manager.nsi
```

Output: `dist\SSHClientManager-1.0.0-Setup.exe`

### 5. Install / Uninstall

Run `SSHClientManager-1.0.0-Setup.exe` — it will:
- Install to `%LOCALAPPDATA%\ssh-client-manager`
- Create Start Menu and Desktop shortcuts
- Register in Add/Remove Programs

To uninstall: use **Add/Remove Programs** in Windows Settings, or run the
`Uninstall.exe` from the install directory.

---

## Updating the Version

1. Edit `packaging/windows/ssh-client-manager.nsi` — update `APPVERSION`:

```nsis
!define APPVERSION   "1.1.0"
```

2. Rebuild with `./packaging/windows/build-exe.sh` and then `makensis`.

---

## Adding an App Icon

PyInstaller and NSIS require a `.ico` file. Place it at:

```
packaging/windows/ssh-client-manager.ico
```

### Create from a PNG

Use ImageMagick (available via MSYS2: `pacman -S mingw-w64-x86_64-imagemagick`):

```bash
magick icon.png -define icon:auto-resize=256,128,64,48,32,16 \
    packaging/windows/ssh-client-manager.ico
```

Or use an online converter. The `.ico` should contain 16×16, 32×32, 48×48,
64×64, 128×128, and 256×256 variants.

If no `.ico` file is present, the build still succeeds but the app will use the
default Windows executable icon.

---

## How the Bundle Works

### What gets bundled

The build script (`build-exe.sh`) copies the following into `dist/SSHClientManager/`:

| Resource | Bundle location |
|----------|----------------|
| GTK4 / libadwaita / VTE DLLs | Root directory (alongside `.exe`) |
| GI typelibs (`*.typelib`) | `girepository-1.0/` |
| GLib schemas | `share/glib-2.0/schemas/` |
| Adwaita icon theme | `share/icons/Adwaita/` |
| GTK-4.0 assets | `share/gtk-4.0/` |
| libadwaita share data | `share/libadwaita-1/` |
| GDK-Pixbuf loaders | `lib/gdk-pixbuf-2.0/2.10.0/` |
| App source (`src/`) | `src/` |

### Runtime hook (`hook-gtk_runtime_win.py`)

Before any application code runs, the runtime hook sets:

| Variable | Value inside bundle |
|----------|-------------------|
| `GI_TYPELIB_PATH` | Bundled typelibs directory |
| `GSETTINGS_SCHEMA_DIR` | Bundled GLib schemas directory |
| `XDG_DATA_DIRS` | Bundled `share/` directory |
| `GDK_PIXBUF_MODULEDIR` | Bundled pixbuf loaders |
| `GTK_EXE_PREFIX` | Bundle root |
| `PATH` | Prepends OpenSSH and System32 directories |
| `XDG_CONFIG_HOME` | `%APPDATA%\ssh-client-manager` |
| `XDG_DATA_HOME` | `%LOCALAPPDATA%\ssh-client-manager` |
| `XDG_CACHE_HOME` | `%LOCALAPPDATA%\ssh-client-manager\cache` |

---

## System Dependency Reference (MSYS2 MINGW64)

| Package | Provides |
|---------|---------|
| `mingw-w64-x86_64-python` | Python 3 runtime |
| `mingw-w64-x86_64-python-gobject` | PyGObject — Python GObject/GTK bindings |
| `mingw-w64-x86_64-python-cairo` | PyCairo — Cairo integration for Python |
| `mingw-w64-x86_64-gtk4` | GTK 4 libraries and typelibs |
| `mingw-w64-x86_64-libadwaita` | libadwaita (GNOME HIG widgets) |
| `mingw-w64-x86_64-vte3` | VTE terminal widget (GTK4 variant) |
| `mingw-w64-x86_64-python-cryptography` | AES encryption for credential storage |
| `mingw-w64-x86_64-python-paramiko` | SSH protocol library |
| `mingw-w64-x86_64-python-psutil` | Process monitoring utilities |
| `mingw-w64-x86_64-python-pyinstaller` | PyInstaller (build tool only) |

No `sshpass`, no `expect`, no Windows Credential Manager required.

---

## Troubleshooting

### MSYS2 MINGW64 shell not available

Ensure you installed MSYS2 correctly. The **MINGW64** shell is a separate shortcut
from the plain **MSYS2** shell. The MINGW64 variant uses native Windows libraries
which is required for GTK4 to work properly.

### `vte3` package not found

```bash
pacman -Ss vte
# Install the one that provides libvte-2.91-gtk4
pacman -S mingw-w64-x86_64-vte3
```

### Application starts but window is blank / unstyled

GTK theme data is missing. Ensure `mingw-w64-x86_64-libadwaita` is installed
and the Adwaita icon theme is present.

In the MSYS2 MINGW64 shell:

```bash
pacman -S mingw-w64-x86_64-adwaita-icon-theme
```

### SSH connections fail with "No such file or directory"

The app uses `ssh.exe` which should be available via Windows OpenSSH. Verify:

```powershell
where ssh.exe
# Should return: C:\Windows\System32\OpenSSH\ssh.exe
```

If not found, enable OpenSSH in Windows Settings → Apps → Optional Features →
**OpenSSH Client**.

### PyInstaller build fails with "module not found"

Ensure you are running the build script in the **MSYS2 MINGW64** shell, not in
PowerShell or CMD. The MINGW64 environment provides all required Python packages.

### Bundled .exe crashes immediately

Run from a terminal to see error output:

```powershell
cd dist\SSHClientManager
.\SSHClientManager.exe
```

Common causes:
- Missing DLL: add the missing DLL pattern to `build-exe.sh`
- Missing typelib: check `dist\SSHClientManager\girepository-1.0\` for required `.typelib` files

---

## Architecture Notes

| Concern | Detail |
|---------|--------|
| Architecture | x86_64 (64-bit) — MSYS2 MINGW64 builds are 64-bit only |
| Windows version | Windows 10 1809+ recommended (for built-in OpenSSH) |
| Credential storage | AES-encrypted locally by `src/credential_store.py`, no Windows Credential Manager |
| SSH client | Uses Windows built-in OpenSSH (`C:\Windows\System32\OpenSSH\ssh.exe`) |
| Config location | `%APPDATA%\ssh-client-manager\` (bundled) or `~/.config/ssh-client-manager/` (source install) |
