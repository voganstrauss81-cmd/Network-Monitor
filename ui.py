"""
ui.py
Tkinter desktop UI for Network Monitor.

Sidebar navigation between: Dashboard, Devices, Network Map, Alerts,
History, Settings. All monitoring work happens on background threads
(see monitor.py); this module only ever touches Tkinter widgets from
the main thread, using root.after() polling to stay responsive.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import webbrowser
from datetime import datetime

from netutils import ping_once, scan_subnet, get_local_subnet_guess

DEVICE_TYPES = ["Router", "Switch", "Printer", "Camera", "PC", "Server", "Access Point", "Other"]
DEVICE_GROUPS = ["Printers", "Cameras", "PCs", "Servers", "Routers", "Wi-Fi", "Other"]

COLORS = {
    "bg": "#1b1f27",
    "sidebar": "#151920",
    "panel": "#232833",
    "panel_light": "#2b3140",
    "text": "#e6e9ef",
    "muted": "#8b93a7",
    "accent": "#3b82f6",
    "online": "#22c55e",
    "offline": "#ef4444",
    "warning": "#f59e0b",
    "unknown": "#8b93a7",
}


class App(tk.Tk):
    def __init__(self, db, monitor, autoclose_ms=None):
        super().__init__()
        self.db = db
        self.monitor = monitor
        self.title("Network Monitor")
        self.geometry("1180x720")
        self.minsize(980, 620)
        self.configure(bg=COLORS["bg"])

        self._selected_device_id = None
        self._map_positions = {}
        self._drag_data = {"item": None, "x": 0, "y": 0}
        self._continuous_windows = {}

        self._build_style()
        self._build_layout()
        self.show_frame("Dashboard")

        self.monitor.start()
        self.after(500, self._poll_events)
        self.after(1000, self._refresh_current_frame)

        if autoclose_ms:
            self.after(autoclose_ms, self.destroy)

    # ---------------- style / layout scaffolding ----------------

    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=COLORS["panel"])
        style.configure("Sidebar.TFrame", background=COLORS["sidebar"])
        style.configure("TLabel", background=COLORS["panel"], foreground=COLORS["text"])
        style.configure("Muted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"])
        style.configure("Card.TLabel", background=COLORS["panel_light"], foreground=COLORS["text"])
        style.configure("Sidebar.TButton", background=COLORS["sidebar"], foreground=COLORS["text"],
                         borderwidth=0, focusthickness=0, padding=10, anchor="w")
        style.map("Sidebar.TButton", background=[("active", COLORS["panel"])])
        style.configure("Accent.TButton", background=COLORS["accent"], foreground="white", padding=8)
        style.map("Accent.TButton", background=[("active", "#2563eb")])
        style.configure("Treeview", background=COLORS["panel"], fieldbackground=COLORS["panel"],
                         foreground=COLORS["text"], rowheight=26, borderwidth=0)
        style.configure("Treeview.Heading", background=COLORS["panel_light"],
                         foreground=COLORS["text"], borderwidth=0)
        style.map("Treeview", background=[("selected", COLORS["accent"])])
        style.configure("TNotebook", background=COLORS["panel"])
        style.configure("TCombobox", fieldbackground=COLORS["panel_light"], foreground="black")

    def _build_layout(self):
        self.sidebar = ttk.Frame(self, style="Sidebar.TFrame", width=190)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        title = tk.Label(self.sidebar, text="Network\nMonitor", bg=COLORS["sidebar"],
                          fg="white", font=("Segoe UI", 14, "bold"), justify="left")
        title.pack(anchor="w", padx=16, pady=(20, 24))

        self.nav_buttons = {}
        for name in ["Dashboard", "Devices", "Network Map", "Alerts", "History", "Settings"]:
            btn = ttk.Button(self.sidebar, text=name, style="Sidebar.TButton",
                              command=lambda n=name: self.show_frame(n))
            btn.pack(fill="x", padx=8, pady=2)
            self.nav_buttons[name] = btn

        self.container = ttk.Frame(self, style="TFrame")
        self.container.pack(side="right", fill="both", expand=True)

        self.frames = {}
        self.frames["Dashboard"] = DashboardFrame(self.container, self)
        self.frames["Devices"] = DevicesFrame(self.container, self)
        self.frames["Network Map"] = NetworkMapFrame(self.container, self)
        self.frames["Alerts"] = AlertsFrame(self.container, self)
        self.frames["History"] = HistoryFrame(self.container, self)
        self.frames["Settings"] = SettingsFrame(self.container, self)

        for frame in self.frames.values():
            frame.place(x=0, y=0, relwidth=1, relheight=1)

        self.current_frame_name = None

    def show_frame(self, name):
        self.current_frame_name = name
        self.frames[name].tkraise()
        refresh = getattr(self.frames[name], "on_show", None)
        if refresh:
            refresh()

    def _refresh_current_frame(self):
        frame = self.frames.get(self.current_frame_name)
        if frame and hasattr(frame, "refresh"):
            try:
                frame.refresh()
            except Exception:
                pass
        self.after(2000, self._refresh_current_frame)

    def _poll_events(self):
        drained = []
        try:
            while True:
                drained.append(self.monitor.events.get_nowait())
        except Exception:
            pass

        alerts_frame = self.frames.get("Alerts")
        for ev in drained:
            if not ev.get("silent") and alerts_frame:
                alerts_frame.add_live_event(ev)

        self.after(500, self._poll_events)

    def open_add_device_dialog(self, prefill_ip=None):
        DeviceDialog(self, self.db, on_saved=self._on_device_changed, prefill_ip=prefill_ip)

    def open_edit_device_dialog(self, device_id):
        device = self.db.get_device(device_id)
        if device:
            DeviceDialog(self, self.db, on_saved=self._on_device_changed, device=device)

    def _on_device_changed(self):
        for frame in self.frames.values():
            if hasattr(frame, "refresh"):
                try:
                    frame.refresh()
                except Exception:
                    pass

    def open_continuous_ping(self, device_id):
        if device_id in self._continuous_windows and self._continuous_windows[device_id].winfo_exists():
            self._continuous_windows[device_id].lift()
            return
        device = self.db.get_device(device_id)
        if not device:
            return
        win = ContinuousPingWindow(self, self.db, dict(device))
        self._continuous_windows[device_id] = win


# ==================================================================
# Dashboard
# ==================================================================

class DashboardFrame(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.db = app.db

        header = ttk.Frame(self)
        header.pack(fill="x", padx=20, pady=(20, 10))
        tk.Label(header, text="Dashboard", bg=COLORS["panel"], fg="white",
                  font=("Segoe UI", 18, "bold")).pack(side="left")

        self.cards_frame = ttk.Frame(self)
        self.cards_frame.pack(fill="x", padx=20, pady=(0, 15))
        self.card_labels = {}
        for key, label in [("total", "Total Devices"), ("online", "Online"),
                            ("offline", "Offline"), ("warnings", "Warnings")]:
            card = tk.Frame(self.cards_frame, bg=COLORS["panel_light"], padx=16, pady=12)
            card.pack(side="left", padx=(0, 12), fill="x", expand=True)
            tk.Label(card, text=label, bg=COLORS["panel_light"], fg=COLORS["muted"],
                      font=("Segoe UI", 9)).pack(anchor="w")
            val = tk.Label(card, text="0", bg=COLORS["panel_light"], fg="white",
                            font=("Segoe UI", 20, "bold"))
            val.pack(anchor="w")
            self.card_labels[key] = val

        self.table = DeviceTable(self, app, show_add_button=True)
        self.table.pack(fill="both", expand=True, padx=20, pady=(0, 20))

    def on_show(self):
        self.refresh()

    def refresh(self):
        devices = self.db.get_devices()
        total = len(devices)
        online = sum(1 for d in devices if d["status"] == "ONLINE")
        offline = sum(1 for d in devices if d["status"] == "OFFLINE")
        warnings = sum(1 for d in devices if d["status"] == "ONLINE" and (d["last_latency"] or 0) > 150)
        self.card_labels["total"].config(text=str(total))
        self.card_labels["online"].config(text=str(online))
        self.card_labels["offline"].config(text=str(offline))
        self.card_labels["warnings"].config(text=str(warnings))
        self.table.refresh()


# ==================================================================
# Reusable device table (used by Dashboard + Devices)
# ==================================================================

class DeviceTable(ttk.Frame):
    COLS = ("name", "ip", "type", "status", "ping", "loss", "last_seen")
    HEADS = {"name": "Device Name", "ip": "IP Address", "type": "Type", "status": "Status",
             "ping": "Ping", "loss": "Packet Loss", "last_seen": "Last Seen"}

    def __init__(self, parent, app, show_add_button=False, group_filter=None):
        super().__init__(parent)
        self.app = app
        self.db = app.db
        self.group_filter = group_filter

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 6))
        if show_add_button:
            ttk.Button(toolbar, text="+ Add Device", style="Accent.TButton",
                       command=self.app.open_add_device_dialog).pack(side="left")
        ttk.Button(toolbar, text="Scan Network", command=self._open_scan_dialog).pack(side="left", padx=8)

        self.tree = ttk.Treeview(self, columns=self.COLS, show="headings", selectmode="browse")
        for c in self.COLS:
            self.tree.heading(c, text=self.HEADS[c])
            self.tree.column(c, width=140, anchor="w")
        self.tree.column("name", width=160)
        self.tree.column("ip", width=120)
        self.tree.pack(fill="both", expand=True)

        self.tree.tag_configure("online", foreground=COLORS["online"])
        self.tree.tag_configure("offline", foreground=COLORS["offline"])
        self.tree.tag_configure("unknown", foreground=COLORS["unknown"])

        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Double-1>", lambda e: self._edit_selected())

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="Ping", command=self._ping_selected)
        self.menu.add_command(label="Continuous Ping", command=self._continuous_ping_selected)
        self.menu.add_command(label="Edit", command=self._edit_selected)
        self.menu.add_command(label="Copy IP", command=self._copy_ip_selected)
        self.menu.add_command(label="View History", command=self._view_history_selected)
        self.menu.add_command(label="Disable Monitoring", command=self._toggle_monitoring_selected)
        self.menu.add_separator()
        self.menu.add_command(label="Delete", command=self._delete_selected)

        self._row_to_device = {}

    def refresh(self):
        devices = self.db.get_devices()
        if self.group_filter and self.group_filter() not in (None, "All"):
            devices = [d for d in devices if d["group_name"] == self.group_filter()]

        self.tree.delete(*self.tree.get_children())
        self._row_to_device = {}
        for d in devices:
            status = d["status"] or "UNKNOWN"
            tag = status.lower() if status.lower() in ("online", "offline") else "unknown"
            ping_txt = f"{d['last_latency']:.0f}ms" if d["last_latency"] not in (None, "") and status == "ONLINE" else "--"
            loss_txt = f"{d['packet_loss']:.0f}%" if d["packet_loss"] is not None else "0%"
            last_seen = "--"
            if d["last_seen"]:
                try:
                    last_seen = datetime.fromisoformat(d["last_seen"]).strftime("%H:%M")
                except Exception:
                    last_seen = d["last_seen"]
            iid = str(d["id"])
            self.tree.insert("", "end", iid=iid, values=(
                d["name"], d["ip"], d["type"], status, ping_txt, loss_txt, last_seen
            ), tags=(tag,))
            self._row_to_device[iid] = d["id"]

    def _selected_device_id(self):
        sel = self.tree.selection()
        if sel:
            return self._row_to_device.get(sel[0])
        return None

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
            self.menu.tk_popup(event.x_root, event.y_root)

    def _ping_selected(self):
        device_id = self._selected_device_id()
        if not device_id:
            return
        device = self.db.get_device(device_id)

        def do_ping():
            ok, latency = ping_once(device["ip"], timeout_ms=1500)
            msg = f"{device['name']} ({device['ip']}): " + (
                f"reply in {latency:.0f}ms" if ok else "no reply")
            self.after(0, lambda: messagebox.showinfo("Ping Result", msg))

        threading.Thread(target=do_ping, daemon=True).start()

    def _continuous_ping_selected(self):
        device_id = self._selected_device_id()
        if device_id:
            self.app.open_continuous_ping(device_id)

    def _edit_selected(self):
        device_id = self._selected_device_id()
        if device_id:
            self.app.open_edit_device_dialog(device_id)

    def _copy_ip_selected(self):
        device_id = self._selected_device_id()
        if not device_id:
            return
        device = self.db.get_device(device_id)
        self.clipboard_clear()
        self.clipboard_append(device["ip"])

    def _view_history_selected(self):
        device_id = self._selected_device_id()
        if device_id:
            self.app.frames["History"].show_device(device_id)
            self.app.show_frame("History")

    def _toggle_monitoring_selected(self):
        device_id = self._selected_device_id()
        if not device_id:
            return
        device = self.db.get_device(device_id)
        new_val = 0 if device["monitoring_enabled"] else 1
        self.db.update_device(device_id, monitoring_enabled=new_val)
        self.refresh()

    def _delete_selected(self):
        device_id = self._selected_device_id()
        if not device_id:
            return
        device = self.db.get_device(device_id)
        if messagebox.askyesno("Delete Device", f"Delete '{device['name']}'? This removes its history too."):
            self.db.delete_device(device_id)
            self.app._on_device_changed()

    def _open_scan_dialog(self):
        ScanDialog(self, self.db, self.app)


# ==================================================================
# Devices frame (table + group filter)
# ==================================================================

class DevicesFrame(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.db = app.db

        header = ttk.Frame(self)
        header.pack(fill="x", padx=20, pady=(20, 10))
        tk.Label(header, text="Devices", bg=COLORS["panel"], fg="white",
                  font=("Segoe UI", 18, "bold")).pack(side="left")

        ttk.Label(header, text="Group:").pack(side="left", padx=(20, 6))
        self.group_var = tk.StringVar(value="All")
        combo = ttk.Combobox(header, textvariable=self.group_var, state="readonly",
                              values=["All"] + DEVICE_GROUPS, width=14)
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", lambda e: self.table.refresh())

        self.table = DeviceTable(self, app, show_add_button=True, group_filter=self.group_var.get)
        self.table.pack(fill="both", expand=True, padx=20, pady=(0, 20))

    def on_show(self):
        self.refresh()

    def refresh(self):
        self.table.refresh()


# ==================================================================
# Add / Edit device dialog
# ==================================================================

class DeviceDialog(tk.Toplevel):
    def __init__(self, parent, db, on_saved, device=None, prefill_ip=None):
        super().__init__(parent)
        self.db = db
        self.on_saved = on_saved
        self.device = device
        self.title("Edit Device" if device else "Add Device")
        self.configure(bg=COLORS["panel"])
        self.resizable(False, False)
        self.grab_set()

        pad = {"padx": 16, "pady": 6}

        self.name_var = tk.StringVar(value=device["name"] if device else "")
        self.ip_var = tk.StringVar(value=device["ip"] if device else (prefill_ip or ""))
        self.type_var = tk.StringVar(value=device["type"] if device else DEVICE_TYPES[0])
        self.group_var = tk.StringVar(value=device["group_name"] if device else DEVICE_GROUPS[0])
        self.desc_var = tk.StringVar(value=device["description"] if device else "")

        ttk.Label(self, text="Device Name").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(self, textvariable=self.name_var, width=32).grid(row=0, column=1, **pad)

        ttk.Label(self, text="IP Address").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(self, textvariable=self.ip_var, width=32).grid(row=1, column=1, **pad)

        ttk.Label(self, text="Device Type").grid(row=2, column=0, sticky="w", **pad)
        ttk.Combobox(self, textvariable=self.type_var, values=DEVICE_TYPES,
                     state="readonly", width=29).grid(row=2, column=1, **pad)

        ttk.Label(self, text="Group").grid(row=3, column=0, sticky="w", **pad)
        ttk.Combobox(self, textvariable=self.group_var, values=DEVICE_GROUPS,
                     state="readonly", width=29).grid(row=3, column=1, **pad)

        ttk.Label(self, text="Description").grid(row=4, column=0, sticky="w", **pad)
        ttk.Entry(self, textvariable=self.desc_var, width=32).grid(row=4, column=1, **pad)

        btn_row = ttk.Frame(self)
        btn_row.grid(row=5, column=0, columnspan=2, pady=14)
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Save", style="Accent.TButton", command=self._save).pack(side="left", padx=6)

    def _save(self):
        name = self.name_var.get().strip()
        ip = self.ip_var.get().strip()
        if not name or not ip:
            messagebox.showerror("Missing Information", "Device Name and IP Address are required.")
            return

        if self.device:
            self.db.update_device(
                self.device["id"],
                name=name, ip=ip, type=self.type_var.get(),
                group_name=self.group_var.get(), description=self.desc_var.get(),
            )
        else:
            self.db.add_device(name, ip, self.type_var.get(), self.group_var.get(),
                                self.desc_var.get())

        self.on_saved()
        self.destroy()


# ==================================================================
# Continuous ping window
# ==================================================================

class ContinuousPingWindow(tk.Toplevel):
    def __init__(self, app, db, device):
        super().__init__(app)
        self.app = app
        self.db = db
        self.device = device
        self.title(f"Continuous Ping - {device['name']} ({device['ip']})")
        self.geometry("560x420")
        self.configure(bg=COLORS["bg"])

        self._stop_event = threading.Event()
        self._thread = None

        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=8)
        self.start_btn = ttk.Button(top, text="Start", style="Accent.TButton", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(top, text="Stop", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=8)

        self.text = tk.Text(self, bg="#0f1115", fg="#22c55e", insertbackground="#22c55e",
                             font=("Consolas", 10), height=16)
        self.text.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.text.insert("end", f"Pinging {device['ip']}...\n\n")
        self.text.configure(state="disabled")

        stats = ttk.Frame(self)
        stats.pack(fill="x", padx=10, pady=(0, 10))
        self.stats_labels = {}
        for key, label in [("sent", "Sent"), ("received", "Received"), ("lost", "Lost"),
                            ("loss_pct", "Loss %"), ("avg_latency", "Avg Ping")]:
            box = tk.Frame(stats, bg=COLORS["panel_light"], padx=10, pady=6)
            box.pack(side="left", padx=4, fill="x", expand=True)
            tk.Label(box, text=label, bg=COLORS["panel_light"], fg=COLORS["muted"],
                      font=("Segoe UI", 8)).pack()
            val = tk.Label(box, text="0", bg=COLORS["panel_light"], fg="white",
                            font=("Segoe UI", 12, "bold"))
            val.pack()
            self.stats_labels[key] = val

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._start()

    def _start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        timeout_ms = int(self.db.get_setting("ping_timeout_ms", "1000"))
        self._thread = threading.Thread(
            target=self.app.monitor.start_continuous_ping,
            args=(self.device["id"], self.device["ip"], timeout_ms,
                  self._append_line, self._update_stats, self._stop_event.is_set),
            daemon=True,
        )
        self._thread.start()

    def _stop(self):
        self._stop_event.set()
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")

    def _append_line(self, line):
        def do():
            if not self.winfo_exists():
                return
            self.text.configure(state="normal")
            self.text.insert("end", line)
            self.text.see("end")
            self.text.configure(state="disabled")
        try:
            self.after(0, do)
        except Exception:
            pass

    def _update_stats(self, stats):
        def do():
            if not self.winfo_exists():
                return
            self.stats_labels["sent"].config(text=str(stats["sent"]))
            self.stats_labels["received"].config(text=str(stats["received"]))
            self.stats_labels["lost"].config(text=str(stats["lost"]))
            self.stats_labels["loss_pct"].config(text=f"{stats['loss_pct']}%")
            self.stats_labels["avg_latency"].config(text=f"{stats['avg_latency']}ms")
        try:
            self.after(0, do)
        except Exception:
            pass

    def _on_close(self):
        self._stop_event.set()
        self.destroy()


# ==================================================================
# Network scan dialog
# ==================================================================

class ScanDialog(tk.Toplevel):
    def __init__(self, parent, db, app):
        super().__init__(parent)
        self.db = db
        self.app = app
        self.title("Network Scan")
        self.geometry("480x420")
        self.configure(bg=COLORS["panel"])
        self.grab_set()

        top = ttk.Frame(self)
        top.pack(fill="x", padx=12, pady=10)
        ttk.Label(top, text="Subnet (CIDR):").pack(side="left")
        self.cidr_var = tk.StringVar(value=get_local_subnet_guess())
        ttk.Entry(top, textvariable=self.cidr_var, width=20).pack(side="left", padx=8)
        self.scan_btn = ttk.Button(top, text="Scan", style="Accent.TButton", command=self._scan)
        self.scan_btn.pack(side="left")

        self.progress_label = ttk.Label(self, text="Enter a subnet and click Scan.")
        self.progress_label.pack(anchor="w", padx=12)

        self.tree = ttk.Treeview(self, columns=("ip", "latency"), show="headings", selectmode="extended")
        self.tree.heading("ip", text="IP Address")
        self.tree.heading("latency", text="Latency")
        self.tree.pack(fill="both", expand=True, padx=12, pady=10)

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(btn_row, text="Add Selected as Devices", style="Accent.TButton",
                   command=self._add_selected).pack(side="left")
        ttk.Button(btn_row, text="Close", command=self.destroy).pack(side="right")

        self._stop_flag = False
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _scan(self):
        cidr = self.cidr_var.get().strip()
        self.tree.delete(*self.tree.get_children())
        self.scan_btn.config(state="disabled")
        self._stop_flag = False

        def progress(done, total):
            self.after(0, lambda: self.progress_label.config(text=f"Scanning... {done}/{total}"))

        def run():
            results = scan_subnet(cidr, timeout_ms=400, progress_cb=progress,
                                   stop_flag=lambda: self._stop_flag)
            self.after(0, lambda: self._show_results(results))

        threading.Thread(target=run, daemon=True).start()

    def _show_results(self, results):
        for r in results:
            self.tree.insert("", "end", values=(r["ip"], f"{r['latency_ms']:.0f}ms" if r["latency_ms"] else "--"))
        self.progress_label.config(text=f"Found {len(results)} responding device(s).")
        self.scan_btn.config(state="normal")

    def _add_selected(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Network Scan", "Select one or more discovered devices first.")
            return
        for iid in selected:
            ip = self.tree.item(iid, "values")[0]
            name = f"Device-{ip.split('.')[-1]}"
            self.db.add_device(name, ip, "Other", "Other", "Discovered via network scan")
        self.app._on_device_changed()
        messagebox.showinfo("Network Scan", f"Added {len(selected)} device(s) to monitoring.")

    def _on_close(self):
        self._stop_flag = True
        self.destroy()


# ==================================================================
# Network Map
# ==================================================================

class NetworkMapFrame(ttk.Frame):
    NODE_R = 26

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.db = app.db
        self._nodes = {}  # device_id -> (oval_id, text_id, label_id)

        header = ttk.Frame(self)
        header.pack(fill="x", padx=20, pady=(20, 10))
        tk.Label(header, text="Network Map", bg=COLORS["panel"], fg="white",
                  font=("Segoe UI", 18, "bold")).pack(side="left")
        ttk.Label(header, text="  Drag devices to rearrange.", style="Muted.TLabel").pack(side="left")

        self.canvas = tk.Canvas(self, bg="#12151b", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        self._built = False

    def on_show(self):
        self._rebuild()

    def refresh(self):
        if not self._built:
            self._rebuild()
        else:
            self._update_colors()

    def _rebuild(self):
        self.canvas.delete("all")
        self._nodes = {}
        devices = self.db.get_devices()
        cols = max(1, int(self.canvas.winfo_width() / 160) or 5)

        for idx, d in enumerate(devices):
            if d["map_x"] is not None and d["map_y"] is not None:
                x, y = d["map_x"], d["map_y"]
            else:
                x = 100 + (idx % 5) * 160
                y = 100 + (idx // 5) * 140
                self.db.update_device(d["id"], map_x=x, map_y=y)
            self._draw_node(d, x, y)
        self._built = True

    def _draw_node(self, device, x, y):
        color = COLORS["online"] if device["status"] == "ONLINE" else (
            COLORS["offline"] if device["status"] == "OFFLINE" else COLORS["unknown"])
        r = self.NODE_R
        oval = self.canvas.create_oval(x - r, y - r, x + r, y + r, fill=color, outline="white", width=2,
                                        tags=("node", f"dev{device['id']}"))
        icon_txt = self.canvas.create_text(x, y, text=device["type"][:2].upper(), fill="#0f1115",
                                            font=("Segoe UI", 10, "bold"), tags=("node", f"dev{device['id']}"))
        label = self.canvas.create_text(x, y + r + 14, text=f"{device['name']}\n{device['ip']}",
                                         fill=COLORS["text"], font=("Segoe UI", 8), justify="center",
                                         tags=(f"dev{device['id']}",))
        self._nodes[device["id"]] = (oval, icon_txt, label)

    def _update_colors(self):
        devices = {d["id"]: d for d in self.db.get_devices()}
        for device_id, (oval, icon_txt, label) in list(self._nodes.items()):
            d = devices.get(device_id)
            if not d:
                continue
            color = COLORS["online"] if d["status"] == "ONLINE" else (
                COLORS["offline"] if d["status"] == "OFFLINE" else COLORS["unknown"])
            self.canvas.itemconfig(oval, fill=color)

    def _find_device_id_at(self, x, y):
        item = self.canvas.find_closest(x, y)
        if not item:
            return None
        tags = self.canvas.gettags(item[0])
        for t in tags:
            if t.startswith("dev"):
                try:
                    return int(t[3:])
                except ValueError:
                    return None
        return None

    def _on_press(self, event):
        device_id = self._find_device_id_at(event.x, event.y)
        self._drag_data = {"device_id": device_id, "x": event.x, "y": event.y}

    def _on_drag(self, event):
        device_id = self._drag_data.get("device_id")
        if not device_id or device_id not in self._nodes:
            return
        dx = event.x - self._drag_data["x"]
        dy = event.y - self._drag_data["y"]
        oval, icon_txt, label = self._nodes[device_id]
        for item in (oval, icon_txt, label):
            self.canvas.move(item, dx, dy)
        self._drag_data["x"] = event.x
        self._drag_data["y"] = event.y

    def _on_release(self, event):
        device_id = self._drag_data.get("device_id")
        if device_id and device_id in self._nodes:
            oval = self._nodes[device_id][0]
            coords = self.canvas.coords(oval)
            cx = (coords[0] + coords[2]) / 2
            cy = (coords[1] + coords[3]) / 2
            self.db.update_device(device_id, map_x=int(cx), map_y=int(cy))
        self._drag_data = {"device_id": None, "x": 0, "y": 0}


# ==================================================================
# Alerts frame
# ==================================================================

class AlertsFrame(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.db = app.db

        header = ttk.Frame(self)
        header.pack(fill="x", padx=20, pady=(20, 10))
        tk.Label(header, text="Alerts", bg=COLORS["panel"], fg="white",
                  font=("Segoe UI", 18, "bold")).pack(side="left")

        self.tree = ttk.Treeview(self, columns=("time", "type", "message"), show="headings")
        self.tree.heading("time", text="Time")
        self.tree.heading("type", text="Type")
        self.tree.heading("message", text="Event")
        self.tree.column("time", width=140)
        self.tree.column("type", width=100)
        self.tree.column("message", width=600)
        self.tree.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        self.tree.tag_configure("OFFLINE", foreground=COLORS["offline"])
        self.tree.tag_configure("ONLINE", foreground=COLORS["online"])
        self.tree.tag_configure("WARNING", foreground=COLORS["warning"])

    def on_show(self):
        self.refresh()

    def refresh(self):
        events = self.db.get_events(limit=300)
        self.tree.delete(*self.tree.get_children())
        for e in events:
            try:
                t = datetime.fromisoformat(e["timestamp"]).strftime("%H:%M:%S")
            except Exception:
                t = e["timestamp"]
            self.tree.insert("", "end", values=(t, e["event_type"], e["message"]),
                              tags=(e["event_type"],))

    def add_live_event(self, ev):
        # Immediate insert at top for responsiveness; full refresh syncs later.
        now = datetime.now().strftime("%H:%M:%S")
        self.tree.insert("", 0, values=(now, ev["type"], ev["message"]), tags=(ev["type"],))


# ==================================================================
# History frame
# ==================================================================

class HistoryFrame(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.db = app.db
        self._device_id = None

        header = ttk.Frame(self)
        header.pack(fill="x", padx=20, pady=(20, 10))
        tk.Label(header, text="History", bg=COLORS["panel"], fg="white",
                  font=("Segoe UI", 18, "bold")).pack(side="left")

        ttk.Label(header, text="Device:").pack(side="left", padx=(20, 6))
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(header, textvariable=self.device_var, state="readonly", width=30)
        self.device_combo.pack(side="left")
        self.device_combo.bind("<<ComboboxSelected>>", lambda e: self._on_device_selected())

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        ttk.Label(left, text="Ping History").pack(anchor="w")
        self.ping_tree = ttk.Treeview(left, columns=("time", "result", "latency"), show="headings")
        self.ping_tree.heading("time", text="Time")
        self.ping_tree.heading("result", text="Result")
        self.ping_tree.heading("latency", text="Latency")
        self.ping_tree.pack(fill="both", expand=True)

        right = ttk.Frame(body)
        right.pack(side="right", fill="both", expand=True, padx=(10, 0))
        ttk.Label(right, text="Online / Offline Events").pack(anchor="w")
        self.event_tree = ttk.Treeview(right, columns=("time", "event"), show="headings")
        self.event_tree.heading("time", text="Time")
        self.event_tree.heading("event", text="Event")
        self.event_tree.pack(fill="both", expand=True)

        self._id_by_label = {}

    def on_show(self):
        self._reload_device_list()

    def refresh(self):
        if self._device_id:
            self._load_history(self._device_id)

    def show_device(self, device_id):
        self._reload_device_list()
        device = self.db.get_device(device_id)
        if device:
            label = f"{device['name']} ({device['ip']})"
            self.device_var.set(label)
            self._device_id = device_id
            self._load_history(device_id)

    def _reload_device_list(self):
        devices = self.db.get_devices()
        labels = [f"{d['name']} ({d['ip']})" for d in devices]
        self._id_by_label = {f"{d['name']} ({d['ip']})": d["id"] for d in devices}
        self.device_combo["values"] = labels
        if not self.device_var.get() and labels:
            self.device_var.set(labels[0])
            self._device_id = self._id_by_label[labels[0]]
            self._load_history(self._device_id)

    def _on_device_selected(self):
        label = self.device_var.get()
        device_id = self._id_by_label.get(label)
        if device_id:
            self._device_id = device_id
            self._load_history(device_id)

    def _load_history(self, device_id):
        self.ping_tree.delete(*self.ping_tree.get_children())
        for row in self.db.get_ping_history(device_id, limit=200):
            try:
                t = datetime.fromisoformat(row["timestamp"]).strftime("%H:%M:%S")
            except Exception:
                t = row["timestamp"]
            result = "Success" if row["success"] else "Timeout"
            latency = f"{row['latency_ms']:.0f}ms" if row["latency_ms"] is not None else "--"
            self.ping_tree.insert("", "end", values=(t, result, latency))

        self.event_tree.delete(*self.event_tree.get_children())
        for e in self.db.get_events_for_device(device_id, limit=100):
            try:
                t = datetime.fromisoformat(e["timestamp"]).strftime("%H:%M:%S")
            except Exception:
                t = e["timestamp"]
            self.event_tree.insert("", "end", values=(t, e["message"]))


# ==================================================================
# Settings frame
# ==================================================================

class SettingsFrame(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.db = app.db

        header = ttk.Frame(self)
        header.pack(fill="x", padx=20, pady=(20, 10))
        tk.Label(header, text="Settings", bg=COLORS["panel"], fg="white",
                  font=("Segoe UI", 18, "bold")).pack(side="left")

        form = ttk.Frame(self)
        form.pack(anchor="w", padx=20, pady=10)

        self.interval_var = tk.StringVar()
        self.timeout_var = tk.StringVar()
        self.threshold_var = tk.StringVar()
        self.theme_var = tk.StringVar()
        self.notif_var = tk.BooleanVar()

        rows = [
            ("Ping interval (seconds)", self.interval_var, "entry"),
            ("Ping timeout (ms)", self.timeout_var, "entry"),
            ("Failed checks before OFFLINE", self.threshold_var, "entry"),
            ("Theme", self.theme_var, "theme"),
            ("Enable alert notifications", self.notif_var, "check"),
        ]
        for i, (label, var, kind) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=i, column=0, sticky="w", padx=(0, 20), pady=8)
            if kind == "entry":
                ttk.Entry(form, textvariable=var, width=12).grid(row=i, column=1, sticky="w")
            elif kind == "theme":
                ttk.Combobox(form, textvariable=var, values=["dark", "light"],
                             state="readonly", width=10).grid(row=i, column=1, sticky="w")
            elif kind == "check":
                ttk.Checkbutton(form, variable=var).grid(row=i, column=1, sticky="w")

        ttk.Button(self, text="Save Settings", style="Accent.TButton",
                   command=self._save).pack(anchor="w", padx=20, pady=10)

        self.status_label = ttk.Label(self, text="", style="Muted.TLabel")
        self.status_label.pack(anchor="w", padx=20)

    def on_show(self):
        settings = self.db.all_settings()
        self.interval_var.set(settings.get("ping_interval_seconds", "5"))
        self.timeout_var.set(settings.get("ping_timeout_ms", "1000"))
        self.threshold_var.set(settings.get("failed_checks_before_offline", "3"))
        self.theme_var.set(settings.get("theme", "dark"))
        self.notif_var.set(settings.get("notifications_enabled", "1") == "1")

    def refresh(self):
        pass

    def _save(self):
        try:
            interval = max(1, int(self.interval_var.get()))
            timeout = max(100, int(self.timeout_var.get()))
            threshold = max(1, int(self.threshold_var.get()))
        except ValueError:
            messagebox.showerror("Invalid Settings", "Interval, timeout, and threshold must be numbers.")
            return

        self.db.set_setting("ping_interval_seconds", interval)
        self.db.set_setting("ping_timeout_ms", timeout)
        self.db.set_setting("failed_checks_before_offline", threshold)
        self.db.set_setting("theme", self.theme_var.get())
        self.db.set_setting("notifications_enabled", "1" if self.notif_var.get() else "0")

        self.status_label.config(text="Settings saved.")
        self.after(2000, lambda: self.status_label.config(text=""))
