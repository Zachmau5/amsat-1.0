#!/usr/bin/env python3
"""
GS-232B Calibration Wizard for Antenna Pointing Bring-up.

Purpose
-------
Provide a small Tk-based wizard to:
  1) Drive the array to known reference headings (North/South) to verify
     physical vs. software north alignment.
  2) Optionally park (stage) the array at a preset azimuth before exit.
  3) Hand control back to the main tracking application.

Role in System
--------------
- Can be launched standalone for bench testing, or invoked from the main GUI.
- Uses a minimal SerialManager wrapper that supports:
    - Real GS-232B hardware on common ports.
    - A simulation mode for environments without hardware.
- Issues only simple commands (W, S, C2); no persistent controller offsets.

High-level Flow (Pseudocode)
----------------------------
  1. SerialManager:
       - On init: optionally open one of several candidate ports; or enter
         simulate mode.
       - write_cmd(): send commands with retries; optionally read a reply.
       - send_move(): clamp/format az/el into Waaa eee; optionally issue C2.
       - stop() / c2(): convenience helpers.
  2. WizardFrame (Tk.Frame):
       - Splash page: safety blurb, Start / Cancel.
       - Step 1 (North): issue W000 000; operator visually confirms north.
       - Step 2 (South): issue W180 000; operator visually confirms south.
       - Stage page: choose preset azimuth (every 15°) and move there.
       - Complete page: Exit / Restart / Cancel options.
       - Periodically poll C2 and display the latest az/el.
  3. run_wizard(root, ser_mgr):
       - Replace existing root contents with WizardFrame.
       - Wait until on_complete() sets a flag.
       - Return a boolean success indicator to the caller.
"""

import time
import threading
import tkinter as tk
import re
from tkinter import ttk
import tkinter.font as tkfont

# PySerial is optional so the UI can be tested without hardware.
try:
    import serial
    from serial import Serial, SerialException
except Exception:  # allow import in environments without pyserial
    serial = None
    Serial = None

    class SerialException(Exception):
        ...


# =========================
# Modern-ish font selection
# =========================
def _pick_ui_font():
    """Select a clean UI font if available; otherwise use TkDefaultFont."""
    try:
        fams = set(tkfont.families())
        for f in ("Segoe UI", "Noto Sans", "DejaVu Sans", "Cantarell", "Roboto", "Arial"):
            if f in fams:
                return f
    except Exception:
        pass
    return "TkDefaultFont"


# =========================
# C2 parser (shared helper)
# =========================
_C2_RE = re.compile(
    r"AZ\s*[:=]\s*([+\-]?\d{1,4})\D+EL\s*[:=]\s*([+\-]?\d{1,3})",
    re.IGNORECASE,
)


def parse_c2_az_el(reply: str):
    """
    Parse GS-232B C2 reply into (az, el) integer degrees.
    Returns (None, None) on failure.
    """
    if not reply:
        return (None, None)
    m = _C2_RE.search(reply)
    if not m:
        return (None, None)
    try:
        az = int(m.group(1))
        el = int(m.group(2))
        az = max(0, min(450, az))
        el = max(0, min(180, el))
        return (az, el)
    except Exception:
        return (None, None)


# ==========================================
# Minimal Serial Manager (with SIM mode)
# ==========================================
class SerialManager:
    """
    Barebones serial manager for GS-232B:
      - Attempts to open one of several candidate ports (unless simulate=True).
      - write_cmd("R") -> sends "R\\r".
      - send_move(az, el) -> sends Wxxx yyy.
      - c2() -> sends C2 and returns one line (or simulated line).

    Simulation mode:
      - If simulate=True, no ports are opened and commands are tracked
        internally.
      - If simulate=False but no ports open, the manager falls back to
        simulate=True automatically.
    """

    def __init__(
        self,
        candidates=("/dev/ttyUSB0", "/dev/ttyUSB1", "COM3", "COM4"),
        baud=9600,
        timeout=1.0,
        simulate: bool = False,
    ):
        self.candidates = list(candidates)
        self.baud = baud
        self.timeout = timeout
        self.ser = None
        self.last_open_port = None

        # Simulated state
        self.simulate = bool(simulate)
        self._sim_az = 0
        self._sim_el = 0
        self._sim_last_cmd = ""

        # If not explicitly simulating, try to open hardware
        if not self.simulate:
            opened = self._open_any()
            if not opened:
                print("[SER] No GS-232B ports found; entering SIMULATE mode.")
                self.simulate = True
        else:
            print("[SER] SIMULATE mode forced; hardware ports will not be opened.")

    # ---- Hardware open/close helpers ----
    def _open_any(self):
        """Try last good port first, then iterate over the rest."""
        if Serial is None:
            return False

        ports_to_try = []
        if self.last_open_port:
            ports_to_try.append(self.last_open_port)
        ports_to_try.extend([p for p in self.candidates if p != self.last_open_port])

        for p in ports_to_try:
            try:
                self.ser = Serial(
                    port=p,
                    baudrate=self.baud,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=self.timeout,
                    xonxoff=False,
                    rtscts=False,
                    dsrdtr=False,
                    write_timeout=1.0,
                )
                self.last_open_port = p
                try:
                    self.ser.reset_input_buffer()
                    self.ser.reset_output_buffer()
                except Exception:
                    pass
                print(f"[SER] Opened {p} @ {self.baud} 8N1")
                return True
            except Exception as e:
                print(f"[SER] Open {p} failed: {e}")
                self.ser = None
        return False

    def ensure_open(self):
        """If the port dropped, try to reopen. In SIM mode we report True."""
        if self.simulate:
            return True
        if self.ser and self.ser.is_open:
            return True
        return self._open_any()

    def close(self):
        """Best-effort close on shutdown (no-op in SIM mode)."""
        if self.simulate:
            return
        try:
            if self.ser:
                self.ser.close()
                print("[SER] Closed")
        except Exception:
            pass
        self.ser = None

    # ---- Raw hardware I/O ----
    def _write_raw(self, bcmd: bytes):
        if self.simulate:
            # Hardware write suppressed in SIM
            return
        if not self.ensure_open():
            raise SerialException("Port not open")
        self.ser.write(bcmd)
        self.ser.flush()

    def _readline(self) -> str:
        if self.simulate:
            # In SIM, readline is unused; C2 is handled in _sim_write_cmd
            return ""
        if not self.ensure_open():
            return ""
        try:
            # Read until CR (0x0D); GS-232B commonly uses CR-only line endings
            b = self.ser.read_until(b"\r")
            if not b:
                return ""
            # Strip the trailing CR and any spurious whitespace
            return b.rstrip(b"\r").decode("ascii", errors="ignore").strip()
        except Exception:
            return ""

    # ---- SIM-mode behavior ----
    def _sim_write_cmd(self, cmd_str: str, expect_reply: bool) -> str:
        """
        Handle commands when simulate=True.
        Tracks az/el from W commands, returns a synthetic C2, etc.
        """
        self._sim_last_cmd = cmd_str.strip()
        line = self._sim_last_cmd.upper()

        # Absolute move: Wxxx yyy
        if line.startswith("W"):
            try:
                # accept "Wxxx yyy" or "Wxxx,yyy"
                body = line[1:].strip()
                parts = re.split(r"[,\s]+", body)
                if len(parts) >= 2:
                    az = int(parts[0])
                    el = int(parts[1])
                    self._sim_az = max(0, min(450, az))
                    self._sim_el = max(0, min(180, el))
            except Exception:
                pass
            return ""  # W has no reply by default

        # Position echo
        if line.startswith("C2"):
            # Typical GS-232B-ish style reply
            return f"AZ={self._sim_az:03d} EL={self._sim_el:03d}"

        # Stop, etc. – no-op but acknowledged
        if line.startswith("S"):
            return ""

        # Anything else: just pretend it was okay
        return ""

    # ---- High-level helpers ----
    def write_cmd(self, cmd_str: str, expect_reply=False, retries=1) -> str:
        """
        Send "cmd\\r" to the controller or SIM engine.
        Optionally read a one-line reply.
        """
        cmd_str = cmd_str.rstrip()

        # SIM path: bypass serial entirely
        if self.simulate:
            return self._sim_write_cmd(cmd_str, expect_reply)

        payload = (cmd_str + "\r").encode("ascii", errors="ignore")

        attempt = 0
        while attempt <= retries:
            try:
                self._write_raw(payload)
                if expect_reply:
                    return self._readline()
                return ""
            except SerialException:
                self.close()
                time.sleep(0.25)
                self.ensure_open()
                attempt += 1
        return ""

    def send_move(self, az_deg: int, el_deg: int, echo_c2=False):
        """
        Convenience wrapper for W commands with clamping and fixed formatting.
        echo_c2=True triggers a C2 readback so I can show the result in the UI.
        """
        az = max(0, min(450, int(round(az_deg))))
        el = max(0, min(180, int(round(el_deg))))
        cmd = f"W{az:03d} {el:03d}"
        reply = self.write_cmd(cmd, expect_reply=False)
        if echo_c2:
            reply = self.write_cmd("C2", expect_reply=True)
        return cmd, reply

    def stop(self):
        """S = All stop (both axes)."""
        return self.write_cmd("S", expect_reply=False)

    def c2(self):
        """C2 = Position echo (az, el)."""
        return self.write_cmd("C2", expect_reply=True)


# ==========================
# Wizard UI
# ==========================
class WizardFrame(tk.Frame):
    """
    Pages:
      - Splash
      - North (W000 000)
      - South (W180 000)
      - Stage  (preset az every 15°)
      - Complete

    `on_complete(True)` returns control to the caller with a success flag.
    """

    def __init__(self, master, ser_mgr: SerialManager, on_complete, *args, **kwargs):
        super().__init__(master, *args, **kwargs)
        self.configure(bg="white")
        self.ser_mgr = ser_mgr
        self.on_complete = on_complete  # callback(bool)

        self.page = None
        self.status_var = tk.StringVar(value="")

        self._c2_poll_id = None
        self._c2_target_var = None  # StringVar to update with latest C2 line
        self._stage_az_var = None

        # Container for the current page + a bottom status line
        self.container = tk.Frame(self, bg="white")
        self.container.pack(fill="both", expand=True, padx=20, pady=20)

        # Fonts + ttk styles (after a Tk root exists)
        try:
            _UI_FONT = _pick_ui_font()
            self.TITLE_FONT = (_UI_FONT, 14, "bold")
            self.BODY_FONT = (_UI_FONT, 11)
            self.style = ttk.Style(self)
            self.style.configure(
                "Heading.TLabel",
                font=self.TITLE_FONT,
                background="white",
                foreground="black",
            )
            self.style.configure(
                "Body.TLabel",
                font=self.BODY_FONT,
                background="white",
                foreground="black",
            )
        except Exception:
            # If fonts/styles fail, fallback to Tk defaults.
            pass

        status_bar = tk.Frame(self, bg="white")
        status_bar.pack(fill="x", side="bottom")
        tk.Label(status_bar, textvariable=self.status_var, bg="white", fg="black").pack(
            anchor="w", padx=8, pady=6
        )

        # Start at the splash screen
        self.goto_splash()

    # ---------- Navigation helpers ----------
    def _clear_page(self):
        """Wipe the current page and reset status text."""
        for w in self.container.winfo_children():
            w.destroy()
        self.page = None
        self._c2_target_var = None
        self.status_var.set("")

    def _serial_status(self, extra=""):
        """
        Update the status banner with port state, mode (HW/SIM), and last action.
        """
        mode = "SIM" if getattr(self.ser_mgr, "simulate", False) else "HW"
        ok = self.ser_mgr.ensure_open()
        port = getattr(self.ser_mgr, "last_open_port", None) if not self.ser_mgr.simulate else "N/A"
        s = f"Mode: {mode} | Serial: {'OK' if ok else 'NOT CONNECTED'}"
        if port and not self.ser_mgr.simulate:
            s += f" | Port: {port}"
        if extra:
            s += f" | {extra}"
        self.status_var.set(s)

    def _c2_echo_label(self, parent):
        row = tk.Frame(parent, bg="white")
        row.pack(fill="x", pady=(12, 0))
        ttk.Label(row, text="C2 Echo:", style="Body.TLabel").pack(side="left")
        echo_var = tk.StringVar(value="(none)")
        ttk.Label(row, textvariable=echo_var, style="Body.TLabel").pack(
            side="left", padx=8
        )
        # Remember this var so the poller can update it globally
        self._c2_target_var = echo_var
        # Ensure the poller is running (1 Hz)
        self._start_c2_poll(1000)
        return echo_var

    def _start_c2_poll(self, period_ms: int = 1000):
        """Start/continue a 1 Hz C2 poll that updates self._c2_target_var if set."""
        if self._c2_poll_id is not None:
            return

        def _tick():
            self._c2_poll_id = self.after(period_ms, _tick)
            if self._c2_target_var is None:
                return
            try:
                reply = self.ser_mgr.c2()
                if reply:
                    self._c2_target_var.set(reply)
            except Exception:
                pass

        self._c2_poll_id = self.after(period_ms, _tick)

    def _stop_c2_poll(self):
        """Stop the periodic C2 poll (called on teardown or if you ever need to)."""
        if self._c2_poll_id is not None:
            try:
                self.after_cancel(self._c2_poll_id)
            except Exception:
                pass
            self._c2_poll_id = None

    # ---------- Pages ----------
    def goto_splash(self):
        """Intro with safety blurb + Start/Cancel."""
        self._clear_page()
        self.page = "splash"
        f = tk.Frame(self.container, bg="white")
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="Calibration Wizard", style="Heading.TLabel").pack(
            pady=(0, 10)
        )
        ttk.Label(
            f,
            text=(
                "This sequence will move the array.\n"
                "Make sure the area is clear before starting."
            ),
            style="Body.TLabel",
            justify="left",
        ).pack(pady=(0, 20))

        tk.Button(f, text="Start", width=16, command=self.goto_north).pack(pady=6)
        tk.Button(
            f, text="Cancel", width=16, command=lambda: self._finish(False)
        ).pack(pady=(6, 0))

        self._serial_status()

    def goto_north(self):
        """
        Step 1: Command W000 000 (AZ=0°, EL=0°).
        Visual check: array should physically face True North.
        """
        self._clear_page()
        self.page = "north"
        f = tk.Frame(self.container, bg="white")
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="Step 1: Point to TRUE NORTH", style="Heading.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            f,
            text="Sends W000 000 (Az=0°, El=0°). Verify the array faces True North.",
            style="Body.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(4, 10))

        buttons = tk.Frame(f, bg="white")
        buttons.pack(anchor="w", pady=8)
        echo_var = self._c2_echo_label(f)

        tk.Button(
            buttons,
            text="Move (W000 000)",
            width=18,
            command=lambda: self._do_move(0, 0, echo_var),
        ).grid(row=0, column=0, padx=4, pady=4)
        tk.Button(
            buttons, text="Next ▶", width=12, command=self.goto_south
        ).grid(row=0, column=1, padx=4, pady=4)
        tk.Button(
            buttons,
            text="Stop + Restart",
            width=16,
            command=self._stop_and_restart,
        ).grid(row=0, column=2, padx=4, pady=4)

        self._serial_status()

    def goto_south(self):
        """
        Step 2: Command W180 000 (AZ=180°, EL=0°).
        Visual check: array should point Due South.
        """
        self._clear_page()
        self.page = "south"
        f = tk.Frame(self.container, bg="white")
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="Step 2: Point to DUE SOUTH", style="Heading.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            f,
            text="Sends W180 000 (Az=180°, El=0°). Verify the array faces Due South.",
            style="Body.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(4, 10))

        buttons = tk.Frame(f, bg="white")
        buttons.pack(anchor="w", pady=8)
        echo_var = self._c2_echo_label(f)

        tk.Button(
            buttons,
            text="Move (W180 000)",
            width=18,
            command=lambda: self._do_move(180, 0, echo_var),
        ).grid(row=0, column=0, padx=4, pady=4)
        tk.Button(
            buttons, text="Next ▶", width=12, command=self.goto_stage
        ).grid(row=0, column=1, padx=4, pady=4)
        tk.Button(
            buttons,
            text="Stop + Restart",
            width=16,
            command=self._stop_and_restart,
        ).grid(row=0, column=2, padx=4, pady=4)

        self._serial_status()

    def goto_stage(self):
        """
        Optional staging step: park the array at a preset azimuth (every 15°).
        EL stays at 0°. Nice to leave it somewhere expected before handing
        control back to the main app.
        """
        self._clear_page()
        self.page = "stage"

        f = tk.Frame(self.container, bg="white")
        f.pack(fill="both", expand=True)

        ttk.Label(
            f,
            text="Final Staging: Choose an azimuth to park the array",
            style="Heading.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            f,
            text=(
                "Select a preset angle (every 15°). "
                "Click Move to command Wxxx 000.\n"
                "Finish will exit the wizard and continue to Satellite Selection."
            ),
            style="Body.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(4, 10))

        # 0..345 by 15°, laid out 6 columns wide
        grid = tk.Frame(f, bg="white")
        grid.pack(anchor="w", pady=(0, 10))
        self._stage_az_var = tk.IntVar(value=0)

        angles = list(range(0, 360, 15))
        cols = 6
        for idx, az in enumerate(angles):
            r = idx // cols
            c = idx % cols
            tk.Radiobutton(
                grid,
                text=f"{az:03d}°",
                value=az,
                variable=self._stage_az_var,
                bg="white",
                fg="black",
                anchor="w",
                padx=6,
            ).grid(row=r, column=c, sticky="w", padx=4, pady=4)

        btns = tk.Frame(f, bg="white")
        btns.pack(anchor="w", pady=8)

        echo_var = self._c2_echo_label(f)

        def _do_stage_move():
            az = int(self._stage_az_var.get())
            try:
                cmd, reply = self.ser_mgr.send_move(az, 0, echo_c2=True)
                echo_var.set(reply if reply else "(no reply)")
                self._serial_status(extra=f"Staged: {cmd}")
            except Exception as e:
                echo_var.set("(error)")
                self._serial_status(extra=f"Stage move failed: {e}")

        tk.Button(btns, text="Move", width=12, command=_do_stage_move).grid(
            row=0, column=0, padx=4, pady=4
        )
        tk.Button(btns, text="Back", width=10, command=self.goto_south).grid(
            row=0, column=1, padx=4, pady=4
        )
        tk.Button(
            btns,
            text="Stop + Restart",
            width=16,
            command=self._stop_and_restart,
        ).grid(row=0, column=2, padx=4, pady=4)
        tk.Button(
            btns, text="Finish ▶", width=12, command=self.goto_complete
        ).grid(row=0, column=3, padx=4, pady=4)

        self._serial_status()

    def goto_complete(self):
        """End page. Continue returns True to the caller to proceed to selection."""
        self._clear_page()
        self.page = "complete"
        f = tk.Frame(self.container, bg="white")
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="Calibration Complete", style="Heading.TLabel").pack(
            pady=(0, 6), anchor="w"
        )
        ttk.Label(
            f,
            text="Proceed to Satellite Selection.",
            style="Body.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(0, 12))

        btns = tk.Frame(f, bg="white")
        btns.pack(anchor="w", pady=10)
        tk.Button(
            btns, text="Exit", width=16, command=lambda: self._finish(True)
        ).grid(row=0, column=0, padx=4, pady=4)
        tk.Button(
            btns, text="Restart Wizard", width=16, command=self.goto_splash
        ).grid(row=0, column=1, padx=4, pady=4)
        tk.Button(
            btns, text="Cancel", width=12, command=lambda: self._finish(False)
        ).grid(row=0, column=2, padx=4, pady=4)

        self._serial_status()

    # ---------- Actions ----------
    def _do_move(self, az_deg, el_deg, echo_var):
        """
        One-shot W move with C2 echo pushed to the UI.
        Kept centralized so button handlers stay tiny.
        """
        try:
            cmd, reply = self.ser_mgr.send_move(az_deg, el_deg, echo_c2=True)
            echo_var.set(reply if reply else "(no reply)")
            self._serial_status(extra=f"Last: {cmd}")
        except Exception as e:
            echo_var.set("(error)")
            self._serial_status(extra=f"Move failed: {e}")

    def _stop_and_restart(self):
        """
        Emergency stop path:
        - Send 'S' (all stop)
        - Restart at Splash.
        """
        try:
            self.ser_mgr.stop()  # 'S' All Stop
        except Exception as e:
            self._serial_status(extra=f"Stop error: {e}")
        self.goto_splash()

    def _finish(self, ok: bool):
        """
        Exit the wizard:
        - Stop C2 poller
        - Destroy my frame and call on_complete(ok) for the caller to decide next steps.
        """
        self._stop_c2_poll()
        self.destroy()
        try:
            self.on_complete(bool(ok))
        except Exception:
            pass


# ==========================
# Public entry point
# ==========================
def run_wizard(root: tk.Tk, ser_mgr: SerialManager) -> bool:
    """
    Run the wizard inside an existing Tk root and SerialManager.
    Blocks until the user completes or cancels, then returns True/False.
    """
    result_holder = {"ok": False}
    done = threading.Event()

    def on_complete(ok: bool):
        result_holder["ok"] = ok
        done.set()

    # Replace whatever is in the root with this wizard
    for w in root.winfo_children():
        w.destroy()
    root.title("GS-232B Calibration Wizard")
    root.configure(bg="white")

    wf = WizardFrame(root, ser_mgr, on_complete)
    wf.pack(fill="both", expand=True)

    # Mini “modal” loop – periodically check for completion
    def check_done():
        if done.is_set():
            root.quit()  # exit the nested loop
        else:
            root.after(100, check_done)

    check_done()
    root.mainloop()
    return result_holder["ok"]


# ==========================
# Standalone for testing
# ==========================
if __name__ == "__main__":
    # Launch the wizard by itself so I can test UI + SIM mode without main.
    root = tk.Tk()
    # For bench testing, you can force simulate=True here if you want:
    # sm = SerialManager(simulate=True)
    sm = SerialManager()

    ok = run_wizard(root, sm)
    print(f"[Wizard] Completed = {ok}")

    # Close serial on exit (best effort)
    try:
        sm.close()
    except Exception:
        pass
