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
    candidates = [
        os.getenv("APPDATA"),
        os.getenv("LOCALAPPDATA"),
        os.path.join(os.getenv("USERPROFILE"), "AppData", "Roaming"),
        os.path.join(os.getenv("USERPROFILE"), "AppData", "Local"),
    ]
    candidates = [c for c in candidates if c and os.path.exists(c)]
    for c in candidates:
        r2 = os.path.join(c, "r2modmanPlus-local")
        tmm = os.path.join(c, "Thunderstore Mod Manager", "DataFolder")
        if os.path.exists(r2):
            bases.append(r2)
        if os.path.exists(tmm):
            bases.append(tmm)
    return list(set(bases))

def get_thunderstore_profiles(game_dir_name):
    profiles = []
    for base in get_thunderstore_bases():
        game_path = os.path.join(base, game_dir_name, "profiles")
        if os.path.exists(game_path):
            for p in os.listdir(game_path):
                full = os.path.join(game_path, p)
                if os.path.isdir(full) and os.path.exists(os.path.join(full, "BepInEx")):
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
    status_label = tk.Label(status_frame, text="Current version: Vanilla")
    status_label.pack(side="left", padx=5)
    status_canvas = tk.Canvas(status_frame, width=12, height=12, highlightthickness=0)
    status_circle = status_canvas.create_oval(2, 2, 10, 10, fill="red")
    status_canvas.pack(side="left")
    status_frame.pack(pady=5)

    tk.Label(steam_tab, text="Select Launcher:").pack(pady=(10, 2))
    mode_var = tk.StringVar(value="User Defined")
    mode_combo = ttk.Combobox(steam_tab, textvariable=mode_var, state="readonly",
                              values=["User Defined", "Thunderstore"], width=20)

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

    exe_label = tk.Label(steam_tab, text="Detected exe: (not yet selected)")
    create_btn = tk.Button(steam_tab, text="Create Launcher")
    revert_btn = tk.Button(steam_tab, text="Restore Vanilla")
    copy_btn = tk.Button(steam_tab, text="Copy Launch Option")

    def update_status():
        sel_name = game_var.get()
        game = next(g for g in games if g["Name"] == sel_name)
        lib_path = os.path.dirname(game["Manifest"])
        game_dir = os.path.join(lib_path, "common", game["InstallDir"])
        if os.path.exists(os.path.join(game_dir, "ModLaunch.cmd")):
            status_label.config(text="Current version: Modded")
            status_canvas.itemconfig(status_circle, fill="green")
            mode_combo.pack_forget()
            profile_frame.pack_forget()
            user_frame.pack_forget()
            exe_label.pack_forget()
            create_btn.config(state="disabled")
            copy_btn.pack(pady=5)
            revert_btn.pack(pady=10)
        else:
            status_label.config(text="Current version: Vanilla")
            status_canvas.itemconfig(status_circle, fill="red")
            mode_combo.pack(pady=5)
            exe_label.pack(pady=5)
            create_btn.config(state="normal")
            copy_btn.pack_forget()
            revert_btn.pack_forget()
            update_mode()

    def update_mode(*args):
        for w in (profile_frame, user_frame):
            w.pack_forget()
        if mode_var.get() == "Thunderstore":
            sel_name = game_var.get()
            game = next((g for g in games if g["Name"] == sel_name), None)
            if game:
                profiles = get_thunderstore_profiles(game["InstallDir"])
                if profiles:
                    profile_combo["values"] = [os.path.basename(p) for p in profiles]
                    profile_combo.current(0)
                else:
                    profile_combo["values"] = ["No profiles detected"]
                    profile_combo.current(0)
            profile_frame.pack(pady=5)
        else:
            user_frame.pack(pady=5)

    def on_ok():
        sel_name = game_var.get()
        game = next(g for g in games if g["Name"] == sel_name)
        lib_path = os.path.dirname(game["Manifest"])
        game_dir = os.path.join(lib_path, "common", game["InstallDir"])
        exe_path = find_game_exe(game_dir)
        if not exe_path:
            messagebox.showerror("Error", f"Could not find any .exe in {game_dir}")
            return

        if mode_var.get() == "Thunderstore":
            profiles = get_thunderstore_profiles(game["InstallDir"])
            if not profiles:
                messagebox.showerror("Error", "No Thunderstore profiles found for this game.")
                return
            sel_profile = profile_var.get()
            profile_path = next((p for p in profiles if os.path.basename(p) == sel_profile), None)
            shim_path = create_shim_with_sync(game_dir, exe_path, profile_path)
        else:
            mod_cmd = cmd_var.get().strip()
            if not mod_cmd:
                messagebox.showerror("Error", "Please select or enter a modded command.")
                return
            shim_path = create_simple_shim(game_dir, mod_cmd)

        exe_label.config(text=f"Detected exe: {exe_path}")
        launch_opts = f"\"{shim_path}\" %command%"
        root.clipboard_clear()
        root.clipboard_append(launch_opts)
        show_status(f"✅ Launcher created for {sel_name}. Paste launch options in Steam.")

        update_status()

    def on_revert():
        sel_name = game_var.get()
        game = next(g for g in games if g["Name"] == sel_name)
        lib_path = os.path.dirname(game["Manifest"])
        game_dir = os.path.join(lib_path, "common", game["InstallDir"])
        leftovers = ["BepInEx","doorstop_config.ini","winhttp.dll","version.dll","ModLaunch.cmd"]
        for item in leftovers:
            path = os.path.join(game_dir, item)
            if os.path.exists(path):
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
        update_status()
        show_status("✅ Vanilla restored. Clear Steam launch options manually.")

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

    # ========================
    # Tab 2: Custom Games
    # ========================
    custom_tab = tk.Frame(notebook)
    notebook.add(custom_tab, text="Custom Games")

    tk.Label(custom_tab, text="Custom Game Name:").pack()
    custom_name_var = tk.StringVar()
    tk.Entry(custom_tab, textvariable=custom_name_var, width=40).pack(pady=5)

    tk.Label(custom_tab, text="Select Base Game or Custom:").pack()
    all_game_names = ["Custom Game"] + [g["Name"] for g in games]
    sel_game_var = tk.StringVar(value="Custom Game")
    sel_game_combo = ttk.Combobox(custom_tab, textvariable=sel_game_var,
                                  state="readonly", values=all_game_names, width=40)
    sel_game_combo.pack(pady=5)
    
    tk.Label(custom_tab, text="Select Launcher:").pack(pady=(10, 2))
    mode_var2 = tk.StringVar(value="User Defined")
    mode_combo2 = ttk.Combobox(custom_tab, textvariable=mode_var2, state="readonly", width=20)
    mode_combo2.pack(pady=5)


    profile_frame2 = tk.Frame(custom_tab)
    profile_var2 = tk.StringVar()
    profile_combo2 = ttk.Combobox(profile_frame2, textvariable=profile_var2,
                                  state="readonly", width=40)
    tk.Label(profile_frame2, text="Thunderstore profile:").pack(side="left", padx=5)
    profile_combo2.pack(side="left", padx=5)

    user_frame2 = tk.Frame(custom_tab)
    exe_var = tk.StringVar()
    exe_entry = tk.Entry(user_frame2, textvariable=exe_var, width=50)
    exe_entry.pack(side="left", padx=5)
    tk.Button(user_frame2, text="Browse…", command=lambda:
              exe_var.set(filedialog.askopenfilename(
                  filetypes=[("Executables", "*.exe *.bat *.cmd *.lnk"), ("All files", "*.*")])
              )).pack(side="left", padx=5)

    def update_mode2(*args):
        profile_frame2.pack_forget()
        user_frame2.pack_forget()

        # If it's a pure Custom Game → only allow "User Defined"
        if sel_game_var.get() == "Custom Game":
            mode_combo2["values"] = ["User Defined"]
            mode_var2.set("User Defined")
            user_frame2.pack(pady=5)

        else:
            # Normal Steam game → allow both
            mode_combo2["values"] = ["User Defined", "Thunderstore"]

            if mode_var2.get() == "Thunderstore":
                sel_game = next((g for g in games if g["Name"] == sel_game_var.get()), None)
                if sel_game:
                    profiles = get_thunderstore_profiles(sel_game["InstallDir"])
                    if profiles:
                        profile_combo2["values"] = [os.path.basename(p) for p in profiles]
                        profile_combo2.current(0)
                    else:
                        profile_combo2["values"] = ["No profiles detected"]
                        profile_combo2.current(0)
                profile_frame2.pack(pady=5)
            else:
                user_frame2.pack(pady=5)


    mode_var2.trace("w", update_mode2)
    sel_game_var.trace("w", update_mode2)
    update_mode2()

    def on_add_custom():
        name = custom_name_var.get().strip()
        if not name:
            messagebox.showerror("Error", "Please enter a custom game name.")
            return

        if sel_game_var.get() == "Custom Game":
            exe = exe_var.get().strip().strip('"')
            if not exe:
                messagebox.showerror("Error", "Please select an executable.")
                return
            startdir = os.path.dirname(exe)
            shim_path = create_simple_shim(startdir, exe)

            add_nonsteam_shortcut(name, shim_path, startdir, "")
            refresh_custom_list()
            show_status(f"✅ Custom launcher '{name}' added. Restart Steam to see it.")

        else:
            sel_game = next((g for g in games if g["Name"] == sel_game_var.get()), None)
            lib_path = os.path.dirname(sel_game["Manifest"])
            game_dir = os.path.join(lib_path, "common", sel_game["InstallDir"])

            safe_name = custom_name_var.get().strip().replace(" ", "_")
            if not safe_name:
                safe_name = str(int(time.time()))

            custom_dir = os.path.join(lib_path, "common",
                                      sel_game["InstallDir"] + f"_customlaunch_{safe_name}")
            os.makedirs(custom_dir, exist_ok=True)

            exe_path = find_game_exe(game_dir)

            if mode_var2.get() == "Thunderstore":
                profiles = get_thunderstore_profiles(sel_game["InstallDir"])
                if not profiles:
                    messagebox.showerror("Error", "No Thunderstore profiles found.")
                    return
                sel_profile = profile_var2.get()
                profile_path = next((p for p in profiles if os.path.basename(p) == sel_profile), None)
                shim_path = create_custom_shim_with_sync(custom_dir, exe_path, profile_path, game_dir)
            else:
                exe = exe_var.get().strip().strip('"')
                if not exe:
                    messagebox.showerror("Error", "Please select an executable.")
                    return
                shim_path = create_simple_shim(custom_dir, exe)

            add_nonsteam_shortcut(name, shim_path, custom_dir, "")
            refresh_custom_list()
            show_status(f"✅ Custom launcher '{name}' added for {sel_game['Name']}. Restart Steam to see it.")

    tk.Button(custom_tab, text="Add to Steam", command=on_add_custom).pack(side="bottom", pady=15)

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

    root.mainloop()

if __name__ == "__main__":
    main()
