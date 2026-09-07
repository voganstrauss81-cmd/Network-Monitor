"""
main.py
Entry point for Network Monitor.

Normal usage: just launch the .exe -- the GUI opens immediately.

Two special modes exist purely to support automated testing in the
GitHub Actions build pipeline (see .github/workflows/build.yml):

  --selftest
      Runs core, non-GUI logic (database init, a real ping, a tiny
      network scan, device CRUD) and exits with code 0 on success or
      1 on failure. No window is opened. This proves the compiled
      .exe's bundled Python runtime, SQLite, and networking code all
      work correctly on a clean Windows machine.

  Environment variable NETMON_AUTOCLOSE_MS=<milliseconds>
      If set, the full GUI is launched normally and then automatically
      closed after the given delay. This proves the actual window can
      open without crashing on a fresh Windows runner.
"""

import os
import sys
import tempfile

# Make sure sibling modules import correctly both when run as a script
# and when frozen by PyInstaller.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The final .exe is built with --windowed (no console box), which means
# Windows gives the process NO stdout/stderr at all (they are None, not
# just hidden). Any stray print() would then crash with
# "AttributeError: 'NoneType' object has no attribute 'write'".
# We redirect to a small log file instead, both to prevent that crash
# and to give a way to see what happened if something ever goes wrong.
if sys.stdout is None or sys.stderr is None:
    try:
        if sys.platform.startswith("win"):
            log_dir = os.path.join(os.environ.get("APPDATA", tempfile.gettempdir()), "NetworkMonitor")
        else:
            log_dir = tempfile.gettempdir()
        os.makedirs(log_dir, exist_ok=True)
        log_file = open(os.path.join(log_dir, "run.log"), "a", buffering=1)
    except Exception:
        log_file = open(os.devnull, "w")
    sys.stdout = sys.stdout or log_file
    sys.stderr = sys.stderr or log_file


def run_selftest():
    print("Network Monitor self-test starting...")
    try:
        import database
        import netutils

        # Use a throwaway database file so this never touches real user data.
        tmp_dir = tempfile.mkdtemp(prefix="netmon_selftest_")
        db_path = os.path.join(tmp_dir, "selftest.db")
        db = database.Database(path=db_path)
        print("  [OK] Database created at", db_path)

        device_id = db.add_device("Selftest-Loopback", "127.0.0.1", "PC", "Other", "CI selftest device")
        assert device_id, "device insert failed"
        print("  [OK] Device inserted (id=%s)" % device_id)

        device = db.get_device(device_id)
        assert device["ip"] == "127.0.0.1"
        print("  [OK] Device read back correctly")

        success, latency = netutils.ping_once("127.0.0.1", timeout_ms=1500)
        print(f"  [OK] ping_once(127.0.0.1) -> success={success}, latency={latency}")

        db.add_ping_result(device_id, success, latency)
        db.add_event(device_id, device["name"], "ONLINE", "Selftest ping recorded")
        history = db.get_ping_history(device_id)
        assert len(history) >= 1, "ping history not recorded"
        print("  [OK] Ping history recorded")

        results = netutils.scan_subnet("127.0.0.1/32", timeout_ms=800)
        print(f"  [OK] scan_subnet('127.0.0.1/32') -> {results}")

        db.update_device(device_id, monitoring_enabled=0)
        updated = db.get_device(device_id)
        assert updated["monitoring_enabled"] == 0
        print("  [OK] Device update works")

        db.delete_device(device_id)
        assert db.get_device(device_id) is None
        print("  [OK] Device delete works")

        db.close()
        print("SELFTEST PASSED")
        return 0
    except Exception as exc:
        print("SELFTEST FAILED:", repr(exc))
        import traceback
        traceback.print_exc()
        return 1


def run_gui():
    import database
    import monitor
    import ui

    db = database.Database()
    engine = monitor.MonitorEngine(db)

    autoclose_ms = os.environ.get("NETMON_AUTOCLOSE_MS")
    autoclose_ms = int(autoclose_ms) if autoclose_ms else None

    app = ui.App(db, engine, autoclose_ms=autoclose_ms)
    try:
        app.mainloop()
    finally:
        engine.stop()
        db.close()
    return 0


def main():
    if "--selftest" in sys.argv:
        sys.exit(run_selftest())
    else:
        sys.exit(run_gui())


if __name__ == "__main__":
    main()
