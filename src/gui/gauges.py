"""
GUI helpers for azimuth and elevation gauges.

Purpose
-------
Provide drawing utilities for the polar azimuth compass and the
elevation gauge used by the tracking GUI.

Role in System
--------------
- az_to_compass(): convert azimuth degrees to a 16-point compass label.
- init_az_compass(ax): draw azimuth polar grid and labels.
- init_el_gauge(ax): draw elevation gauge (0° horizon, 90° zenith).
"""

import math
import matplotlib.patheffects as pe


def az_to_compass(az: float) -> str:
    """
    Convert azimuth in degrees to a 16-point compass label
    (N, NNE, NE, ..., NNW).
    """
    dirs = [
        "N", "NNE", "NE", "ENE",
        "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW",
        "W", "WNW", "NW", "NNW",
    ]
    return dirs[int((az / 22.5) + 0.5) % 16]


def init_az_compass(ax):
    """
    Initialize the azimuth polar gauge:
      - Black background
      - 0° at North, clockwise
      - Minor gridlines every 30°
      - Bright lines at 0/90/180/270
      - Cardinal labels N / E / S / W
    """
    ax.set_facecolor("black")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_rlim(0, 1.0)
    ax.set_rticks([])
    ax.set_xticklabels([])

    ax.text(
        0.5,
        1.08,
        "Azimuth",
        transform=ax.transAxes,
        ha="center",
        va="bottom",
        color="white",
        fontsize=12,
        path_effects=[pe.withStroke(linewidth=3, foreground="black")],
    )

    # Faint minor rings (range circles)
    for r in (0.33, 0.66, 1.0):
        ax.plot([0, 2 * math.pi], [r, r], color="white", alpha=0.15, linewidth=1)

    # Minor gridlines every 30°
    for ang in range(0, 360, 30):
        t = math.radians(ang)
        ax.plot([t, t], [0.0, 1.0], color="white", alpha=0.15, linewidth=1)

    # Major lines at cardinal directions (0°, 90°, 180°, 270°)
    for ang in [0, 90, 180, 270]:
        t = math.radians(ang)
        ax.plot(
            [t, t],
            [0.0, 1.0],
            color="yellow",
            alpha=0.8,
            linewidth=2,
        )

    # Cardinal direction labels
    for ang, lab in [(0, "N"), (90, "E"), (180, "S"), (270, "W")]:
        ax.text(
            math.radians(ang),
            0.75,
            lab,
            color="white",
            ha="center",
            va="bottom",
            fontsize=11,
            path_effects=[pe.withStroke(linewidth=3, foreground="black")],
        )


def init_el_gauge(ax):
    """
    Initialize the elevation polar gauge:
      - 0° at horizon, 90° at zenith
      - Black background
      - Major gridlines at 0°, 30°, 60°, 90°
      - Degree labels along the arc
    """
    ax.set_facecolor("black")
    ax.set_theta_zero_location("W")
    ax.set_theta_direction(-1)
    ax.set_thetamin(0)
    ax.set_thetamax(90)
    ax.set_rlim(0, 1.0)
    ax.set_rticks([])
    ax.set_xticklabels([])

    # Title
    ax.text(
        0.5,
        -0.18,
        "Elevation",
        transform=ax.transAxes,
        ha="center",
        va="top",
        color="white",
        fontsize=12,
        path_effects=[pe.withStroke(linewidth=3, foreground="black")],
    )

    # Major gridlines at 0°, 30°, 60°, 90°
    for ang in [0, 30, 60, 90]:
        t = math.radians(ang)
        ax.plot(
            [t, t],
            [0.0, 1.0],
            color="yellow" if ang in [0, 90] else "white",
            alpha=0.8 if ang in [0, 90] else 0.3,
            linewidth=2 if ang in [0, 90] else 1,
        )
        ax.text(
            t,
            1.05,
            f"{ang}°",
            color="white",
            fontsize=9,
            ha="center",
            va="bottom",
        )

    # Concentric rings (depth cues)
    for r in (0.33, 0.66, 1.0):
        ax.plot(
            [math.radians(0), math.radians(90)],
            [r, r],
            color="white",
            alpha=0.15,
            linewidth=1,
        )
