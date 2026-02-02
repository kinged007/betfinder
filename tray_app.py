import os
import sys
import subprocess
import threading
import time
import webbrowser
import shutil
import logging
import platform
import secrets
from logging.handlers import RotatingFileHandler
from PIL import Image
import pystray

# --- Configuration ---
APP_NAME = "Sports Bet Finder"
ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.png")

# Determine paths
if getattr(sys, 'frozen', False):
    # Running as compiled exe
    APP_DIR = os.path.dirname(sys.executable)
    
    # Check for macOS .app bundle structure
    if platform.system() == "Darwin" and "Contents/MacOS" in APP_DIR:
        # We are likely deep inside the .app bundle.
        # Structure on macOS build often results in:
        # /dist/version/darwin/Folder/BetFinderApp.app/Contents/MacOS/Exe
        # And our resources might be in /dist/version/darwin/Folder/backend
        
        possible_roots = [
            os.path.join(APP_DIR, "..", "Resources"),               # Contents/Resources (Standard Bundle)
            os.path.abspath(os.path.join(APP_DIR, "..", "..")),     # BetFinderApp.app Root
            os.path.abspath(os.path.join(APP_DIR, "..", "..", "..")) # Folder containing .app (Parallel to .app)
        ]

        found_backend = False
        for root in possible_roots:
            if os.path.exists(os.path.join(root, "backend")):
                # Found backend - logger not available yet, will log later
                APP_DIR = root
                found_backend = True
                break

        if not found_backend:
            # Could not locate backend directory - logger not available yet
            # Default will remain Contents/MacOS, which will likely fail, but we tried.
            pass

    BACKEND_DIR = os.path.join(APP_DIR, "backend")
    DATA_DIR = os.path.join(APP_DIR, "data")
    SAMPLE_ENV_FILE = os.path.join(BACKEND_DIR, "sample.env")
else:
    # Running from source (dev mode)
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    BACKEND_DIR = APP_DIR
    DATA_DIR = APP_DIR
    SAMPLE_ENV_FILE = os.path.join(APP_DIR, "sample.env")

LOG_DIR = os.path.join(APP_DIR, "logs")
ENV_FILE = os.path.join(DATA_DIR, ".env")

# Determine UV executable path
system = platform.system()
uv_filename = "uv.exe" if system == "Windows" else "uv"
UV_PATH = os.path.join(APP_DIR, uv_filename)

# --- Logging Setup ---
# --- Logging Setup ---
# Early startup debugging for frozen apps
if getattr(sys, 'frozen', False):
    try:
        tmp_log = os.path.join(os.path.expanduser("~"), "betfinder_startup.log")
        with open(tmp_log, "a") as f:
            f.write(f"Startup check at {time.ctime()}\n")
            f.write(f"APP_DIR resolved to: {APP_DIR}\n")
    except Exception:
        pass

if not os.path.exists(LOG_DIR):
    try:
        os.makedirs(LOG_DIR)
    except Exception as e:
        # Fallback logging if we can't create directory (e.g. permission error in /Contents/MacOS?)
        # On macOS inside .app, we might not have write permission if signed/sandboxed?
        # Usually we do for non-sandboxed.
         pass

log_file = os.path.join(LOG_DIR, "app.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=5) if os.path.exists(LOG_DIR) else logging.NullHandler(),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("TrayApp")

# --- Global State ---
server_process = None
stop_event = threading.Event()

def ensure_environment():
    """Ensure data directory and .env file exist."""
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
        logger.info(f"Created data directory: {DATA_DIR}")

    if not os.path.exists(ENV_FILE):
        logger.info(".env file not found.")
        if os.path.exists(SAMPLE_ENV_FILE):
            logger.info(f"Generating .env from {SAMPLE_ENV_FILE} with auto-generated API keys...")
            try:
                with open(SAMPLE_ENV_FILE, "r") as f_src:
                    content = f_src.read()
                
                # Auto-generate a strong API Access Key
                generated_key = secrets.token_urlsafe(32)
                
                # Replace the empty key with the generated one
                # We assume the line is "SECRET_KEY=" or "SECRET_KEY"
                if "SECRET_KEY=" in content:
                    lines = content.splitlines()
                    new_lines = []
                    for line in lines:
                        if line.strip().startswith("SECRET_KEY="):
                            # Check if it is empty or we force overwrite? User asked for auto generate.
                            # Usually sample has "API_ACCESS_KEY=" (empty)
                            if line.strip() == "SECRET_KEY=":
                                new_lines.append(f"SECRET_KEY={generated_key}")
                            else:
                                new_lines.append(line) # Keep existing if sample had one?
                        else:
                            new_lines.append(line)
                    content = "\n".join(new_lines)
                else:
                    # Append it if not found
                    content += f"\SECRET_KEY={generated_key}\n"
                
                with open(ENV_FILE, "w") as f_dest:
                    f_dest.write(content)
                
                logger.info(f"Created {ENV_FILE} with generated API_ACCESS_KEY.")
                
                # Open the file for editing so user sees it
                open_file(ENV_FILE)
                
            except Exception as e:
                logger.error(f"Failed to generate .env from sample: {e}")
                # Fallback to simple copy if modification fails
                shutil.copy(SAMPLE_ENV_FILE, ENV_FILE)
        else:
            logger.error(f"sample.env not found at {SAMPLE_ENV_FILE}. Cannot bootstrap .env.")

def open_file(path):
    """Open a file with the default system editor."""
    if platform.system() == "Windows":
        os.startfile(path)
    elif platform.system() == "Darwin":
        subprocess.call(["open", path])
    else:
        subprocess.call(["xdg-open", path])

def run_migrations(env, executable):
    """Run database migrations."""
    try:
        logger.info("Running database migrations...")
        migration_cmd = [executable, "run", "alembic", "upgrade", "head"]
        startupinfo = None
        if platform.system() == "Windows":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        subprocess.run(
            migration_cmd,
            cwd=BACKEND_DIR,
            env=env,
            check=True,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        )
        logger.info("Database migrations completed successfully.")
        return True
    except Exception as e:
        logger.error(f"Failed to run database migrations: {e}")
        return False

def prepare_environment():
    """Prepare environment variables and return the env dict."""
    # Ensure data/.env is synced to backend/.env
    backend_env = os.path.join(BACKEND_DIR, ".env")
    env_missing = False

    if os.path.exists(ENV_FILE):
        try:
            shutil.copy(ENV_FILE, backend_env)
            logger.info(f"Synced {ENV_FILE} to {backend_env}")
        except Exception as e:
            logger.error(f"Failed to copy .env to backend: {e}")
    else:
        env_missing = True

    env = os.environ.copy()
    env["ENVIRONMENT"] = "production"
        
    return env, env_missing

def run_server_process(env):
    """Target function to run the server subprocess."""
    global server_process
    
    # Verify paths
    if not os.path.exists(BACKEND_DIR):
        logger.error(f"Backend directory not found at {BACKEND_DIR}")
        return

    # Determine executable
    executable = UV_PATH
    if not os.path.exists(executable) and not getattr(sys, 'frozen', False):
        executable = "uv"
    
    cmd = [executable, "run", "prod"]
    logger.info(f"Starting server with command: {cmd} in {BACKEND_DIR}")

    try:
        # Create a startup info to hide console window on Windows
        startupinfo = None
        if platform.system() == "Windows":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        server_process = subprocess.Popen(
            cmd,
            cwd=BACKEND_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        )
        
        # Read output line by line and log it
        for line in server_process.stdout:
            if stop_event.is_set():
                break
            line = line.strip()
            if line:
                logger.info(f"[Server] {line}")
                
        server_process.wait()
        logger.info(f"Server process exited with code {server_process.returncode}")
        
    except Exception as e:
        logger.error(f"Failed to start server: {e}")

def start_server_thread(env):
    """Start the server in a separate thread."""
    global stop_event
    stop_event.clear()
    t = threading.Thread(target=run_server_process, args=(env,))
    t.daemon = True
    t.start()

def get_executable():
    executable = UV_PATH
    if not os.path.exists(executable) and not getattr(sys, 'frozen', False):
        executable = "uv"
    return executable

def orchestrate_startup(icon):
    """Run full startup sequence: Env -> Migrations -> Server -> Browser."""
    icon.notify("Initializing application...", APP_NAME)
    
    # 1. Prepare Environment
    env, env_missing = prepare_environment()
    
    # 2. Run Migrations (only if env exists)
    if not env_missing:
        executable = get_executable()
        run_migrations(env, executable)
    
    # 3. Start Server
    start_server_thread(env)
    
    # 4. Wait for Server to Warm Up
    # We can't easily poll the port without extra deps, so we just sleep confidently
    time.sleep(5)
    
    # 5. Open Browser and Notify
    on_open_web(icon, None)
    icon.notify("Application is ready!", APP_NAME)

def stop_server():
    """Stop the running server."""
    global server_process, stop_event
    stop_event.set()
    if server_process:
        logger.info("Terminating server process...")
        
        # On Windows, we need to kill the process tree forcefully to ensure uvicorn/uv dies
        if platform.system() == "Windows":
             try:
                 subprocess.call(["taskkill", "/F", "/T", "/PID", str(server_process.pid)], 
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
             except Exception as e:
                 logger.error(f"Failed to taskkill: {e}")
        
        # Try standard terminate first (works well on Unix or if taskkill failed)
        try:
            server_process.terminate()
        except Exception:
            pass
            
        try:
            server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("Server did not exit, killing...")
            try:
                server_process.kill()
            except Exception:
                pass
        
        server_process = None

def restart_server(icon, item):
    """Restart operation."""
    icon.notify("Restarting server...", APP_NAME)
    stop_server()
    time.sleep(3)
    # Re-run full orchestration? Or just start server?
    # Usually restart implies re-reading config, so full orchestration is safer but maybe skip migrations?
    # Let's do full orchestration to be safe and consistent.
    orchestrate_startup(icon)

def get_port_from_env():
    """Simple parser to get PORT from .env file."""
    port = 8123
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, "r") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("PORT="):
                        try:
                            port = int(line.split("=")[1].strip())
                        except ValueError:
                            pass
        except Exception:
            pass
    return port

def on_open_web(icon, item):
    port = get_port_from_env()
    webbrowser.open(f"http://localhost:{port}")

def on_settings(icon, item):
    if os.path.exists(ENV_FILE):
        open_file(ENV_FILE)
    else:
        # Should not happen if ensure_environment ran
        ensure_environment()
        open_file(ENV_FILE)

def on_logs(icon, item):
    open_file(log_file)

def on_quit(icon, item):
    stop_server()
    icon.stop()

def setup(icon):
    """Called when the icon is ready."""
    icon.visible = True
    # Run orchestration in a separate thread so we don't block the UI loop (if any)
    # Pystray run() blocks, so setup() is called. But setup runs in the main thread usually?
    # Pystray documentation says setup is called in a separate thread depending on backend.
    # To be safe, let's run orchestration immediately here.
    orchestrate_startup(icon)

def main():
    logger.info(f"Starting {APP_NAME}...")
    
    ensure_environment()
    # start_server() -> Removed, moved to orchestration
    
    # Load icon
    image = None
    if not os.path.exists(ICON_PATH) and not getattr(sys, 'frozen', False):
         logger.warning(f"Icon not found at {ICON_PATH}, using default.")
         image = Image.new('RGB', (64, 64), color = (73, 109, 137))
    else:
         try:
             image = Image.open(ICON_PATH)
         except Exception as e:
             logger.error(f"Failed to load icon: {e}")
             image = Image.new('RGB', (64, 64), color = (255, 0, 0))

    menu = pystray.Menu(
        pystray.MenuItem("Open Web App", on_open_web, default=True),
        pystray.MenuItem("Settings", on_settings),
        pystray.MenuItem("View Logs", on_logs),
        pystray.MenuItem("Restart Server", restart_server),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit)
    )

    icon = pystray.Icon(APP_NAME, image, APP_NAME, menu)
    
    # Pass setup callback to run immediately after icon creation
    icon.run(setup)

if __name__ == "__main__":
    main()
