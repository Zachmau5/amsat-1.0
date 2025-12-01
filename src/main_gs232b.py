#!/usr/bin/env python3
"""
Main GS-232B tracking GUI entry point.

Purpose
-------
Provide the end-to-end user interface for:
  - Selecting TLE groups and individual satellites.
  - Prefetching TLE data and computing short-term visibility metadata.
  - Launching a Matplotlib-based tracking window with:
      * Global map view.
      * Near-sided (QTH-centered) map.
      * Azimuth and elevation gauges.
      * A serial console area for GS-232B status.
  - Driving the GS-232B rotator with anti-jitter and slew-rate limiting.

Role in System
--------------
- Consumes:
    - TLE groups from fetch_tle.fetch_group().
    - Pass visibility summaries from pass_visibility.compute_pass_visibility_for_file().
    - Drawing helpers from gui.maps and gui.gauges.
    - SerialManager from gs232.serial_manager for GS-232B control.
- Produces:
    - Real-time motion commands to the rotator.
    - On-screen plots and gauges for an operator.

High-level Flow (Pseudocode)
----------------------------
  1. On module invocation as __main__:
       a. Define ground-station latitude/longitude.
       b. Prefetch TLE groups and build a tle_cache.
       c. For each group, compute pass visibility and store vis_cache.
       d. Create the Tk root window and call SetupWindow(...).
  2. SetupWindow(root, my_lat, my_lon, tle_cache, vis_cache):
       a. Build the "Satellite Selector" UI:
            - Group dropdown (Amateur / NOAA / GOES / Weather / CUBESAT / SATNOGS).
            - Scrollable list of satellites with checkboxes.
            - Optional visibility annotation (next pass peak time and elevation).
       b. Wire the "Run Prediction" button to runPredictionTool(...).
  3. runPredictionTool(...):
       a. Collect selected satellites; require at least one.
       b. Load the corresponding TLE file and construct a lookup table of TLE lines.
       c. Open a SerialManager for the GS-232B rotator.
       d. Build a 2x2 Matplotlib figure:
            - Global map, near-sided map, azimuth gauge, elevation gauge, serial console.
            - Add a STOP + CLOSE button that sends S and closes the figure.
       e. In the animation callback:
            - Resolve the first selected satellite to a Skyfield EarthSatellite.
            - Propagate to now, compute topocentric az/el from the ground station.
            - Apply unwrapping, zenith-freeze, slew limit, quantization, and deadband.
            - Send W commands to the GS-232B (with optional C2 echo).
            - Update gauges, maps, and serial console text.
  4. Tk mainloop runs until the operator exits the program.
"""

import sys
import math
import time
import queue
import threading

import tkinter as tk
from fetch_tle import fetch_group
# from visibility import has_visible_pass_next_hour  # from the separate module
from pass_visibility import compute_pass_visibility_for_file
from zoneinfo import ZoneInfo
from gs232.serial_manager import SerialManager
from gui.gauges import az_to_compass, init_az_compass, init_el_gauge
from gui.maps import create_maps, draw_nearsided_background

LOCAL_TZ = ZoneInfo("America/Denver")

# Keep references to animations so they are not garbage-collected.
_ANIMATIONS = []

# The following commented block is an operator reference for recovering
# the GS-232B serial port with screen/minicom on Linux.
#
# 1. Kill anything else holding the port
#    lsof /dev/ttyUSB0
#    fuser -v /dev/ttyUSB0
#
#    If a process (Python, miniterm, etc.) is shown, stop it:
#
#    kill <PID>
#
# 2. Open screen on the rotator’s port
#    screen /dev/ttyUSB0 9600
#
#    The GS-232 expects 9600 baud, 8N1, no handshake by default.
#
# 3. Basic “flush” commands
#
#    Inside screen:
#
#      S      → Stop command, halts rotation and clears input buffer.
#      C2     → Controller replies with current AZ/EL (e.g., +0180+0090).
#      H / H2 → List command help screens, confirming the controller is alive.
#
# 4. Exit screen cleanly
#
#    Ctrl-A, then K, then Y.

# def runPredictionTool(checkbox_dict, tle_dict, my_lat, my_lon):
def runPredictionTool(checkbox_dict, tle_dict, my_lat, my_lon, tle_path):
    """
    Launch the tracking figure and start live GS-232B drive for the
    first selected satellite.

    Layout:
        TL: Global map
        TR: Near-sided (QTH-centered) map
        BL: Az/Elev gauges
        BR: GS-232B console (Waaa eee + C2 echo)
    """
    # 1) Gather satellites
    selected = {name: tle_dict[name] for name, var in checkbox_dict.items() if var.get() == 1}
    if not selected:
        from tkinter import messagebox
        messagebox.showwarning("No Satellites Selected", "Please check at least one satellite before running.")
        return

    # ────────────────────────────────────────────────────────────────────
    # TLE lookup: read raw TLE lines and build a name/NORAD index.
    def _norm_name(s: str) -> str:
        return "".join((s or "").upper().split())

    def load_tle_lookup(tle_path="amateur.tle"):
        lk = {}
        try:
            with open(tle_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = [ln.strip() for ln in f if ln.strip()]
            i = 0
            while i <= len(lines) - 3:
                name = lines[i]
                l1 = lines[i + 1]
                l2 = lines[i + 2]
                if l1.startswith("1 ") and l2.startswith("2 "):
                    lk[_norm_name(name)] = (l1, l2)
                    # Also index by NORAD if needed.
                    norad = l1[2:7].strip()
                    lk[norad] = (l1, l2)
                    i += 3
                else:
                    i += 1
        except Exception as e:
            print(f"[WARN] Could not load TLE file: {e}")
        return lk

    # tle_lookup = load_tle_lookup("amateur.tle")
    tle_lookup = load_tle_lookup(tle_path)

    # Serial manager for GS-232B
    ser_mgr = SerialManager(
        candidates=["/dev/ttyUSB0", "/dev/ttyUSB1", "COM3", "COM4"],
        baud=9600,
        timeout=1.0,
    )

    # ────────────────────────────────────────────────────────────────────
    # Heavy imports now (to avoid slowing initial Tk window startup).
    import numpy as np  # noqa: F401  (import kept in case of future use)
    import matplotlib.pyplot as plt
    import matplotlib.patheffects as pe
    from matplotlib import animation
    from datetime import datetime
    from collections import deque
    from skyfield.api import load, wgs84, EarthSatellite
    from matplotlib.widgets import Button

    ts = load.timescale()
    _sat_cache = {}

    # N2YO-style debug print (disabled by default in production use).
    def n2yo_style_debug(name, sat, t, note=""):
        try:
            geoc = sat.at(t)
            sp = geoc.subpoint()
            lat = sp.latitude.degrees
            lon = sp.longitude.degrees
            alt_km = sp.elevation.km
            vx, vy, vz = geoc.velocity.km_per_s
            speed = (vx * vx + vy * vy + vz * vz) ** 0.5
            utc_now = datetime.utcnow()
            # Uncomment for detailed diagnostics if desired.
            # print("\n--- N2YO Comparison Style ---")
            # print(f"Satellite:     {name}")
            # print(f"UTC Time:      {utc_now.strftime('%H:%M:%S')}")
            # print(f"LATITUDE:      {lat:.2f}°")
            # print(f"LONGITUDE:     {lon:.2f}°")
            # print(f"ALTITUDE [km]: {alt_km:.2f}")
            # print(f"SPEED [km/s]:  {speed:.2f}")
            # if note:
            #     print(f"NOTE:          {note}")
            # print("-----------------------------\n")
        except Exception as e:
            print(f"[DEBUG] N2YO-style debug failed: {e}")

    # ────────────────────────────────────────────────────────────────────
    # Figure layout
    fig = plt.figure(figsize=(14, 7))
    gs = fig.add_gridspec(2, 2, hspace=0.25, wspace=0.25)
    ax1 = fig.add_subplot(gs[0, 0])   # Global
    ax2 = fig.add_subplot(gs[0, 1])   # Near-sided
    subgs = gs[1, 0].subgridspec(2, 1, hspace=0.35)
    ax_az = fig.add_subplot(subgs[0, 0], projection="polar")
    ax_el = fig.add_subplot(subgs[1, 0], projection="polar")

    # Serial console (bottom-right)
    ax4 = fig.add_subplot(gs[1, 1])
    ax4.set_facecolor("black")
    ax4.axis("off")
    ax4.set_title("Yaesu GS-232B Serial Console", color="white")
    serial_lines = deque(maxlen=14)
    serial_text = ax4.text(
        0.02,
        0.98,
        "",
        transform=ax4.transAxes,
        color="black",
        fontsize=11,
        family="monospace",
        ha="left",
        va="top",
    )

    # ────────────────────────────────────────────────────────────────────
    # STOP button (sends 'S' then closes tracking window)
    # ────────────────────────────────────────────────────────────────────
    stop_ax = fig.add_axes([0.82, 0.02, 0.14, 0.05])
    stop_button = Button(
        stop_ax,
        "STOP + CLOSE",
        color="#A12731",
        hovercolor="#D64545",
    )

    def on_stop_clicked(_event):
        try:
            ser_mgr.stop()
        except Exception as e:
            print(f"[SER] Stop error: {e}")
        plt.close(fig)

    stop_button.on_clicked(on_stop_clicked)

    # ────────────────────────────────────────────────────────────────────
    # Anti-jitter parameters
    AZ_DEADBAND_DEG = 2.0
    EL_DEADBAND_DEG = 1.0
    MIN_INTERVAL_S = 1.0
    AZ_SLEW_DEG_PER_S = 8.0
    EL_SLEW_DEG_PER_S = 6.0
    QUANT_STEP_DEG = 0.5
    last_sent_time = [0.0]
    last_cmd = {"az": None, "el": None}
    smoothed = {"az": None, "el": None}
    ZENITH_FREEZE_DEG = 87.0   # hold az near zenith to avoid flips
    USE_AZ_UNWRAP = True       # keep az continuous so azimuth can exceed 360° if needed

    def unwrap_az(prev, new_0to360):
        """Return new azimuth near 'prev' by allowing ±360° jumps."""
        if prev is None:
            return new_0to360
        cands = (new_0to360, new_0to360 + 360.0, new_0to360 - 360.0)
        return min(cands, key=lambda x: abs(x - prev))

    def _quantize(v, step=QUANT_STEP_DEG):
        return round(v / step) * step

    def _slew_toward(current, target, max_rate_deg_s, dt):
        if current is None:
            return target
        max_delta = max_rate_deg_s * max(dt, 1e-3)
        dv = target - current
        if dv > max_delta:
            dv = max_delta
        if dv < -max_delta:
            dv = -max_delta
        return current + dv

    # Maps
    map1, map2 = create_maps(ax1, ax2, my_lat, my_lon)

    track_objs, track_lbls = [], []

    init_az_compass(ax_az)
    init_el_gauge(ax_el)
    plt.pause(0.01)  # force a draw with the correct formatting

    # ────────────────────────────────────────────────────────────────────
    def animate(frame_idx, sel_dict):
        nonlocal track_objs, track_lbls, serial_lines, serial_text

        # Clear dynamic artists
        for o in track_objs:
            try:
                o.remove()
            except Exception:
                pass
        track_objs.clear()
        for l in track_lbls:
            try:
                l.remove()
            except Exception:
                pass
        track_lbls.clear()

        # Gauges fresh
        ax_az.cla()
        init_az_compass(ax_az)
        ax_el.cla()
        init_el_gauge(ax_el)

        now = datetime.utcnow()
        az_cmd_local = None
        el_cmd_local = None

        # Use the first selected satellite as the drive reference.
        first_name = next(iter(sel_dict))

        # Get TLE lines by name from the file-based lookup.
        lkp = tle_lookup.get(_norm_name(first_name))
        if not lkp:
            serial_lines.append(f"{now:%H:%M:%S}  {first_name:<18} → [WARN] No TLE for name in file")
            serial_text.set_text("\n".join(serial_lines))
            return track_objs
        l1, l2 = lkp

        # Build or reuse the Skyfield satellite object
        key = (first_name, l1, l2)
        sat = _sat_cache.get(key)
        if sat is None:
            sat = EarthSatellite(l1, l2, first_name, ts)
            _sat_cache[key] = sat

        # Propagate and compute topocentric az/el
        t = ts.utc(
            now.year,
            now.month,
            now.day,
            now.hour,
            now.minute,
            now.second + now.microsecond * 1e-6,
        )
        gs = wgs84.latlon(my_lat, my_lon, elevation_m=0.0)
        alt_ref, az_ref, distance = (sat - gs).at(t).altaz()
        el_deg = alt_ref.degrees
        az_0to360 = az_ref.degrees % 360.0

        # ---- Anti-jitter + send ----
        if el_deg < 0:
            cmd_echo = "HOLD (below horizon)"
        else:
            if smoothed["az"] is None:
                smoothed["az"] = az_0to360
            if smoothed["el"] is None:
                smoothed["el"] = el_deg

            if USE_AZ_UNWRAP:
                target_azc = unwrap_az(smoothed["az"], az_0to360)
            else:
                target_azc = az_0to360

            if el_deg >= ZENITH_FREEZE_DEG:
                target_azc = smoothed["az"]

            dt = min(
                2.0,
                max(
                    0.05,
                    (now.timestamp() - last_sent_time[0]) if last_sent_time[0] > 0 else 0.6,
                ),
            )
            smoothed["az"] = _slew_toward(smoothed["az"], target_azc, AZ_SLEW_DEG_PER_S, dt)
            smoothed["el"] = _slew_toward(smoothed["el"], el_deg, EL_SLEW_DEG_PER_S, dt)

            # Clamp to mount limits; allow up to 450° for azimuth if supported.
            az_cmd = max(0, min(450, _quantize(smoothed["az"])))
            el_cmd = max(0, min(180, _quantize(smoothed["el"])))
            az_cmd_local = az_cmd
            el_cmd_local = el_cmd

            # Deadband and minimum time between commands
            if (
                last_cmd["az"] is not None
                and last_cmd["el"] is not None
                and abs(az_cmd - last_cmd["az"]) < AZ_DEADBAND_DEG
                and abs(el_cmd - last_cmd["el"]) < EL_DEADBAND_DEG
            ):
                cmd_echo = f"SKIP (deadband) → {az_cmd:6.2f} {el_cmd:6.2f}"
            elif last_sent_time[0] > 0 and (now.timestamp() - last_sent_time[0]) < MIN_INTERVAL_S:
                cmd_echo = f"SKIP (rate-limit) → {az_cmd:6.2f} {el_cmd:6.2f}"
            else:
                sent_cmd, reply = ser_mgr.send_move(az_cmd, el_cmd, echo_c2=True)
                last_cmd["az"], last_cmd["el"] = az_cmd, el_cmd
                last_sent_time[0] = now.timestamp()
                n2yo_style_debug(first_name, sat, t, note=f"Sent: {sent_cmd}")
                if reply:
                    cmd_echo = f"{sent_cmd} | echo: {reply}"
                else:
                    cmd_echo = f"{sent_cmd} | (no echo)"

        serial_lines.append(f"{now:%H:%M:%S}  {first_name:<18} → {cmd_echo}")
        serial_text.set_text("\n".join(serial_lines))

        # ---- Gauges ----
        ax_az.cla()
        init_az_compass(ax_az)
        ax_el.cla()
        init_el_gauge(ax_el)

        theta_az = math.radians(az_0to360)
        el_disp_raw = max(0.0, min(90.0, el_deg))
        theta_el = math.radians(el_disp_raw)

        # Azimuth needle
        ax_az.plot(
            [theta_az, theta_az],
            [0.0, 1.0],
            color="#A12731",
            linewidth=3,
            zorder=5,
        )
        ax_az.plot(
            [theta_az],
            [1.0],
            marker="o",
            markersize=8,
            markeredgecolor="black",
            markerfacecolor="#A12731",
            zorder=6,
        )

        # Elevation needle
        ax_el.plot(
            [0.0, theta_el],
            [0.0, 1.0],
            color="#A12731",
            linewidth=3,
            zorder=5,
        )
        ax_el.plot(
            [theta_el],
            [1.0],
            marker="o",
            markersize=8,
            markeredgecolor="black",
            markerfacecolor="#A12731",
            zorder=6,
        )

        # Commanded readouts (digital, to the left of each gauge)
        az_disp = az_cmd_local if az_cmd_local is not None else last_cmd.get("az")
        el_disp_cmd = el_cmd_local if el_cmd_local is not None else last_cmd.get("el")

        def _fmt_deg(v):
            return f"{v:6.1f}°" if v is not None else "--.-°"

        az_compass = az_to_compass(az_disp) if az_disp is not None else ""
        ax_az.text(
            -0.25,
            0.50,
            f"{_fmt_deg(az_disp)}\n{az_compass}",
            transform=ax_az.transAxes,
            ha="right",
            va="center",
            color="white",
            fontsize=12,
            family="monospace",
            path_effects=[pe.withStroke(linewidth=3, foreground="black")],
        )

        ax_el.text(
            -0.25,
            0.50,
            _fmt_deg(el_disp_cmd),
            transform=ax_el.transAxes,
            ha="right",
            va="center",
            color="white",
            fontsize=12,
            family="monospace",
            path_effects=[pe.withStroke(linewidth=3, foreground="black")],
        )

        # ---- Maps ----
        ax2.cla()
        draw_nearsided_background(map2, ax2, my_lat, my_lon)

        subpoint = sat.at(t).subpoint()
        sat_lat = subpoint.latitude.degrees
        sat_lon = subpoint.longitude.degrees
        alt_km = subpoint.elevation.km

        xs, ys = map2(sat_lon, sat_lat)
        p2, = ax2.plot(xs, ys, "r*", markersize=10, zorder=10)
        track_objs.append(p2)

        xg1, yg1 = map1(sat_lon, sat_lat)
        p1, = ax1.plot(xg1, yg1, "r*", markersize=8, zorder=10)
        track_objs.append(p1)
        lbl = ax1.annotate(
            first_name,
            xy=(xg1, yg1),
            xytext=(xg1 + 6, yg1 + 6),
            color="black",
            fontsize=9,
            zorder=11,
        )
        track_lbls.append(lbl)

        geoc = sat.at(t)
        vx, vy, vz = geoc.velocity.km_per_s
        speed = (vx * vx + vy * vy + vz * vz) ** 0.5
        plt.suptitle(
            f"UTC {now:%Y-%m-%d %H:%M:%S} | {first_name}  "
            f"Lat {sat_lat:+7.2f}°  Lon {sat_lon:+8.2f}°  Alt {alt_km:.0f} km  |  {speed:.2f} km/s",
            color="black",
        )
        return track_objs

    ani = animation.FuncAnimation(
        fig,
        animate,
        fargs=(selected,),
        interval=600,
        blit=False,
        repeat=False,
    )

    _ANIMATIONS.append(ani)

    import matplotlib.pyplot as plt  # re-import to keep local alias
    plt.show()


import tkinter as tk  # noqa: E402
from fetch_tle import fetch_group  # noqa: E402
from keplerian_parser import ParseTwoLineElementFile  # noqa: E402


def _format_next_peak(pass_list):
    """Return 'HH:MM @ XX°' for the next pass peak, or None."""
    if not pass_list:
        return None

    def _peak_time(p):
        return getattr(p, "peak_utc", getattr(p, "t_peak", getattr(p, "peak", None)))

    def _max_el(p):
        return getattr(p, "max_el_deg", getattr(p, "max_el", None))

    candidates = [p for p in pass_list if _peak_time(p) is not None]
    if not candidates:
        return None
    p = min(candidates, key=lambda x: _peak_time(x))
    t_utc = _peak_time(p)
    el = _max_el(p)
    try:
        t_local = t_utc.astimezone(LOCAL_TZ)
        t_str = t_local.strftime("%H:%M")
        if el is not None:
            return f"{t_str} @ {round(float(el))}°"
    except Exception:
        pass
    return None


def SetupWindow(root, my_lat, my_lon, tle_cache=None, vis_cache=None):
    """
    Build the satellite selector window.

    Parameters
    ----------
    root : tk.Tk
        Root window.
    my_lat, my_lon : float
        Ground-station latitude/longitude in degrees.
    tle_cache : dict or None
        Optional pre-fetched mapping {group_key: tle_path}.
    vis_cache : dict or None
        Optional visibility summaries {group_key: {sat_name: SatPassSummary}}.

    Returns
    -------
    checkbox_dict : dict
        Mapping {sat_name: tk.IntVar} for all checkboxes.
    """
    root.title("Satellite Selector")
    root.configure(bg="white")

    # ── Group selector ──────────────────────────────────────────────────
    frame0 = tk.Frame(root, bg="white")
    frame0.grid(row=0, column=0, padx=10, pady=10, sticky="w")

    group_var = tk.StringVar(value="Amateur")
    tk.Label(frame0, text="Select Group:", bg="white").pack(side="left")
    group_menu = tk.OptionMenu(
        frame0,
        group_var,
        "Amateur",
        "NOAA",
        "GOES",
        "Weather",
        "CUBESAT",
        "SATNOGS",
    )
    group_menu.pack(side="left")

    # ── Scrollable checkbox area: V + H scrollbars ─────────────────────
    frame1_container = tk.Frame(root, bg="white")
    frame1_container.grid(row=1, column=0, padx=10, pady=10, sticky="nsew")

    root.grid_rowconfigure(1, weight=1)
    root.grid_columnconfigure(0, weight=1)

    canvas = tk.Canvas(frame1_container, bg="white", highlightthickness=0)
    vsb = tk.Scrollbar(frame1_container, orient="vertical", command=canvas.yview)
    hsb = tk.Scrollbar(frame1_container, orient="horizontal", command=canvas.xview)
    canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

    canvas.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hsb.grid(row=1, column=0, sticky="ew")

    frame1_container.grid_rowconfigure(0, weight=1)
    frame1_container.grid_columnconfigure(0, weight=1)

    list_frame = tk.Frame(canvas, bg="white")
    list_window = canvas.create_window((0, 0), window=list_frame, anchor="nw")  # noqa: F841

    def _on_list_config(_evt=None):
        canvas.configure(scrollregion=canvas.bbox("all"))

    list_frame.bind("<Configure>", _on_list_config)

    checkbox_dict = {}

    def _bind_mousewheel(canvas):
        """Cross-platform mousewheel binding (vertical + horizontal)."""
        system = canvas.tk.call("tk", "windowingsystem")

        def on_wheel(e):
            step = -1 if getattr(e, "delta", 0) > 0 else 1
            canvas.yview_scroll(step, "units")

        def on_wheel_h(e):
            step = -1 if getattr(e, "delta", 0) > 0 else 1
            canvas.xview_scroll(step, "units")

        def on_linux_up(e):    canvas.yview_scroll(-1, "units")  # noqa: E306
        def on_linux_down(e):  canvas.yview_scroll(1, "units")
        def on_linux_left(e):  canvas.xview_scroll(-1, "units")
        def on_linux_right(e): canvas.xview_scroll(1, "units")

        def enable(_):
            if system in ("win32", "aqua"):
                canvas.bind_all("<MouseWheel>", on_wheel)
                canvas.bind_all("<Shift-MouseWheel>", on_wheel_h)
            else:
                canvas.bind_all("<Button-4>", on_linux_up)
                canvas.bind_all("<Button-5>", on_linux_down)
                canvas.bind_all("<Shift-Button-4>", on_linux_left)
                canvas.bind_all("<Shift-Button-5>", on_linux_right)

        def disable(_):
            if system in ("win32", "aqua"):
                canvas.unbind_all("<MouseWheel>")
                canvas.unbind_all("<Shift-MouseWheel>")
            else:
                canvas.unbind_all("<Button-4>")
                canvas.unbind_all("<Button-5>")
                canvas.unbind_all("<Shift-Button-4>")
                canvas.unbind_all("<Shift-Button-5>")

        canvas.bind("<Enter>", enable)
        canvas.bind("<Leave>", disable)

    _bind_mousewheel(canvas)

    def load_satellites():
        """Populate the checkbox list for the selected group."""
        nonlocal checkbox_dict
        for w in list_frame.winfo_children():
            w.destroy()
        checkbox_dict.clear()

        LABEL_TO_KEY = {
            "Amateur": "Amateur",
            "NOAA": "NOAA",
            "GOES": "GOES",
            "Weather": "Weather",
            "CUBESAT": "CUBESAT",
            "SATNOGS": "SATNOGS",
        }
        key = LABEL_TO_KEY.get(group_var.get())
        if key is None:
            tk.messagebox.showerror("Unknown group", f"{group_var.get()} is not configured.")
            return

        # 1) Get TLE filename from cache (no network access here)
        if tle_cache is not None and key in tle_cache:
            tle_filename = tle_cache[key]
        else:
            from fetch_tle import fetch_group as _fetch_group  # local import
            tle_filename = _fetch_group(key)

        # 2) Parse satellite names for GUI
        tle_dict = ParseTwoLineElementFile(tle_filename)

        # 3) Get visibility map from vis_cache, do not recompute here
        if vis_cache is not None and key in vis_cache:
            visibility_map = vis_cache[key]
        else:
            visibility_map = {}

        row_ctr, col_ctr = 0, 0
        for sat_name in sorted(tle_dict.keys()):
            vis_summary = visibility_map.get(sat_name)
            has_pass = bool(vis_summary and getattr(vis_summary, "passes", None))

            label_text = sat_name
            if has_pass:
                peak_str = _format_next_peak(vis_summary.passes)
                if peak_str:
                    label_text = f"{sat_name}  [{peak_str}]"

            bg_color = "pale green" if has_pass else "white"
            active_bg = bg_color

            var = tk.IntVar(value=0)
            cb = tk.Checkbutton(
                list_frame,
                text=label_text,
                variable=var,
                fg="black",
                bg=bg_color,
                activebackground=active_bg,
                anchor="w",
                selectcolor=bg_color,
                padx=2,
            )
            cb.grid(row=row_ctr, column=col_ctr, sticky=tk.W, pady=2, padx=4)
            checkbox_dict[sat_name] = var

            row_ctr += 1
            if row_ctr >= 30:
                row_ctr = 0
                col_ctr += 1

        list_frame.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))

        run_button.config(
            command=lambda: runPredictionTool(
                checkbox_dict,
                tle_dict,
                my_lat,
                my_lon,
                tle_path=tle_filename,
            )
        )

    # Buttons row
    frame2 = tk.Frame(root, bg="white")
    frame2.grid(row=2, column=0, padx=10, pady=10)
    run_button = tk.Button(frame2, text="Run Prediction")
    run_button.grid(row=0, column=0, padx=5, pady=5)
    tk.Button(frame2, text="Quit Program", command=root.quit).grid(row=0, column=1, padx=5, pady=5)

    # Initial load + reload on selection change
    load_satellites()
    group_var.trace_add("write", lambda *_: load_satellites())

    return checkbox_dict


if __name__ == "__main__":
    from fetch_tle import fetch_group
    from pass_visibility import compute_pass_visibility_for_file

    # Ground station
    my_lat = 41.19502233287143
    my_lon = -111.94128097234622

    GROUP_KEYS = ["Amateur", "NOAA", "GOES", "Weather", "CUBESAT", "SATNOGS"]

    # 1) Prefetch all TLE groups at startup
    tle_cache = {}
    for key in GROUP_KEYS:
        try:
            tle_path = fetch_group(key)
            tle_cache[key] = tle_path
            print(f"[TLE] Prefetched group {key}: {tle_path}")
        except Exception as e:
            print(f"[TLE] Failed to fetch group {key}: {e}")

    # 2) Compute visibility once per group at startup
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    RESET = "\033[0m"

    vis_cache = {}
    for key, tle_path in tle_cache.items():
        print(f"{RESET}[VIS] Computing visibility for {key} ...")

        start = time.perf_counter()
        try:
            vis_map = compute_pass_visibility_for_file(
                tle_path=tle_path,
                my_lat=my_lat,
                my_lon=my_lon,
            )
            elapsed = time.perf_counter() - start

            vis_cache[key] = vis_map
            print(f"{GREEN}[VIS] {key} done in {elapsed:.2f}s{RESET}")

        except Exception as e:
            elapsed = time.perf_counter() - start
            print(f"{RED}[VIS] FAILED for {key} after {elapsed:.2f}s → {e}{RESET}")
            vis_cache[key] = {}

    # 3) Launch GUI with both caches
    root = tk.Tk()
    root.geometry("1100x900")

    checkbox_dict = SetupWindow(
        root,
        my_lat,
        my_lon,
        tle_cache=tle_cache,
        vis_cache=vis_cache,
    )
    root.mainloop()
