import os
import shutil
import urllib.request
import tarfile
import zipfile
import subprocess
import platform
import sys


# --- Configuration ---
APP_NAME = "BetFinderApp"
MAIN_SCRIPT = "tray_app.py"
ICON_PATH = os.path.join("assets", "icon.png")
DIST_BASE_DIR = "dist"
BUILD_WORK_DIR = "build"

# UV Download URLs
UV_VERSION = "0.5.26"
UV_PLATFORM_MAP = {
    "Windows": f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-x86_64-pc-windows-msvc.zip",
    "Darwin": f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-x86_64-apple-darwin.tar.gz",
    "Linux": f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz"
}

def get_version():
    """Extract version from pyproject.toml."""
    try:
        with open("pyproject.toml", "r") as f:
            # Simple manual parse to avoid depending on toml library if not present in build env
            # But the user likely has it or we can just parse line by line
            for line in f:
                if line.strip().startswith("version ="):
                    return line.split("=")[1].strip().strip('"').strip("'")
    except Exception as e:
        print(f"Warning: Could not read version from pyproject.toml: {e}")
    return "unknown"

def download_uv(target_dir):
    """Download and extract uv executable."""
    system = platform.system()
    url = UV_PLATFORM_MAP.get(system)
    if not url:
        print(f"Unsupported platform: {system}")
        sys.exit(1)
        
    print(f"Downloading uv from {url}...")
    filename = url.split("/")[-1]
    filepath = os.path.join(target_dir, filename)
    
    try:
        urllib.request.urlretrieve(url, filepath)
    except Exception as e:
        print(f"Failed to download uv: {e}")
        sys.exit(1)
        
    print("Extracting uv...")
    if filename.endswith(".zip"):
        with zipfile.ZipFile(filepath, 'r') as zip_ref:
            zip_ref.extractall(target_dir)
    elif filename.endswith(".tar.gz"):
        with tarfile.open(filepath, "r:gz") as tar:
            tar.extractall(target_dir)
            
    # Find the binary
    binary_name = "uv.exe" if system == "Windows" else "uv"
    found_path = None
    for root, dirs, files in os.walk(target_dir):
        if binary_name in files:
            found_path = os.path.join(root, binary_name)
            break
            
    if found_path:
        dest = os.path.join(target_dir, binary_name)
        if found_path != dest:
            shutil.move(found_path, dest)
        
        # Cleanup
        os.remove(filepath)
        print(f"uv binary ready at {dest}")
        if system != "Windows":
            os.chmod(dest, 0o755)
    else:
        print("Could not find uv binary in downloaded archive.")
        sys.exit(1)

def build_app():
    # 0. Context
    version = get_version()
    os_name = platform.system().lower()
    
    # 1. Prepare Release Paths
    # Output structure: dist/v{version}/{os_name}/BetFinderApp/
    release_dir = os.path.join(DIST_BASE_DIR, f"v{version}", os_name)
    app_output_dir = os.path.join(release_dir, APP_NAME)
    
    print(f"Building version {version} for {os_name}...")
    print(f"Target directory: {release_dir}")

    # Clean only this specific target directory if exists
    if os.path.exists(release_dir):
        try:
            shutil.rmtree(release_dir)
        except Exception as e:
            print(f"Warning: Could not clean target directory {release_dir}: {e}")
            print("Attempting to proceed...")

    # clean build work dir
    if os.path.exists(BUILD_WORK_DIR):
        try:
             shutil.rmtree(BUILD_WORK_DIR)
        except:
             pass
        
    # 2. PyInstaller Build
    print("Running PyInstaller...")
    
    # We use --distpath to specify where the 'BetFinderApp' folder/bundle will be created.
    # PyInstaller creates {distpath}/{name} (Windows/Linux) or {distpath}/{name}.app (macOS)
    
    spec_file = f"{APP_NAME}.spec"
    if os.path.exists(spec_file):
        print(f"Using existing spec file: {spec_file}")
        cmd = [
            "pyinstaller",
            "--noconfirm",
            "--distpath", release_dir,
            "--workpath", BUILD_WORK_DIR,
            spec_file
        ]
    else:
        print("No spec file found, using default arguments...")
        cmd = [
            "pyinstaller",
            "--noconfirm",
            "--onedir",
            "--windowed",
            "--name", APP_NAME,
            "--icon", ICON_PATH,
            "--distpath", release_dir,
            "--workpath", BUILD_WORK_DIR,
            "--add-data", f"{ICON_PATH}{os.pathsep}assets",
            MAIN_SCRIPT
        ]
    
    subprocess.check_call(cmd)
    
    # 3. Add External Dependencies
    print("Assembling package...")
    
    if os_name == "darwin":
        # On macOS, PyInstaller with --windowed creates a .app bundle directly in release_dir
        # Path: dist/vX/darwin/BetFinderApp.app
        macos_app_root = os.path.join(release_dir, f"{APP_NAME}.app")
        macos_content_root = os.path.join(macos_app_root, "Contents", "MacOS")
        
        # Verify it exists
        if not os.path.exists(macos_content_root):
            print(f"Warning: Could not find macOS bundle at {macos_content_root}.")
            # Fallback (maybe it didn't make a .app? Unlikely with --windowed)
            # Check if it made a folder instead
            potential_folder = os.path.join(release_dir, APP_NAME)
            if os.path.exists(potential_folder):
                 print(f"Found folder instead of .app at {potential_folder}")
                 dest_backend = os.path.join(potential_folder, "backend")
                 uv_target_dir = potential_folder
            else:
                 print("Critical: Build artifact not found.")
                 return
        else:
            print(f"Targeting macOS bundle at: {macos_content_root}")
            dest_backend = os.path.join(macos_content_root, "backend")
            uv_target_dir = macos_content_root
            
            # Update app_output_dir just for the final print statement to point to the .app
            app_output_dir = macos_app_root
    else:
        # Windows / Linux (OneDir)
        # PyInstaller creates a folder named APP_NAME in release_dir
        dest_backend = os.path.join(app_output_dir, "backend")
        uv_target_dir = app_output_dir

    os.makedirs(dest_backend, exist_ok=True)
    
    items_to_copy = [
        "app",
        "alembic",
        "scripts",
        "pyproject.toml",
        "uv.lock",
        "alembic.ini",
        "sample.env",
        "README.md"
    ]
    
    for item in items_to_copy:
        src_path = os.path.abspath(item)
        if os.path.exists(src_path):
            if os.path.isdir(src_path):
                shutil.copytree(src_path, os.path.join(dest_backend, item), dirs_exist_ok=True)
            else:
                shutil.copy2(src_path, os.path.join(dest_backend, item))
                
    # 4. Download and bundle UV
    download_uv(uv_target_dir)
    
    print(f"\nBuild complete! Artifacts are available at:")
    print(f"  {app_output_dir}")
    print("\nDirectory structure:")
    print(f"  {release_dir}/")
    print(f"    └── {APP_NAME}/  (Zip this folder to distribute)")

if __name__ == "__main__":
    build_app()
