# BetFinder App Build Guide

This guide details how to package the BetFinder application for distribution on Windows, macOS, and Linux.

## 🚀 Quick Start

| Task                | Command            | Description                                                               |
| ------------------- | ------------------ | ------------------------------------------------------------------------- |
| **Build App**       | `uv run build`     | Creates a portable, standalone app folder/bundle in `dist/`.              |
| **Build Installer** | `uv run installer` | **(Windows Only)** Creates a `setup.exe` installer for easy distribution. |

---

## 🛠 Prerequisites

### General
*   **Python 3.12+**
*   **uv** package manager installed.

### Windows Installer (Optional)
To generate the `.exe` installer (setup file) on Windows, you must install:
*   [**Inno Setup 6+**](https://jrsoftware.org/isdl.php)
    *   Download and install the standard version.
    *   The build script will automatically locate `ISCC.exe`.

---

## 📦 Build Output

All artifacts are generated in the `dist/v{version}/{os}/` directory.

### 1. Portable App (`uv run build`)
Creates a fully self-contained folder (Windows/Linux) or App Bundle (macOS).
*   **Windows**: `dist/.../BetFinderApp/` (Folder containing `BetFinderApp.exe`)
*   **macOS**: `dist/.../BetFinderApp.app` (Native macOS Registry Bundle)
*   **Linux**: `dist/.../BetFinderApp/`

**Features:**
*   Bundles a portable `uv` binary (users don't need Python installed).
*   Bundles all backend code and migrations.
*   **Auto-Config**: On first launch, it generates a secure `.env` file with a unique `SECRET_KEY`.

### 2. Windows Installer (`uv run installer`)
*   **Output**: `BetFinder_Installer_vX.X.X.exe`
*   **Description**: A standard Windows setup wizard that installs the app to `AppData/Local/SportsBetFinder`, creates Desktop shortcuts, and includes an Uninstaller.

---

## 🧑‍💻 Development

You can test the tray wrapper without building:
```bash
uv run python tray_app.py
```
*   It operates in "Dev Mode", using your local source and system `uv`.

## ⚠️ Troubleshooting

*   **"Inno Setup Compiler not found"**: Ensure you have installed Inno Setup 6.
*   **Mac Startup Issues**: Check `~/betfinder_startup.log` if the app crashes before logs are created.
*   **Port Conflicts**: The app defaults to port `8123`. You can change this in the `.env` settings menu.
