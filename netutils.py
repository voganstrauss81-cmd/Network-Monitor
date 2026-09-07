"""
netutils.py
Low-level networking helpers: single ping, and subnet scanning.

Uses the OS 'ping' command (Windows syntax) rather than raw sockets so
that no extra permissions or third-party packages are required at
runtime -- this keeps the compiled .exe small and dependency-free.
"""

import re
import socket
import subprocess
import sys
import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed

IS_WINDOWS = sys.platform.startswith("win")

# Hide the console window that would otherwise flash on screen each
# time we shell out to 'ping.exe' from the GUI app.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_TIME_RE = re.compile(r"time[=<]\s*([\d.]+)\s*ms", re.IGNORECASE)


def ping_once(ip, timeout_ms=1000):
    """
    Send a single ICMP echo request to `ip`.

    Returns (success: bool, latency_ms: float or None).
    Works cross-platform but is tuned for the Windows ping.exe output
    format, which is what the compiled application will run against.
    """
    timeout_ms = max(int(timeout_ms), 100)
    if IS_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
    else:
        # Fallback used only when developing/testing on non-Windows hosts.
        timeout_s = max(1, round(timeout_ms / 1000))
        cmd = ["ping", "-c", "1", "-W", str(timeout_s), ip]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=(timeout_ms / 1000.0) + 1.5,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception:
        return False, None

    output = (result.stdout or "") + (result.stderr or "")

    if result.returncode != 0:
        # Still try to parse in case of partial success reporting
        match = _TIME_RE.search(output)
        if match:
            return True, float(match.group(1))
        return False, None

    match = _TIME_RE.search(output)
    if match:
        return True, float(match.group(1))

    # Windows prints "time<1ms" sometimes without matching due to locale;
    # treat a 0% loss report as a success with unknown latency.
    if "TTL=" in output.upper() or "ttl=" in output:
        return True, 0.0

    return False, None


def get_local_subnet_guess():
    """Best-effort guess of the local /24 subnet, e.g. '192.168.1.0/24'."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        parts = local_ip.split(".")
        return f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
    except Exception:
        return "192.168.1.0/24"


def scan_subnet(cidr, timeout_ms=500, max_workers=60, progress_cb=None, stop_flag=None):
    """
    Ping-sweep a CIDR range and return a list of dicts for hosts that
    responded: [{"ip": "192.168.1.5", "latency_ms": 1.2}, ...]

    progress_cb(done, total) is called after each host is checked.
    stop_flag is an optional callable; if it returns True, scanning
    stops early.
    """
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return []

    hosts = list(network.hosts())
    total = len(hosts)
    found = []
    done = 0

    def check(ip_obj):
        ip = str(ip_obj)
        ok, latency = ping_once(ip, timeout_ms=timeout_ms)
        return ip, ok, latency

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(check, h): h for h in hosts}
        for future in as_completed(futures):
            if stop_flag and stop_flag():
                break
            ip, ok, latency = future.result()
            done += 1
            if ok:
                found.append({"ip": ip, "latency_ms": latency})
            if progress_cb:
                progress_cb(done, total)

    found.sort(key=lambda d: tuple(int(p) for p in d["ip"].split(".")))
    return found
