#!/usr/bin/env python3
"""
GUI helpers for Basemap-based global and near-sided (QTH-centered) maps.

Purpose
-------
Factor out Basemap setup and drawing so that the main tracking code
(main_gs232b.py) can focus on control logic instead of cartography.

Role in System
--------------
- create_maps(): builds and initializes two Basemap instances:
    - A global map.
    - A near-sided view centered on the ground station.
- draw_nearsided_background(): refreshes the near-sided map background
  between animation frames (coasts, graticule, QTH marker, etc.).

High-level Flow (Pseudocode)
----------------------------
  1. create_maps(ax_global, ax_near, my_lat, my_lon):
       - Create a global “mill” projection bound to ax_global.
       - Draw boundaries, continents, coasts, and QTH marker.
       - Create a near-sided nsper projection centered at QTH bound to ax_near.
       - Draw a dark background and QTH marker.
       - Return (map_global, map_near).
  2. draw_nearsided_background(map_near, ax_near, my_lat, my_lon):
       - Clear and redraw the near-sided view:
           * Oceans, continents, coasts.
           * Graticule.
           * QTH marker and label.
"""
from mpl_toolkits.basemap import Basemap


def create_maps(ax_global, ax_near, my_lat, my_lon):
    """
    Create and initialize the two Basemap instances.

    Returns
    -------
    (map_global, map_near) : tuple[Basemap, Basemap]
    """
    # Global map
    map_global = Basemap(
        projection="mill",
        llcrnrlat=-90,
        urcrnrlat=90,
        llcrnrlon=-180,
        urcrnrlon=180,
        resolution="c",
        ax=ax_global,
    )
    map_global.drawmapboundary(fill_color="white")
    map_global.fillcontinents(color="gray", lake_color="blue")
    map_global.drawcoastlines()
    ax_global.set_facecolor("white")
    ax_global.set_title("Global View", color="black")

    # QTH marker on global map
    x_q_g, y_q_g = map_global(my_lon, my_lat)
    map_global.plot(x_q_g, y_q_g, "go", markersize=8)
    ax_global.annotate(
        "Me",
        xy=(x_q_g, y_q_g),
        xytext=(x_q_g + 5, y_q_g + 5),
        color="black",
    )

    # Near-sided (nsper) map
    map_near = Basemap(
        projection="nsper",
        lon_0=my_lon,
        lat_0=my_lat,
        satellite_height=2000 * 1000.0,
        resolution="l",
        ax=ax_near,
    )

    # Draw initial background (QTH marker, coastlines, etc.)
    draw_nearsided_background(map_near, ax_near, my_lat, my_lon)

    return map_global, map_near


def draw_nearsided_background(map_near, ax_near, my_lat, my_lon):
    """
    Redraw the near-sided (QTH-centered) background.

    This matches your original draw_nearsided_background() logic:
      - dark oceans
      - continents, coasts, states, countries
      - graticule
      - QTH marker + label
    """
    ax_near.set_facecolor("black")

    # Make sure Basemap is bound to the correct Axes
    map_near.ax = ax_near

    # Ocean / boundary
    map_near.drawmapboundary(fill_color="aqua")
    map_near.drawmapboundary(fill_color="#1a1a1a")  # dark gray ocean

    # Continents
    map_near.fillcontinents(
        color="#444444",
        lake_color="#1a1a1a",
        zorder=1,
    )

    # Coastlines + graticule
    map_near.drawcoastlines(color="white", linewidth=0.4)
    map_near.drawparallels(
        range(-90, 91, 10),
        color="gray",
        dashes=[1, 1],
        linewidth=0.3,
    )
    map_near.drawmeridians(
        range(-180, 181, 10),
        color="gray",
        dashes=[1, 1],
        linewidth=0.3,
    )

    # Political boundaries (where available)
    try:
        map_near.drawstates()
    except Exception:
        pass
    try:
        map_near.drawcountries()
    except Exception:
        pass

    # QTH marker on near-sided view
    xq, yq = map_near(my_lon, my_lat)
    ax_near.plot(xq, yq, "go", markersize=8, zorder=5)
    ax_near.annotate(
        "Me",
        xy=(xq, yq),
        xytext=(xq + 5, yq + 5),
        color="white",
        zorder=6,
    )

    ax_near.set_title("Near-Sided (QTH-centered)", color="white")
