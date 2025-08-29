import os
import re
import json
import shutil
import struct
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import winreg
import time
import string
import tkinter.simpledialog as simpledialog

STATE_FILE = os.path.join(os.path.expanduser("~"), "steam_mod_launcher_state.json")

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"[WARN] Could not save state: {e}")

# global cache of last state
last_state = load_state()





def search_file(filename, max_depth=4):
    """
    Recursively search all drives for a file/folder name.
    Returns list of matching paths.
    """
    import string
    results = []

    def walk_dir(path, depth):
        if depth > max_depth:
            return
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if filename.lower() in entry.name.lower():
                        print(f"[FOUND] {entry.path}")   # 👈 debug print
                        results.append(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        walk_dir(entry.path, depth + 1)
        except (PermissionError, FileNotFoundError):
            return

    # find all drives
    drives = [f"{d}:\\"
              for d in string.ascii_uppercase
              if os.path.exists(f"{d}:\\")]
    for drive in drives:
        print(f"[SCAN] Scanning drive: {drive}")  # 👈 debug print
        walk_dir(drive, 0)

    return results


# -----------------------------
# --- Shortcuts.vdf helpers ---
# -----------------------------
def parse_shortcuts(path):
    shortcuts = []
    if not os.path.exists(path):
        return shortcuts
    with open(path, "rb") as f:
        data = f.read()

    i = 0
    while i < len(data):
        if data[i] == 0x08:
            break
        if data[i] != 0x00:
            i += 1
            continue
        i += 1
        while data[i] != 0x00:
            i += 1
        i += 1
        entry = {}
        while i < len(data):
            t = data[i]
            i += 1
            if t == 0x08:
                break
            key = b""
            while data[i] != 0x00:
                key += bytes([data[i]])
                i += 1
            i += 1
            key = key.decode("utf-8")
            if t == 0x01:
                val = b""
                while data[i] != 0x00:
                    val += bytes([data[i]])
                    i += 1
                i += 1
                entry[key] = val.decode("utf-8")
            elif t == 0x02:
                val = struct.unpack("<I", data[i:i+4])[0]
                i += 4
                entry[key] = val
        shortcuts.append(entry)
    return shortcuts

def write_shortcuts(path, shortcuts):
    buf = b"\x00shortcuts\x00"
    for idx, sc in enumerate(shortcuts):
        buf += b"\x00" + str(idx).encode("utf-8") + b"\x00"
        for k, v in sc.items():
            if isinstance(v, int):
                buf += b"\x02" + k.encode("utf-8") + b"\x00"
                buf += struct.pack("<I", v)
            else:
                buf += b"\x01" + k.encode("utf-8") + b"\x00"
                buf += v.encode("utf-8") + b"\x00"
        buf += b"\x08"
    buf += b"\x08\x08"
    with open(path, "wb") as f:
        f.write(buf)

def add_nonsteam_shortcut(name, exe, startdir, launch_opts=""):
    steam_root = get_steam_root()
    userdata = os.path.join(steam_root, "userdata")
    exe_norm = exe.strip('"').lower()
    for sid in os.listdir(userdata):
        cfg = os.path.join(userdata, sid, "config")
        os.makedirs(cfg, exist_ok=True)
        shortcuts_path = os.path.join(cfg, "shortcuts.vdf")
        shortcuts = parse_shortcuts(shortcuts_path)

        if any(sc.get("exe", "").strip('"').lower() == exe_norm for sc in shortcuts):
            continue

        shortcuts.append({
            "appid": 0,
            "AppName": name,
            "exe": f"\"{exe}\"",
            "StartDir": f"\"{startdir}\"",
            "LaunchOptions": launch_opts,
            "icon": "",
            "ShortcutPath": "",
            "IsHidden": 0,
            "AllowOverlay": 1,
            "OpenVR": 0,
            "Devkit": 0,
            "DevkitGameID": "",
            "LastPlayTime": 0,
            "tags": ""
        })
        write_shortcuts(shortcuts_path, shortcuts)

def remove_nonsteam_shortcut(exe_path):
    steam_root = get_steam_root()
    userdata = os.path.join(steam_root, "userdata")
    target = os.path.normpath(os.path.abspath(exe_path.strip('"'))).lower()

    for sid in os.listdir(userdata):
        cfg = os.path.join(userdata, sid, "config")
        shortcuts_path = os.path.join(cfg, "shortcuts.vdf")
        if not os.path.exists(shortcuts_path):
            continue

        shortcuts = parse_shortcuts(shortcuts_path)
        new_shortcuts = []
        changed = False

        for sc in shortcuts:
            sc_exe = sc.get("exe", "").strip('"')
            sc_exe_norm = os.path.normpath(os.path.abspath(sc_exe)).lower()
            if sc_exe_norm == target:
                changed = True
                continue
            new_shortcuts.append(sc)

        if changed:
            write_shortcuts(shortcuts_path, new_shortcuts)



# -----------------------------
# --- Auto Detect Engine Type Of Game   ---
# -----------------------------            
def detect_engine(game_dir):
    """
    Detect whether the game is Unity, Unreal, or Unknown.
    """
    # Unity games almost always have UnityPlayer.dll
    if os.path.exists(os.path.join(game_dir, "UnityPlayer.dll")):
        return "Unity"

    # Unreal Engine games have Engine/Binaries/Win64 or paks folder
    for root, dirs, files in os.walk(game_dir):
        if "UE4Editor.exe" in files or "UnrealEditor.exe" in files:
            return "Unreal"
        if "Paks" in dirs:
            return "Unreal"
    return "Unknown"


# -----------------------------
# --- Steam + Mod helpers   ---
# -----------------------------
def get_steam_root():
    paths = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\WOW6432Node\\Valve\\Steam") as k:
            paths.append(winreg.QueryValueEx(k, "InstallPath")[0])
    except Exception:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\\Valve\\Steam") as k:
            paths.append(winreg.QueryValueEx(k, "SteamPath")[0])
    except Exception:
        pass
    paths += [r"C:\\Program Files (x86)\\Steam", r"C:\\Program Files\\Steam"]
    for p in paths:
        if p and os.path.exists(p):
            return p
    raise FileNotFoundError("Steam root not found.")

def get_library_folders(steam_root):
    vdf_path = os.path.join(steam_root, "steamapps", "libraryfolders.vdf")
    libs = []
    with open(vdf_path, encoding="utf-8") as f:
        text = f.read()
    try:
        data = json.loads(text)
        for _, v in data.get("libraryfolders", {}).items():
            path = v.get("path")
            if path:
                apps = os.path.join(path, "steamapps")
                if os.path.exists(apps):
                    libs.append(apps)
    except Exception:
        paths = re.findall(r'"path"\s*"([^"]+)"', text)
        for p in paths:
            apps = os.path.join(p, "steamapps")
            if os.path.exists(apps):
                libs.append(apps)
    default_apps = os.path.join(steam_root, "steamapps")
    if os.path.exists(default_apps):
        libs.append(default_apps)
    return list(set(libs))

def parse_appmanifest(path):
    with open(path, encoding="utf-8") as f:
        txt = f.read()
    return {
        "AppId": re.search(r'"appid"\s*"([^"]+)"', txt).group(1),
        "Name": re.search(r'"name"\s*"([^"]+)"', txt).group(1),
        "InstallDir": re.search(r'"installdir"\s*"([^"]+)"', txt).group(1),
        "Manifest": path,
    }

def find_games(libs):
    seen = {}
    for lib in libs:
        for fname in os.listdir(lib):
            if fname.startswith("appmanifest_") and fname.endswith(".acf"):
                try:
                    game = parse_appmanifest(os.path.join(lib, fname))
                    seen[game["AppId"]] = game
                except Exception:
                    pass
    return sorted(seen.values(), key=lambda g: g["Name"].lower())

def find_game_exe(game_dir):
    exes = []
    for root, _, files in os.walk(game_dir):
        for f in files:
            if f.lower().endswith(".exe"):
                exes.append(os.path.join(root, f))
    if not exes:
        return None
    folder_name = os.path.basename(game_dir).lower()
    for exe in exes:
        if folder_name in os.path.basename(exe).lower():
            return exe
    exes.sort(key=lambda x: os.path.getsize(x), reverse=True)
    return exes[0]

# -----------------------------
# --- Thunderstore helpers  ---
# -----------------------------
def get_thunderstore_bases():
    bases = []

    # Default known locations
    candidates = [
        os.getenv("APPDATA"),
        os.getenv("LOCALAPPDATA"),
        os.path.join(os.getenv("USERPROFILE", ""), "AppData", "Roaming"),
        os.path.join(os.getenv("USERPROFILE", ""), "AppData", "Local"),
    ]
    for c in candidates:
        if not c or not os.path.exists(c):
            continue
        r2 = os.path.join(c, "r2modmanPlus-local")
        tmm = os.path.join(c, "Thunderstore Mod Manager", "DataFolder")
        if os.path.exists(r2):
            bases.append(r2)
        if os.path.exists(tmm):
            bases.append(tmm)

    # If nothing found, scan drives
    if not bases:
        found_r2 = search_file("r2modmanPlus-local")
        found_tmm = search_file("Thunderstore Mod Manager")
        bases.extend(found_r2 + found_tmm)

    return list(set(bases))

# -----------------------------
# --- Vortex Mod helpers    ---
# -----------------------------
import zipfile

def normalize_gameid(gameid):
    """
    Normalize Steam InstallDir to Vortex folder name.
    Example: 'Lethal Company' -> 'lethalcompany'
    """
    return gameid.lower().replace(" ", "").replace("-", "")

def get_vortex_downloads(gameid):
    """
    Find mods for a given game from Vortex downloads.
    Supports both .zip files and extracted folders.
    """
    base = os.path.expandvars(r"%APPDATA%\Vortex\downloads")
    norm_id = normalize_gameid(gameid)
    game_dir = os.path.join(base, norm_id)

    if not os.path.exists(game_dir):
        print(f"[VORTEX] No downloads found for {norm_id} (from {gameid})")
        return []

    mods = []
    for f in os.listdir(game_dir):
        full_path = os.path.join(game_dir, f)
        if f.lower().endswith(".zip") or os.path.isdir(full_path):
            mods.append(full_path)

    print(f"[VORTEX] Found {len(mods)} mods for {norm_id}")
    return mods




def extract_mod(zip_path, target_dir):
    """
    Extract a single zip mod into the target folder.
    """
    if os.path.exists(target_dir):
        shutil.rmtree(target_dir)  # clean old version
    os.makedirs(target_dir, exist_ok=True)

    with zipfile.ZipFile(zip_path, 'r') as zf:
        zf.extractall(target_dir)

    print(f"[DEPLOYED] {os.path.basename(zip_path)} -> {target_dir}")

def deploy_vortex_mods(gameid, game_dir, selected_mods=None):
    mods = get_vortex_downloads(gameid)
    if not mods:
        return False

    engine = detect_engine(game_dir)
    print(f"[DEPLOY] Detected engine: {engine}")

    if engine == "Unity":
        plugins_dir = os.path.join(game_dir, "BepInEx", "plugins")
        os.makedirs(plugins_dir, exist_ok=True)
        target_root = game_dir
    elif engine == "Unreal":
        plugins_dir = None
        target_root = os.path.join(game_dir, "Content", "Paks", "~mods")
        os.makedirs(target_root, exist_ok=True)
    else:
        plugins_dir = os.path.join(game_dir, "modded")
        os.makedirs(plugins_dir, exist_ok=True)
        target_root = plugins_dir

    for mod_zip in mods:
        if selected_mods and os.path.basename(mod_zip) not in selected_mods:
            continue

        name = os.path.basename(mod_zip).lower()
        # special case → BepInEx framework
        if engine == "Unity" and "bepinexpack" in name:
            extract_mod(mod_zip, target_root)
            print(f"[DEPLOYED FRAMEWORK] {os.path.basename(mod_zip)} → {target_root}")
        else:
            target = os.path.join(plugins_dir, os.path.splitext(os.path.basename(mod_zip))[0]) if plugins_dir else os.path.join(target_root, os.path.splitext(os.path.basename(mod_zip))[0])
            extract_mod(mod_zip, target)
            print(f"[DEPLOYED MOD] {os.path.basename(mod_zip)} → {target}")

    return True




def get_thunderstore_bases():
    bases = []

    # Default known locations
    candidates = [
        os.getenv("APPDATA"),
        os.getenv("LOCALAPPDATA"),
        os.path.join(os.getenv("USERPROFILE", ""), "AppData", "Roaming"),
        os.path.join(os.getenv("USERPROFILE", ""), "AppData", "Local"),
    ]
    for c in candidates:
        if not c or not os.path.exists(c):
            continue
        r2 = os.path.join(c, "r2modmanPlus-local")
        tmm = os.path.join(c, "Thunderstore Mod Manager", "DataFolder")
        if os.path.exists(r2):
            print(f"[TS BASE FOUND] {r2}")   # 👈 debug
            bases.append(r2)
        if os.path.exists(tmm):
            print(f"[TS BASE FOUND] {tmm}")  # 👈 debug
            bases.append(tmm)

    if not bases:
        print("[SCAN] No Thunderstore in defaults, scanning drives...")
        found_r2 = search_file("r2modmanPlus-local", max_depth=5)
        found_tmm = search_file("Thunderstore Mod Manager", max_depth=5)
        for f in found_r2 + found_tmm:
            print(f"[TS BASE FOUND] {f}")
            bases.append(f)

    return list(set(bases))


def get_thunderstore_profiles_any(game_name_guess=None):
    profiles = []
    for base in get_thunderstore_bases():
        for game_folder in os.listdir(base):
            game_path = os.path.join(base, game_folder, "profiles")
            if not os.path.exists(game_path):
                continue

            if game_name_guess and game_name_guess.lower().replace(" ", "") not in game_folder.lower():
                continue

            for p in os.listdir(game_path):
                full = os.path.join(game_path, p)
                if os.path.isdir(full):
                    print(f"[TS PROFILE] {full}")  # 👈 debug
                    profiles.append(full)
    return profiles



# -----------------------------
# --- Shim creation helpers ---
# -----------------------------
def create_simple_shim(game_dir, mod_cmd):
    shim_path = os.path.join(game_dir, "ModLaunch.cmd")
    mod_cmd = mod_cmd.strip().strip('"')
    if mod_cmd.lower().endswith(".lnk"):
        launch_line = f'start "" explorer.exe "{mod_cmd}" %*'
    else:
        launch_line = f'start "" "{mod_cmd}" %*'
    shim = f"""@echo off
setlocal enabledelayedexpansion
{launch_line}
exit
"""
    with open(shim_path, "w", encoding="utf-8") as f:
        f.write(shim)
    return shim_path

def create_shim_with_sync(game_dir, game_exe, profile_path):
    shim_path = os.path.join(game_dir, "ModLaunch.cmd")
    shim = f"""@echo off
setlocal enabledelayedexpansion
set PROFILE={profile_path}

if exist "%PROFILE%\\BepInEx" (
    xcopy /E /Y /I "%PROFILE%\\BepInEx" "%~dp0BepInEx" >nul
)
for %%f in (doorstop_config.ini winhttp.dll version.dll) do (
    if exist "%PROFILE%\\%%f" copy /Y "%PROFILE%\\%%f" "%~dp0%%f" >nul
)

start "" "{game_exe}" %*
timeout /t 3 >nul
exit
"""
    with open(shim_path, "w", encoding="utf-8") as f:
        f.write(shim)
    return shim_path

def create_custom_shim_with_sync(shim_dir, game_exe, profile_path, game_dir):
    os.makedirs(shim_dir, exist_ok=True)
    shim_path = os.path.join(shim_dir, "ModLaunch.cmd")
    shim = f"""@echo off
setlocal enabledelayedexpansion
set PROFILE={profile_path}
set GAMEDIR={game_dir}

if exist "%~dp0modded" rmdir /S /Q "%~dp0modded"
mkdir "%~dp0modded"

if exist "%PROFILE%\\BepInEx" (
    xcopy /E /Y /I "%PROFILE%\\BepInEx" "%~dp0modded\\BepInEx" >nul
)
for %%f in (doorstop_config.ini winhttp.dll version.dll) do (
    if exist "%PROFILE%\\%%f" copy /Y "%PROFILE%\\%%f" "%~dp0modded\\%%f" >nul
)

if exist "%GAMEDIR%\\BepInEx" rmdir /S /Q "%GAMEDIR%\\BepInEx"
for %%f in (doorstop_config.ini winhttp.dll version.dll) do (
    if exist "%GAMEDIR%\\%%f" del /F /Q "%GAMEDIR%\\%%f"
)

if exist "%~dp0modded\\BepInEx" (
    xcopy /E /Y /I "%~dp0modded\\BepInEx" "%GAMEDIR%\\BepInEx" >nul
)
for %%f in (doorstop_config.ini winhttp.dll version.dll) do (
    if exist "%~dp0modded\\%%f" copy /Y "%~dp0modded\\%%f" "%GAMEDIR%\\%%f" >nul
)

start "" "{game_exe}" %*
timeout /t 3 >nul
exit
"""
    with open(shim_path, "w", encoding="utf-8") as f:
        f.write(shim)
    return shim_path

def is_modded(game):
    """
    Detect modded state ONLY if our launcher placed something.
    Checks for shim + our modded deployment.
    """
    game_dir = os.path.join(os.path.dirname(game["Manifest"]), "common", game["InstallDir"])

    # Shim marker
    shim = os.path.join(game_dir, "ModLaunch.cmd")
    if os.path.exists(shim):
        return True

    # Vortex-style folder
    if os.path.exists(os.path.join(game_dir, "modded")):
        return True

    # Thunderstore deploy leaves BepInEx directly in game folder
    bepinex_dir = os.path.join(game_dir, "BepInEx")
    if os.path.exists(bepinex_dir):
        return True

    return False



# -----------------------------
# --- GUI ---------------------
# -----------------------------
def main():
    try:
        steam_root = get_steam_root()
        libs = get_library_folders(steam_root)
        games = find_games(libs)
    except Exception as e:
        messagebox.showerror("Error", f"Failed to find Steam libraries: {e}")
        return
    if not games:
        messagebox.showerror("Error", "No Steam games found.")
        return

    root = tk.Tk()
    root.title("Steam Mod Launcher Setup")
    notebook = ttk.Notebook(root)
    notebook.pack(expand=True, fill="both")

    # ========================
    # Tab 1: Steam Games
    # ========================
    steam_tab = tk.Frame(notebook)
    notebook.add(steam_tab, text="Steam Games")

    tk.Label(steam_tab, text="Select a game:").pack(pady=5)
    game_var = tk.StringVar()
    combo = ttk.Combobox(steam_tab, textvariable=game_var, state="readonly",
                         values=[g["Name"] for g in games], width=40)
    combo.pack(pady=5)
    combo.current(0)

    status_frame = tk.Frame(steam_tab)
    version_label = tk.Label(status_frame, text="Current version: Vanilla")
    version_label.pack(side="left", padx=5)
    status_canvas = tk.Canvas(status_frame, width=12, height=12, highlightthickness=0)
    status_circle = status_canvas.create_oval(2, 2, 10, 10, fill="red")
    status_canvas.pack(side="left")
    status_frame.pack(pady=5)

    launcher_label = tk.Label(steam_tab, text="Select Launcher:")
    launcher_label.pack(pady=(10, 2))

    mode_var = tk.StringVar(value="User Defined")
    mode_combo = ttk.Combobox(
        steam_tab,
        textvariable=mode_var,
        state="readonly",
        values=["User Defined", "Thunderstore", "Vortex"],
        width=20
    )
    mode_combo.pack(pady=5)



    profile_frame = tk.Frame(steam_tab)
    profile_var = tk.StringVar()
    profile_combo = ttk.Combobox(profile_frame, textvariable=profile_var, state="readonly", width=40)
    tk.Label(profile_frame, text="Thunderstore profile:").pack(side="left", padx=5)
    profile_combo.pack(side="left", padx=5)

    user_frame = tk.Frame(steam_tab)
    cmd_var = tk.StringVar()
    cmd_entry = tk.Entry(user_frame, textvariable=cmd_var, width=50)
    browse_btn = tk.Button(user_frame, text="Browse…",
                           command=lambda: cmd_var.set(
                               f"\"{filedialog.askopenfilename(title='Select modded EXE/BAT/CMD/LNK', filetypes=[('Executables','*.exe *.bat *.cmd *.lnk'),('All files','*.*')])}\""
                           ))
    cmd_entry.pack(side="left", padx=5)
    browse_btn.pack(side="left", padx=5)

    create_btn = tk.Button(steam_tab, text="Create Launcher")
    revert_unlock_btn = tk.Button(steam_tab, text="Revert to Vanilla & Unlock")
    revert_btn = tk.Button(steam_tab, text="Restore Vanilla")
    copy_btn = tk.Button(steam_tab, text="Copy Launch Option")
        # Inline status label for Vortex mods
    mods_status_label = tk.Label(steam_tab, text="")

    engine_var = tk.StringVar(value="Engine: Unknown")
    engine_label = tk.Label(root, textvariable=engine_var, fg="blue")
    engine_label.pack(side="bottom", pady=2)

    def update_engine_label():
        sel_name = game_var.get()
        game = next((g for g in games if g["Name"] == sel_name), None)
        if game:
            game_dir = os.path.join(os.path.dirname(game["Manifest"]), "common", game["InstallDir"])
            engine = detect_engine(game_dir)
            engine_var.set(f"Game Engine: {engine}")
        else:
            engine_var.set("Game Engine: Unknown")

    # hook it into game change
    game_var.trace("w", lambda *a: (update_status(), update_engine_label()))


    def revert_unlock(game, game_dir):
        """
        Restore vanilla and unlock launcher when using Vortex.
        """
        # Remove Mod Selection tab if visible
        for i in range(notebook.index("end")):
            if notebook.tab(i, "text") == "Mod Selection":
                notebook.forget(mods_tab)
                break
        
        leftovers = [
            "BepInEx",
            "doorstop_config.ini",
            "winhttp.dll",
            "version.dll",
            "ModLaunch.cmd",
            "modded"
        ]

        for item in leftovers:
            path = os.path.join(game_dir, item)
            if os.path.exists(path):
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)

        # 🔑 Clear saved state so update_status won't think it's still modded
        sel_name = game["Name"]
        if sel_name in last_state:
            del last_state[sel_name]
            save_state(last_state)

        # ✅ Reset UI to Vanilla
        version_label.config(text="Current version: Vanilla")
        status_canvas.itemconfig(status_circle, fill="red")

        # Unlock launcher dropdowns
        mode_combo.config(state="readonly")
        profile_combo.config(state="readonly")
        cmd_entry.config(state="normal")
        browse_btn.config(state="normal")

        # Hide revert button
        revert_unlock_btn.pack_forget()
        revert_btn.pack_forget()
        copy_btn.pack_forget()

        # Remove Mod Selection tab if still visible
        for i in range(notebook.index("end")):
            if notebook.tab(i, "text") == "Mod Selection":
                notebook.forget(mods_tab)
                break

        # Finally update UI status cleanly
        update_status()

        # Show success message
        show_status(f"✅ {sel_name} restored to Vanilla. Launcher unlocked.")




    def detect_existing_launcher(game):
        """
        Detect if the game is modded, and if so, which launcher/profile is active.
        Returns (launcher, profile_name) or (None, None) if vanilla.
        """
        game_dir = os.path.join(os.path.dirname(game["Manifest"]), "common", game["InstallDir"])
        shim_path = os.path.join(game_dir, "ModLaunch.cmd")

        if not os.path.exists(shim_path):
            return None, None

        try:
            with open(shim_path, "r", encoding="utf-8") as f:
                contents = f.read().lower()
        except Exception:
            return None, None

        # Thunderstore shim contains "set PROFILE="
        if "set profile=" in contents:
            for line in contents.splitlines():
                if line.strip().lower().startswith("set profile="):
                    profile_path = line.split("=", 1)[1].strip()
                    profile_name = os.path.basename(profile_path)
                    return "Thunderstore", profile_name

        # Vortex shim contains "set GAMEDIR="
        if "modded" in contents and "set gamedir=" in contents:
            return "Vortex", None

        return None, None


    def update_status():
        sel_name = game_var.get()
        game = next(g for g in games if g["Name"] == sel_name)
        game_dir = os.path.join(os.path.dirname(game["Manifest"]), "common", game["InstallDir"])

        # Reset UI
        mode_combo.config(state="readonly")
        profile_combo.config(state="readonly")
        cmd_entry.config(state="normal")
        browse_btn.config(state="normal")

        create_btn.pack_forget()
        revert_btn.pack_forget()
        revert_unlock_btn.pack_forget()
        copy_btn.pack_forget()

        # Always hide Mod Selection tab at start
        for i in range(notebook.index("end")):
            if notebook.tab(i, "text") == "Mod Selection":
                notebook.forget(mods_tab)
                break

        # Load game state if it exists
        state = last_state.get(sel_name)

        if state:
            launcher = state.get("launcher")
            profile = state.get("profile", "")

            mode_var.set(launcher)
            mode_combo.config(state="disabled")
            status_canvas.itemconfig(status_circle, fill="green")

            if launcher == "Thunderstore":
                version_label.config(text=f"Current version: Modded (Thunderstore)")
                profile_combo.set(profile)
                profile_combo.config(state="disabled")
                revert_btn.pack(pady=10)
                copy_btn.pack(pady=10)

            elif launcher == "Vortex":
                revert_unlock_btn.config(command=lambda: revert_unlock(game, game_dir))
                revert_unlock_btn.pack(pady=10)

                enabled_mods = state.get("enabled_mods", [])
                count = len(enabled_mods)

                version_label.config(
                    text=f"Current version: Modded (Vortex, {count} mod{'s' if count != 1 else ''} enabled)"
                )
                status_canvas.itemconfig(status_circle, fill="green")

                # Only add Mod Selection tab if it’s not already there
                if "Mod Selection" not in [notebook.tab(i, "text") for i in range(notebook.index("end"))]:
                    notebook.add(mods_tab, text="Mod Selection")

                populate_mod_list(game["InstallDir"], game_dir)

                # Restore checkboxes
                for m in enabled_mods:
                    if m in mod_vars:
                        mod_vars[m].set(True)



            elif launcher == "User Defined":
                version_label.config(text=f"Current version: Modded (User Defined)")
                cmd_entry.delete(0, tk.END)
                cmd_entry.insert(0, profile)
                cmd_entry.config(state="disabled")
                browse_btn.config(state="disabled")
                revert_btn.pack(pady=10)
                copy_btn.pack(pady=10)

        else:
            # Default Vanilla
            version_label.config(text="Current version: Vanilla")
            status_canvas.itemconfig(status_circle, fill="red")

            create_btn.config(
                text="Create Launcher",
                state="normal",
                command=on_ok
            )
            create_btn.pack(side="bottom", pady=10)

            update_mode()



    def on_revert():
        sel_name = game_var.get()
        game = next(g for g in games if g["Name"] == sel_name)
        lib_path = os.path.dirname(game["Manifest"])
        game_dir = os.path.join(lib_path, "common", game["InstallDir"])
        
        # Remove Mod Selection tab if visible
        for i in range(notebook.index("end")):
            if notebook.tab(i, "text") == "Mod Selection":
                notebook.forget(mods_tab)
                break
        
        # Remove all mod leftovers
        leftovers = [
            "BepInEx",
            "doorstop_config.ini",
            "winhttp.dll",
            "version.dll",
            "ModLaunch.cmd",
            "modded"
        ]
        for item in leftovers:
            path = os.path.join(game_dir, item)
            if os.path.exists(path):
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)

        # ✅ Remove saved modded state
        if sel_name in last_state:
            del last_state[sel_name]
            save_state(last_state)

        # Force Vanilla UI
        version_label.config(text="Current version: Vanilla")
        status_canvas.itemconfig(status_circle, fill="red")

        mode_combo.config(state="readonly")
        profile_combo.config(state="readonly")
        cmd_entry.config(state="normal")
        browse_btn.config(state="normal")

        revert_btn.pack_forget()
        revert_unlock_btn.pack_forget()
        copy_btn.pack_forget()

        show_status(f"✅ {sel_name} restored to Vanilla. Launcher unlocked.")

        # Now redraw everything fresh
        update_status()







    def update_mode(*args):
        # Hide irrelevant frames
        for w in (profile_frame, user_frame):
            w.pack_forget()

        # Clear any old status label
        try:
            mods_status_label.pack_forget()
        except Exception:
            pass

        launcher = mode_var.get()

        if launcher == "Thunderstore":
            sel_name = game_var.get()
            game = next((g for g in games if g["Name"] == sel_name), None)
            if game:
                profiles = get_thunderstore_profiles_any(game["InstallDir"])
                if profiles:
                    profile_combo["values"] = [os.path.basename(p) for p in profiles]
                    profile_combo.current(0)
                else:
                    profile_combo["values"] = ["No profiles detected"]
                    profile_combo.current(0)
            profile_frame.pack(pady=5)
            create_btn.config(text="Create Launcher", state="normal")

        elif launcher == "Vortex":
            sel_name = game_var.get()
            game = next((g for g in games if g["Name"] == sel_name), None)
            mods = []
            if game:
                mods = get_vortex_downloads(game["InstallDir"])

            # Inline status label
            mods_status_label.config(
                text=(f"✅ {len(mods)} mods available" if mods else "❌ No mods available"),
                fg=("green" if mods else "red")
            )
            mods_status_label.pack(pady=(2, 5))

            if mods:
                create_btn.config(text="Use Mod Selection", state="normal")
            else:
                create_btn.config(state="disabled")





    def on_ok():
        sel_name = game_var.get()
        game = next(g for g in games if g["Name"] == sel_name)
        lib_path = os.path.dirname(game["Manifest"])
        game_dir = os.path.join(lib_path, "common", game["InstallDir"])
        exe_path = find_game_exe(game_dir)
        if not exe_path:
            messagebox.showerror("Error", f"Could not find any .exe in {game_dir}")
            return

        # -----------------------------
        # Thunderstore
        # -----------------------------
        if mode_var.get() == "Thunderstore":
            profiles = get_thunderstore_profiles_any(game["InstallDir"])
            if not profiles:
                profile_combo["values"] = ["No profiles detected"]
                profile_combo.current(0)
                return

            sel_profile = profile_var.get()
            profile_path = next((p for p in profiles if os.path.basename(p) == sel_profile), None)
            if not profile_path:
                profile_combo.set("No profile selected")
                return

            shim_path = create_shim_with_sync(game_dir, exe_path, profile_path)

            # Save state
            last_state[sel_name] = {
                "launcher": "Thunderstore",
                "profile": sel_profile
            }
            save_state(last_state)

            # UI
            version_label.config(text="Current version: Modded (Thunderstore)")
            status_canvas.itemconfig(status_circle, fill="green")
            mode_combo.config(state="disabled")
            profile_combo.config(state="disabled")
            create_btn.pack_forget()
            revert_btn.config(command=on_revert)
            revert_btn.pack(pady=10)

            # Copy launch option
            launch_opts = f"\"{shim_path}\" %command%"
            root.clipboard_clear()
            root.clipboard_append(launch_opts)
            copy_btn.pack(pady=10)

            show_status(f"✅ Thunderstore launcher created for profile '{sel_profile}' (copied to clipboard).")

        # -----------------------------
        # Vortex
        # -----------------------------
        elif mode_var.get() == "Vortex":
            mods = get_vortex_downloads(game["InstallDir"])
            if not mods:
                mods_status_label.config(text="❌ No mods available", fg="red")
                create_btn.config(state="disabled")
                return
            else:
                mods_status_label.config(text=f"✅ {len(mods)} mods available", fg="green")

            modded_dir = os.path.join(game_dir, "modded")

            # 👇 don’t deploy mods yet, just prepare environment
            if not os.path.exists(modded_dir):
                os.makedirs(modded_dir, exist_ok=True)

            # Update UI status
            version_label.config(text="Current version: Modded (Vortex, 0 mods enabled)")
            status_canvas.itemconfig(status_circle, fill="green")

            mode_combo.config(state="disabled")
            create_btn.pack_forget()
            revert_unlock_btn.config(command=lambda: revert_unlock(game, game_dir))
            revert_unlock_btn.pack(pady=10)

            # Add Mod Selection tab only now
            if "Mod Selection" not in [notebook.tab(i, "text") for i in range(notebook.index("end"))]:
                notebook.add(mods_tab, text="Mod Selection")
            populate_mod_list(game["InstallDir"], game_dir)
            notebook.select(mods_tab)

            # Save state
            last_state[sel_name] = {"launcher": "Vortex"}
            save_state(last_state)



        # -----------------------------
        # User Defined
        # -----------------------------
        elif mode_var.get() == "User Defined":
            exe = cmd_var.get().strip().strip('"')
            if not exe:
                messagebox.showerror("Error", "Please select an executable.")
                return

            shim_path = create_simple_shim(game_dir, exe)

            version_label.config(text="Current version: Modded (User Defined)")
            status_canvas.itemconfig(status_circle, fill="green")

            mode_combo.config(state="disabled")
            user_frame.pack_forget()
            create_btn.pack_forget()

            revert_btn.config(command=on_revert)
            revert_btn.pack(pady=10)
            copy_btn.pack(pady=10)

            # Save state
            last_state[sel_name] = {
                "launcher": "User Defined",
                "exe": exe
            }
            save_state(last_state)

            # Copy launch option
            launch_opts = f"\"{shim_path}\" %command%"
            root.clipboard_clear()
            root.clipboard_append(launch_opts)
            show_status("✅ User Defined launcher created (copied to clipboard).")








    def on_revert():
        sel_name = game_var.get()
        game = next(g for g in games if g["Name"] == sel_name)
        lib_path = os.path.dirname(game["Manifest"])
        game_dir = os.path.join(lib_path, "common", game["InstallDir"])

        leftovers = [
            "BepInEx",
            "doorstop_config.ini",
            "winhttp.dll",
            "version.dll",
            "ModLaunch.cmd",
            "modded"
        ]

        for item in leftovers:
            path = os.path.join(game_dir, item)
            if os.path.exists(path):
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)

        # Reset UI
        version_label.config(text="Current version: Vanilla")
        status_canvas.itemconfig(status_circle, fill="red")

        # ✅ Unlock dropdowns again
        mode_combo.config(state="readonly")
        profile_combo.config(state="readonly")

        # Hide revert + copy buttons
        revert_btn.pack_forget()
        revert_unlock_btn.pack_forget()
        copy_btn.pack_forget()

        # ✅ Clear saved state for this game
        if sel_name in last_state:
            del last_state[sel_name]
            save_state(last_state)

        # Update status UI
        update_status()
        show_status(f"✅ Restored {sel_name} to Vanilla.")




    def on_copy():
        sel_name = game_var.get()
        game = next(g for g in games if g["Name"] == sel_name)
        lib_path = os.path.dirname(game["Manifest"])
        game_dir = os.path.join(lib_path, "common", game["InstallDir"])
        shim_path = os.path.join(game_dir, "ModLaunch.cmd")
        if not os.path.exists(shim_path):
            messagebox.showerror("Error", "No shim found. Create a launcher first.")
            return
        launch_opts = f"\"{shim_path}\" %command%"
        root.clipboard_clear()
        root.clipboard_append(launch_opts)
        show_status("✅ Launch option copied to clipboard.")

    create_btn.config(command=on_ok)
    revert_btn.config(command=on_revert)
    copy_btn.config(command=on_copy)
    mode_var.trace("w", update_mode)
    game_var.trace("w", lambda *a: update_status())
    update_status()
    create_btn.pack(side="bottom", pady=10)

    def on_game_change(*args):
        update_status()
        if mode_var.get() == "Vortex":
            sel_name = game_var.get()
            game = next((g for g in games if g["Name"] == sel_name), None)
            if game:
                populate_mod_list(game["InstallDir"])

    game_var.trace("w", on_game_change)


    # ========================
    # Tab 2: Custom Games
    # ========================
    custom_tab = tk.Frame(notebook)
    notebook.add(custom_tab, text="Custom Games")

    # --- Custom game name ---
    tk.Label(custom_tab, text="Custom Game Name:").pack()
    custom_name_var = tk.StringVar()
    tk.Entry(custom_tab, textvariable=custom_name_var, width=40).pack(pady=5)

    # --- Base game selection ---
    tk.Label(custom_tab, text="Select Base Game or Custom:").pack()
    all_game_names = ["Custom Game"] + [g["Name"] for g in games]
    sel_game_var = tk.StringVar(value="Custom Game")
    sel_game_combo = ttk.Combobox(custom_tab, textvariable=sel_game_var,
                                  state="readonly", values=all_game_names, width=40)
    sel_game_combo.pack(pady=5)

    # --- Launcher selection ---
    tk.Label(custom_tab, text="Select Launcher:").pack(pady=(10, 2))
    mode_var2 = tk.StringVar(value="User Defined")
    mode_combo2 = ttk.Combobox(custom_tab, textvariable=mode_var2, state="readonly",
                               width=20,
                               values=["User Defined", "Thunderstore", "Vortex"])
    mode_combo2.pack(pady=5)

    # --- Thunderstore profile frame ---
    ts_profile_frame2 = tk.Frame(custom_tab)
    ts_profile_var2 = tk.StringVar()
    ts_profile_combo2 = ttk.Combobox(ts_profile_frame2, textvariable=ts_profile_var2,
                                     state="readonly", width=40)
    tk.Label(ts_profile_frame2, text="Thunderstore profile:").pack(side="left", padx=5)
    ts_profile_combo2.pack(side="left", padx=5)

    # --- Vortex profile frame ---
    vx_profile_frame2 = tk.Frame(custom_tab)
    vx_profile_var2 = tk.StringVar()
    vx_profile_combo2 = ttk.Combobox(vx_profile_frame2, textvariable=vx_profile_var2,
                                     state="readonly", width=40)
    tk.Label(vx_profile_frame2, text="Vortex profile:").pack(side="left", padx=5)
    vx_profile_combo2.pack(side="left", padx=5)

    # --- User-defined exe frame ---
    user_frame2 = tk.Frame(custom_tab)
    exe_var = tk.StringVar()
    exe_entry = tk.Entry(user_frame2, textvariable=exe_var, width=50)
    exe_entry.pack(side="left", padx=5)
    tk.Button(user_frame2, text="Browse…", command=lambda:
              exe_var.set(filedialog.askopenfilename(
                  filetypes=[("Executables", "*.exe *.bat *.cmd *.lnk"), ("All files", "*.*")])
              )).pack(side="left", padx=5)

    # --- Mode updater ---
    def update_mode2(*args):
        ts_profile_frame2.pack_forget()
        vx_profile_frame2.pack_forget()
        user_frame2.pack_forget()

        if sel_game_var.get() == "Custom Game":
            mode_combo2["values"] = ["User Defined"]
            mode_var2.set("User Defined")
            user_frame2.pack(pady=5)
            add_to_steam_btn.config(state="normal")

        else:
            mode_combo2["values"] = ["User Defined", "Thunderstore", "Vortex"]

            if mode_var2.get() == "Thunderstore":
                sel_game = next((g for g in games if g["Name"] == sel_game_var.get()), None)
                if sel_game:
                    profiles = get_thunderstore_profiles_any(sel_game["InstallDir"])
                    if profiles:
                        ts_profile_combo2["values"] = [os.path.basename(p) for p in profiles]
                        ts_profile_combo2.current(0)
                    else:
                        ts_profile_combo2["values"] = ["No profiles detected"]
                        ts_profile_combo2.current(0)
                ts_profile_frame2.pack(pady=5)
                add_to_steam_btn.config(state="normal")

            elif mode_var2.get() == "Vortex":
                sel_game = next((g for g in games if g["Name"] == sel_game_var.get()), None)
                if sel_game:
                    mods = get_vortex_downloads(sel_game["InstallDir"].lower())
                    if mods:
                        messagebox.showinfo("Mods Found", f"✅ Found {len(mods)} mods for {sel_game['Name']}.")
                        profile_name = simpledialog.askstring("Profile Name", "Enter a profile name for this mod set:")
                        if profile_name:
                            vx_profile_var2.set(profile_name)
                            populate_mod_list(sel_game["InstallDir"].lower())
                            notebook.select(mods_tab)
                            # Only enable Add to Steam after mods are valid
                            add_to_steam_btn.config(state="normal")
                        else:
                            add_to_steam_btn.config(state="disabled")
                    else:
                        messagebox.showwarning("No Mods", "No mods found for this game in Vortex downloads.")
                        add_to_steam_btn.config(state="disabled")
                else:
                    add_to_steam_btn.config(state="disabled")

            else:  # User Defined
                user_frame2.pack(pady=5)
                add_to_steam_btn.config(state="normal")






    # --- Add custom button logic ---
    def on_add_custom():
        name = custom_name_var.get().strip()
        if not name:
            messagebox.showerror("Error", "Please enter a custom game name.")
            return

        if sel_game_var.get() == "Custom Game":
            # User-specified exe
            exe = exe_var.get().strip().strip('"')
            if not exe:
                messagebox.showerror("Error", "Please select an executable.")
                return
            startdir = os.path.dirname(exe)
            shim_path = create_simple_shim(startdir, exe)
            add_nonsteam_shortcut(name, shim_path, startdir, "")
            refresh_custom_list()
            show_status(f"✅ Custom launcher '{name}' added. Restart Steam to see it.")
            return

        # Base game (Steam)
        sel_game = next((g for g in games if g["Name"] == sel_game_var.get()), None)
        lib_path = os.path.dirname(sel_game["Manifest"])
        game_dir = os.path.join(lib_path, "common", sel_game["InstallDir"])
        exe_path = find_game_exe(game_dir)

        safe_name = name.replace(" ", "_") or str(int(time.time()))
        custom_dir = os.path.join(lib_path, "common",
                                  sel_game["InstallDir"] + f"_customlaunch_{safe_name}")
        os.makedirs(custom_dir, exist_ok=True)

        if mode_var2.get() == "Thunderstore":
            profiles = get_thunderstore_profiles_any(sel_game["InstallDir"])
            sel_profile = ts_profile_var2.get()
            profile_path = next((p for p in profiles if os.path.basename(p) == sel_profile), None)
            if not profile_path:
                messagebox.showerror("Error", "No Thunderstore profile selected.")
                return
            shim_path = create_custom_shim_with_sync(custom_dir, exe_path, profile_path, game_dir)

        elif mode_var2.get() == "Vortex":
            gameid = sel_game["InstallDir"].lower()

            # Collect selected mods from Mod Selection tab
            selected_mods = [m for m, v in mod_vars.items() if v.get()]

            if not selected_mods:
                messagebox.showerror("Error", "No mods selected.")
                return

            # Deploy chosen mods into custom_dir/modded
            deploy_vortex_mods(gameid, custom_dir, selected_mods)

            # Create shim pointing to modded folder
            shim_path = create_custom_shim_with_sync(custom_dir, exe_path,
                                                     os.path.join(custom_dir, "modded"), game_dir)

        else:  # User Defined exe
            exe = exe_var.get().strip().strip('"')
            if not exe:
                messagebox.showerror("Error", "Please select an executable.")
                return
            shim_path = create_simple_shim(custom_dir, exe)

        add_nonsteam_shortcut(name, shim_path, custom_dir, "")
        refresh_custom_list()
        show_status(f"✅ Custom launcher '{name}' added for {sel_game['Name']}. Restart Steam to see it.")

    def toggle_mod(mod_name, var, gameid, game_dir):
        selected = [m for m, v in mod_vars.items() if v.get()]
        deploy_vortex_mods(gameid, game_dir, selected)

        version_label.config(
            text=f"Current version: Modded (Vortex, {len(selected)} mod{'s' if len(selected)!=1 else ''} enabled)"
        )

        sel_name = game_var.get()
        last_state[sel_name] = {
            "launcher": "Vortex",
            "enabled_mods": selected
        }
        save_state(last_state)




    # ========================
    # Tab 3: Manage Custom Launches
    # ========================
    manage_tab = tk.Frame(notebook)
    notebook.add(manage_tab, text="Manage Custom Launches")

    custom_listbox = tk.Listbox(manage_tab, width=60, height=15)
    custom_listbox.pack(pady=10)

    custom_map = {}

    def refresh_custom_list():
        custom_listbox.delete(0, tk.END)
        custom_map.clear()
        seen = set()

        for lib in libs:
            common_dir = os.path.join(lib, "common")
            if not os.path.exists(common_dir):
                continue

            for folder in os.listdir(common_dir):
                if "_customlaunch_" in folder:
                    path = os.path.join(common_dir, folder)
                    shim = os.path.join(path, "ModLaunch.cmd")
                    if not os.path.exists(shim):
                        continue

                    key = os.path.abspath(shim).lower()
                    if key in seen:
                        continue
                    seen.add(key)

                    try:
                        base, custom = folder.split("_customlaunch_", 1)
                    except ValueError:
                        base, custom = folder, "unknown"

                    display = f"{custom} (for {base})"
                    custom_map[display] = path
                    custom_listbox.insert(tk.END, display)

    def on_delete_custom():
        sel = custom_listbox.curselection()
        if not sel:
            messagebox.showerror("Error", "No custom launch selected.")
            return

        display = custom_listbox.get(sel[0])
        folder = custom_map[display]
        shim = os.path.join(folder, "ModLaunch.cmd")

        remove_nonsteam_shortcut(shim)

        if os.path.exists(folder):
            shutil.rmtree(folder)

        refresh_custom_list()
        show_status(f"❌ Custom launch '{display}' deleted. Restart Steam to see it.")

    def on_browse_custom():
        sel = custom_listbox.curselection()
        if not sel:
            messagebox.showerror("Error", "No custom launch selected.")
            return
        display = custom_listbox.get(sel[0])
        os.startfile(custom_map[display])

    btn_frame = tk.Frame(manage_tab)
    btn_frame.pack(pady=5)
    tk.Button(btn_frame, text="Browse", command=on_browse_custom).pack(side="left", padx=5)
    tk.Button(btn_frame, text="Delete Selected", command=on_delete_custom).pack(side="left", padx=5)

    refresh_custom_list()

    # --- Status message at bottom ---
    status_var = tk.StringVar(value="")
    status_label = tk.Label(root, textvariable=status_var, fg="green")
    status_label.pack(side="bottom", pady=5)

    def show_status(msg, duration=4000):
        status_var.set(msg)
        root.after(duration, lambda: status_var.set(""))

    # Restart Steam utility button
    def restart_steam():
        try:
            os.system("taskkill /IM steam.exe /F")
        except Exception:
            pass
        steam_root = get_steam_root()
        steam_exe = os.path.join(steam_root, "steam.exe")
        if os.path.exists(steam_exe):
            os.startfile(steam_exe)

    restart_btn = tk.Button(root, text="🔄 Restart Steam", command=restart_steam)
    restart_btn.pack(side="bottom", pady=2)
    
    
    
    
    
    # ========================
    # Tab 4: Mod Selection (created later when needed)
    # ========================
    mods_tab = tk.Frame(notebook)
    # ❌ don’t add it yet — only add inside update_status() or on_ok()


    tk.Label(mods_tab, text="Select mods to enable:").pack(pady=5)
    mods_listbox_frame = tk.Frame(mods_tab)
    mods_listbox_frame.pack(fill="both", expand=True)

    mod_vars = {}  # {mod_name: tk.BooleanVar()}

    def populate_mod_list(gameid, game_dir=None):
        for widget in mods_listbox_frame.winfo_children():
            widget.destroy()
        mod_vars.clear()

        mods = get_vortex_downloads(gameid)
        if not mods:
            tk.Label(mods_listbox_frame, text="❌ No mods found for this game.").pack()
            return False

        for m in mods:
            mod_name = os.path.basename(m)
            var = tk.BooleanVar(value=False)  # start unchecked

            def on_toggle(mod=mod_name, v=var):
                if game_dir:  # only redeploy when we know the target dir
                    toggle_mod(mod, v, gameid, game_dir)

            chk = tk.Checkbutton(
                mods_listbox_frame,
                text=mod_name,
                variable=var,
                command=on_toggle,   # <-- wire toggle here
                anchor="w",
                justify="left"
            )
            chk.pack(fill="x", padx=10, anchor="w")
            mod_vars[mod_name] = var

        return True




    root.mainloop()

if __name__ == "__main__":
    main()
