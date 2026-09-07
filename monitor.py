"""
monitor.py
Background monitoring engine.

Runs entirely on worker threads so the Tkinter UI thread is never
blocked. Ping results are written straight to the database, and
important events (device went offline / came back online / high
latency) are pushed onto a thread-safe queue that the UI polls with
root.after(...) to update itself safely.
"""

import threading
import time
import queue
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from netutils import ping_once

HIGH_LATENCY_THRESHOLD_MS = 150


class MonitorEngine:
    def __init__(self, db):
        self.db = db
        self.events = queue.Queue()  # UI polls this for live updates
        self._stop = threading.Event()
        self._thread = None
        self._pool = ThreadPoolExecutor(max_workers=25)
        self._continuous_stop_flags = {}  # device_id -> threading.Event

    # ---------------- lifecycle ----------------

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    # ---------------- main loop ----------------

    def _run_loop(self):
        while not self._stop.is_set():
            interval = float(self.db.get_setting("ping_interval_seconds", "5"))
            devices = [d for d in self.db.get_devices() if d["monitoring_enabled"]]

            futures = []
            for device in devices:
                futures.append(self._pool.submit(self._check_device, dict(device)))
            for f in futures:
                try:
                    f.result(timeout=10)
                except Exception:
                    pass

            # sleep in small increments so stop() is responsive
            slept = 0.0
            while slept < interval and not self._stop.is_set():
                time.sleep(0.25)
                slept += 0.25

    def _check_device(self, device):
        device_id = device["id"]
        timeout_ms = int(self.db.get_setting("ping_timeout_ms", "1000"))
        threshold = int(self.db.get_setting("failed_checks_before_offline", "3"))

        success, latency = ping_once(device["ip"], timeout_ms=timeout_ms)
        self.db.add_ping_result(device_id, success, latency)

        avg_latency = self.db.compute_avg_latency(device_id)
        packet_loss = self.db.compute_packet_loss(device_id)
        now_iso = datetime.now().isoformat(timespec="seconds")
        now_disp = datetime.now().strftime("%H:%M")

        prev_status = device["status"]
        consecutive_failures = device["consecutive_failures"] or 0

        update = {
            "last_latency": latency,
            "avg_latency": avg_latency,
            "packet_loss": packet_loss,
        }

        if success:
            update["last_seen"] = now_iso
            consecutive_failures = 0
            new_status = "ONLINE"

            if latency is not None and latency > HIGH_LATENCY_THRESHOLD_MS:
                self._emit_event(device_id, device["name"], "WARNING",
                                  f"{device['name']} high latency ({latency:.0f}ms)")

            if prev_status == "OFFLINE":
                went_offline_at = device["went_offline_at"]
                downtime_str = ""
                if went_offline_at:
                    try:
                        off_dt = datetime.fromisoformat(went_offline_at)
                        delta = datetime.now() - off_dt
                        mins = int(delta.total_seconds() // 60)
                        downtime_str = f" (downtime: {mins} min)" if mins > 0 else " (downtime: <1 min)"
                    except Exception:
                        pass
                self.db.add_event(device_id, device["name"], "ONLINE",
                                   f"{device['name']} came ONLINE{downtime_str}")
                self._emit_event(device_id, device["name"], "ONLINE",
                                  f"{now_disp} {device['name']} came ONLINE{downtime_str}")
                update["went_offline_at"] = None
        else:
            consecutive_failures += 1
            if consecutive_failures >= threshold:
                new_status = "OFFLINE"
                if prev_status != "OFFLINE":
                    update["went_offline_at"] = now_iso
                    self.db.add_event(device_id, device["name"], "OFFLINE",
                                       f"{device['name']} went OFFLINE")
                    self._emit_event(device_id, device["name"], "OFFLINE",
                                      f"{now_disp} {device['name']} went OFFLINE")
            else:
                # Still within grace period, keep previous status label
                new_status = prev_status if prev_status in ("ONLINE", "OFFLINE") else "UNKNOWN"

        update["status"] = new_status
        update["consecutive_failures"] = consecutive_failures
        self.db.update_device(device_id, **update)

        self._emit_event(device_id, device["name"], "PING_UPDATE", "", silent=True)

    def _emit_event(self, device_id, device_name, event_type, message, silent=False):
        self.events.put({
            "device_id": device_id,
            "device_name": device_name,
            "type": event_type,
            "message": message,
            "silent": silent,
        })

    # ---------------- continuous ping (per-device live stream) ----------------

    def start_continuous_ping(self, device_id, ip, timeout_ms, line_cb, stats_cb, stop_check):
        """
        Runs in a dedicated thread (call this inside a threading.Thread).
        Calls line_cb(str) for each ping line, stats_cb(dict) after each
        ping with running statistics, until stop_check() returns True.
        """
        sent = 0
        received = 0
        latencies = []
        line_cb(f"Pinging {ip}...\n")

        while not stop_check():
            sent += 1
            success, latency = ping_once(ip, timeout_ms=timeout_ms)
            if success:
                received += 1
                if latency is not None:
                    latencies.append(latency)
                line_cb(f"Reply from {ip}: time={latency:.0f}ms\n" if latency is not None
                        else f"Reply from {ip}: time<1ms\n")
            else:
                line_cb(f"Request timed out for {ip}.\n")

            lost = sent - received
            loss_pct = round((lost / sent) * 100, 1) if sent else 0.0
            avg = round(sum(latencies) / len(latencies), 1) if latencies else 0.0
            stats_cb({
                "sent": sent,
                "received": received,
                "lost": lost,
                "loss_pct": loss_pct,
                "avg_latency": avg,
            })
            time.sleep(1)
