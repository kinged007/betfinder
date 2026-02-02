import os
import subprocess
import shutil
import urllib.request
import platform
import sys

# Constants
ISCC_URL = "https://files.jrsoftware.org/isno/6.3.3/innosetup-6.3.3.exe"
INNO_INSTALLER_NAME = "innosetup-installer.exe"
ISS_SCRIPT_NAME = "betfinder_installer.iss"

def get_version():
    """Extract version from pyproject.toml."""
    try:
        with open("pyproject.toml", "r") as f:
            for line in f:
                if line.strip().startswith("version ="):
                    return line.split("=")[1].strip().strip('"').strip("'")
    except Exception as e:
        print(f"Warning: Could not read version from pyproject.toml: {e}")
    return "0.1.0"

def create_iss_script(version):
    """Create Inno Setup script."""
    
    # We expect the built files to be in dist/v{version}/windows/BetFinderApp
    # But wait, PyInstaller outputs to dist/v{version}/windows/BetFinderApp
    # We should package that folder.
    
    source_dir = os.path.abspath(os.path.join("dist", f"v{version}", "windows", "BetFinderApp"))
    if not os.path.exists(source_dir):
        print(f"Error: Build directory not found at {source_dir}. Run 'uv run build' first.")
        sys.exit(1)
        
    output_dir = os.path.abspath(os.path.join("dist", f"v{version}", "windows"))
    
    script_content = f"""
[Setup]
AppName=Sports Bet Finder
AppVersion={version}
DefaultDirName={{localappdata}}\\SportsBetFinder
DefaultGroupName=SportsBetFinder
PrivilegesRequired=lowest
UninstallDisplayIcon={{app}}\\BetFinderApp.exe
Compression=lzma2
SolidCompression=yes
OutputDir={output_dir}
OutputBaseFilename=BetFinder_Installer_v{version}
SetupIconFile=assets\\icon.ico

[Files]
Source: "{source_dir}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "betfinder.db,.env,*.log,__pycache__,*.pyc,*.spec,build"

[Icons]
Name: "{{group}}\\Sports Bet Finder"; Filename: "{{app}}\\BetFinderApp.exe"
Name: "{{group}}\\Uninstall Sports Bet Finder"; Filename: "{{uninstallexe}}"
Name: "{{commondesktop}}\\Sports Bet Finder"; Filename: "{{app}}\\BetFinderApp.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"; Flags: unchecked

[Run]
Filename: "{{app}}\\BetFinderApp.exe"; Description: "Launch Sports Bet Finder"; Flags: nowait postinstall skipifsilent
"""
    
    # Needs icon.ico. If we only have icon.png, we should convert or just not use SetupIconFile?
    # Inno Setup requires .ico for SetupIconFile.
    # We will skip SetupIconFile if .ico doesn't exist, or user must provide it.
    if not os.path.exists(os.path.join("assets", "icon.ico")):
        # Remove SetupIconFile line
        lines = script_content.splitlines()
        script_content = "\n".join([l for l in lines if not l.startswith("SetupIconFile=")])
    
    with open(ISS_SCRIPT_NAME, "w") as f:
        f.write(script_content)
    
    print(f"Created Inno Setup script: {ISS_SCRIPT_NAME}")
    return ISS_SCRIPT_NAME

def find_iscc():
    """Find local Inno Setup Compiler."""
    # Common paths
    paths = [
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None

def build_installer():
    if platform.system() != "Windows":
        print("Installer build is only supported on Windows.")
        sys.exit(1)
        
    version = get_version()
    
    # 1. Create ISS Script
    iss_file = create_iss_script(version)
    
    # 2. Find ISCC
    iscc_path = find_iscc()
    if not iscc_path:
        print("Inno Setup Compiler (ISCC) not found.")
        print("Please install Inno Setup 6 from https://jrsoftware.org/isdl.php")
        print(f"Or you can compile '{iss_file}' manually.")
        sys.exit(1)
        
    # 3. Compile
    print(f"Compiling installer using {iscc_path}...")
    try:
        subprocess.check_call([iscc_path, iss_file])
        print("Installer compilation successful!")
        print(f"Installer available at: dist/v{version}/windows/BetFinder_Installer_v{version}.exe")
    except subprocess.CalledProcessError as e:
        print(f"Failed to compile installer: {e}")
        sys.exit(1)

if __name__ == "__main__":
    build_installer()
