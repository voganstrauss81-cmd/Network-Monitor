# Network Monitor

A lightweight Windows network monitoring app (dashboard, automatic
ping, offline detection, alerts, history, network map, subnet scanning)
inspired by MikroTik WinBox / The Dude.

**You do not need Python, Visual Studio, or any developer software
installed on your PC.** GitHub's own servers build the `.exe` for you.
You just download the finished file.

---

## What you need to do (step by step)

You only need a free GitHub account. Everything else happens automatically.

### Step 1 — Create a GitHub account (skip if you already have one)

Go to https://github.com/signup and create a free account.

### Step 2 — Create a new repository

1. Once logged in, click the **+** icon in the top-right corner → **New repository**.
2. Name it `NetworkMonitor` (any name is fine).
3. Leave it **Public** or **Private**, either works.
4. Do **not** check "Add a README" (we already have one).
5. Click **Create repository**.

### Step 3 — Upload these files

1. On your new (empty) repository page, click **"uploading an existing file"**
   (this link appears in the middle of the page for a brand-new repo).
2. Drag and drop **the entire contents** of this project folder into the
   upload box — that means:
   - the `app` folder
   - the `.github` folder
   - `requirements.txt`
   - `README.md`
   - `.gitignore`

   > **Important:** Make sure the `.github` folder (with `workflows/build.yml`
   > inside it) actually gets uploaded. Some file managers hide folders
   > starting with a dot — if drag-and-drop doesn't pick it up, use the
   > "choose your files" link instead and manually select it, or upload
   > the whole project as a `.zip` and GitHub will let you extract it
   > (see the note at the bottom if you're unsure).
3. Scroll down and click the green **"Commit changes"** button.

### Step 4 — Let GitHub build the EXE automatically

As soon as your files are uploaded, GitHub notices the workflow file and
starts building automatically. To watch it happen:

1. Click the **"Actions"** tab near the top of your repository page.
2. You'll see a workflow run called **"Build Windows EXE"** with a yellow
   dot (running) — click it.
3. Wait for it to finish. This usually takes **2–5 minutes**. It's done
   when the dot turns into a green checkmark ✅.

If you ever want to re-run it manually: go to the **Actions** tab →
**Build Windows EXE** (left sidebar) → **Run workflow** button → **Run workflow**.

### Step 5 — Download NetworkMonitor.exe

1. Still on that finished (green ✅) workflow run page, scroll down to
   the **"Artifacts"** section at the bottom.
2. Click **`NetworkMonitor-Windows-EXE`** to download a `.zip` file.
3. Open the `.zip` file and pull out **`NetworkMonitor.exe`** — put it
   anywhere on your Windows PC (Desktop is fine).

### Step 6 — Run it

Double-click `NetworkMonitor.exe`. That's it — no installer, no setup
wizard, no Python. The dashboard opens immediately.

> Windows SmartScreen may show a blue "Windows protected your PC"
> warning the first time, because the file isn't digitally signed by a
> paid certificate (this is normal for small independent apps). Click
> **"More info"** → **"Run anyway"**.

---

## Using the app

- **Dashboard** — overview of all devices and their live status.
- **+ Add Device** — add a device manually (name, IP, type, description).
- **Scan Network** — enter a subnet like `192.168.1.0/24` and discover
  devices automatically, then add the ones you want.
- **Right-click any device** — Ping, Continuous Ping, Edit, Copy IP,
  View History, Disable Monitoring, Delete.
- **Network Map** — drag devices around to lay out your network visually.
- **Alerts** — a live log of devices going offline/online and high-latency warnings.
- **History** — per-device ping history and offline/online events.
- **Settings** — ping interval, timeout, failure threshold, theme, notifications.

All of your devices and history are saved automatically in a local
database file, so closing and reopening the app keeps everything.

---

## Every time you change something in the future

If you (or I, if you paste updated code back to me) ever change the
application source code:

1. Upload the changed files the same way (Step 3), or use GitHub's
   "Add file → Upload files" on the existing repo.
2. GitHub automatically re-runs the build (Step 4).
3. Download the new `NetworkMonitor.exe` from the newest "Actions" run (Step 5).

You never need to install anything locally, ever.

---

## What the automated build actually does

Defined in `.github/workflows/build.yml`, running on a temporary
Windows machine that GitHub provides for free:

1. Checks out the source code.
2. Installs Python and PyInstaller *(only on GitHub's temporary machine
   — never on your PC)*.
3. Runs an automated self-test of the core logic (database, ping,
   network scan) before even attempting to compile.
4. Compiles everything into one self-contained `NetworkMonitor.exe`
   using PyInstaller's `--onefile` mode (the Python runtime is bundled
   inside the .exe, so nothing extra needs to be installed on the
   machine that runs it).
5. Runs the **compiled .exe's** own self-test to confirm the built
   file actually works, not just the source.
6. Launches the real application window on the Windows build machine
   and confirms it opens without crashing.
7. Packages `NetworkMonitor.exe` and uploads it as a downloadable
   "artifact" — the file you get in Step 5 above.

If any of these checks fail, the build is marked ❌ failed and no
broken .exe is produced — so a green checkmark means the file that's
waiting for you has been verified to actually run.

---

## Project structure

```
NetworkMonitor/
├── app/
│   ├── main.py        Entry point (also contains --selftest for CI)
│   ├── database.py    SQLite storage (devices, ping history, events, settings)
│   ├── netutils.py    Ping + subnet scan logic
│   ├── monitor.py     Background monitoring engine (runs on worker threads)
│   └── ui.py           Tkinter desktop interface (dashboard, map, alerts, etc.)
├── requirements.txt    Build-only dependency (PyInstaller)
├── .github/workflows/build.yml   The automated build/test/package pipeline
└── README.md            This file
```

## Notes and honest limitations

- The compiled `.exe` typically ends up around 15–30 MB (the Python
  runtime is bundled in), which is light for a modern app but larger
  than a hand-written C++ tool would be. It still runs fine on older
  Windows 10/11 PCs.
- Ping accuracy depends on Windows' own `ping.exe`, which the app calls
  directly — no raw sockets, no admin rights required.
- The Network Map is a simple, draggable node view (not a full
  topology auto-discovery tool).
- Notifications are shown inside the app's Alerts tab; there are no
  Windows system-tray popups in this version.
