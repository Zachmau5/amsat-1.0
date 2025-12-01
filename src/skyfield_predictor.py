#!/usr/bin/env python3
"""
Skyfield-based satellite lookup and pointing helpers.

Purpose
-------
Provide small utilities built on top of Skyfield to:
  - Load and cache TLE files.
  - Resolve satellites by name or NORAD ID.
  - Compute azimuth/elevation from a ground station.
  - Generate simple sub-satellite ground tracks.
  - Compute azimuth/elevation for multiple satellites at once.

Role in System
--------------
- Consumed by higher-level tools (e.g., tracking GUIs or analysis scripts)
  that need quick, Skyfield-based pointing and ground-track data.
- Reads local TLE files and returns Skyfield EarthSatellite objects.
- Independent of any GUI or GS-232B logic.

High-level Flow (Pseudocode)
----------------------------
  1. Create a process-wide Loader and timescale and keep them in module globals.
  2. Maintain an in-memory cache mapping TLE path -> TLEIndex (sats + lookups).
  3. When a TLE file is requested:
       - If cached, return the cached TLEIndex.
       - Otherwise, load it with Skyfield, build name and NORAD lookups, cache it.
  4. Expose helpers:
       - list_satellites(tle_path): list display names.
       - get_satellite(tle_path, key): resolve by normalized name or NORAD.
       - az_el_at(sat, lat, lon, elev, when): compute (az, el, range_km).
       - groundtrack(sat, start, minutes, step_s): compute lons/lats along track.
       - multi_az_el(sats, lat, lon, elev, when): az/el for multiple sats at once.
       - n2yo_style_debug(...): optional diagnostic print helper.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from skyfield.api import EarthSatellite, Loader, wgs84

# Single Loader instance; ephemeris cache is stored under ./skyfield-data.
_sky_loader = Loader("./skyfield-data")
_ts = _sky_loader.timescale()

# In-memory cache so each TLE file is only parsed once per process.
_TLE_CACHE: Dict[str, "TLEIndex"] = {}


def _norm_key(s: str) -> str:
    """Normalize satellite names/keys: collapse whitespace, upper-case."""
    return "".join(s.split()).upper()


@dataclass
class TLEIndex:
    tle_path: str
    sats: List[EarthSatellite]
    by_name: Dict[str, EarthSatellite]   # normalized name -> sat
    by_norad: Dict[str, EarthSatellite]  # NORAD (string) -> sat


def load_tle_index(tle_path: str) -> TLEIndex:
    """
    Load and index satellites from a TLE file.

    Indexing:
      - by normalized .name (spaces removed, upper-case).
      - by NORAD catalog number (string).

    Instances are cached per-process by path so repeated calls are inexpensive.
    """
    global _TLE_CACHE
    if tle_path in _TLE_CACHE:
        return _TLE_CACHE[tle_path]

    sats: List[EarthSatellite] = _sky_loader.tle_file(tle_path)
    by_name: Dict[str, EarthSatellite] = {}
    by_norad: Dict[str, EarthSatellite] = {}

    for sat in sats:
        # Index by normalized printed name
        by_name[_norm_key(sat.name or "")] = sat
        # Index by catalog number if available
        try:
            norad = str(int(sat.model.satnum))
            by_norad[norad] = sat
        except Exception:
            pass

    idx = TLEIndex(tle_path=tle_path, sats=sats, by_name=by_name, by_norad=by_norad)
    _TLE_CACHE[tle_path] = idx
    return idx


def list_satellites(tle_path: str) -> List[str]:
    """Return display names for all satellites in a TLE file."""
    return [sat.name for sat in load_tle_index(tle_path).sats]


def get_satellite(
    tle_path: str,
    key: str,
    allow_prefix: bool = True
) -> EarthSatellite:
    """
    Retrieve a satellite by:
      - exact/loose name (case/space-insensitive), or
      - NORAD catalog number (string or int).

    If allow_prefix is True, a unique prefix of the name will match.

    Raises ValueError if no unique match is found.
    """
    idx = load_tle_index(tle_path)
    k = str(key).strip()

    # Try NORAD first if key is numeric
    if k.isdigit():
        if k in idx.by_norad:
            return idx.by_norad[k]
        raise ValueError(f"NORAD {k} not found in {tle_path}")

    # Normalize for name lookups
    nk = _norm_key(k)
    if nk in idx.by_name:
        return idx.by_name[nk]

    # Optional: prefix match on names
    if allow_prefix:
        matches = [sat for name, sat in idx.by_name.items() if name.startswith(nk)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = ", ".join(sorted({sat.name for sat in matches})[:8])
            raise ValueError(
                f"Name prefix '{key}' is ambiguous ({len(matches)} matches). "
                f"Examples: {names} ..."
            )

    # Fallback: substring search (unique required)
    candidates = [sat for name, sat in idx.by_name.items() if nk in name]
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        names = ", ".join(sorted({sat.name for sat in candidates})[:8])
        raise ValueError(
            f"Name '{key}' is ambiguous ({len(candidates)} matches). "
            f"Examples: {names} ..."
        )
    raise ValueError(f"Satellite '{key}' not found in {tle_path}")


def az_el_at(
    sat: EarthSatellite,
    lat_deg: float,
    lon_deg: float,
    elev_m: float = 0.0,
    when: Optional[datetime] = None,
) -> Tuple[float, float, float]:
    """
    Compute (az_deg, el_deg, range_km) from a ground site to a satellite at time 'when'.

    Datetime handling:
      - If 'when' is None, current UTC is used.
      - If 'when' is naive, it is interpreted as UTC.
    """
    if when is None:
        when = datetime.now(timezone.utc)
    elif when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    t = _ts.from_datetime(when.astimezone(timezone.utc))
    gs = wgs84.latlon(latitude_degrees=lat_deg, longitude_degrees=lon_deg, elevation_m=elev_m)
    topocentric = (sat - gs).at(t)
    alt, az, distance = topocentric.altaz()
    return (az.degrees % 360.0, alt.degrees, distance.km)


def groundtrack(
    sat: EarthSatellite,
    start: Optional[datetime] = None,
    minutes: int = 90,
    step_s: int = 10,
) -> Tuple[List[float], List[float]]:
    """
    Compute a simple sub-satellite ground track.

    Parameters
    ----------
    sat : EarthSatellite
        Skyfield satellite object.
    start : datetime, optional
        Start time in UTC (naive treated as UTC). Defaults to now.
    minutes : int
        Total duration of the track.
    step_s : int
        Time step in seconds.

    Returns
    -------
    (lons_deg, lats_deg) : list of float
        Lists of longitudes and latitudes along the ground track.
    """
    if start is None:
        start = datetime.now(timezone.utc)
    elif start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)

    steps = max(1, int((minutes * 60) // step_s))
    times = _ts.utc(
        [(start + timedelta(seconds=i * step_s)).year for i in range(steps)],
        [(start + timedelta(seconds=i * step_s)).month for i in range(steps)],
        [(start + timedelta(seconds=i * step_s)).day for i in range(steps)],
        [(start + timedelta(seconds=i * step_s)).hour for i in range(steps)],
        [(start + timedelta(seconds=i * step_s)).minute for i in range(steps)],
        [(start + timedelta(seconds=i * step_s)).second for i in range(steps)],
    )

    lons: List[float] = []
    lats: List[float] = []
    for t in times:
        sp = sat.at(t).subpoint()
        lats.append(sp.latitude.degrees)
        lons.append(sp.longitude.degrees)
    return (lons, lats)


def multi_az_el(
    sats: Iterable[EarthSatellite],
    lat_deg: float,
    lon_deg: float,
    elev_m: float = 0.0,
    when: Optional[datetime] = None,
) -> Dict[str, Tuple[float, float, float]]:
    """
    Vector helper for an iterable of EarthSatellite objects.

    Returns a mapping:
        {sat.name: (az_deg, el_deg, range_km)}
    """
    if when is None:
        when = datetime.now(timezone.utc)
    elif when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    t = _ts.from_datetime(when.astimezone(timezone.utc))
    gs = wgs84.latlon(lat_deg, lon_deg, elevation_m=elev_m)

    results: Dict[str, Tuple[float, float, float]] = {}
    for sat in sats:
        alt, az, distance = (sat - gs).at(t).altaz()
        results[sat.name] = (az.degrees % 360.0, alt.degrees, distance.km)
    return results


def n2yo_style_debug(sat, ts, when=None):
    """
    Print an N2YO-style summary for a given Skyfield EarthSatellite.

    This is a diagnostic helper for manual comparison against online trackers.
    """
    if when is None:
        when = datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    t = ts.from_datetime(when)
    geocentric = sat.at(t)
    subpoint = geocentric.subpoint()

    # Lat/Lon/Alt
    lat = subpoint.latitude.degrees
    lon = subpoint.longitude.degrees
    alt_km = subpoint.elevation.km

    # Velocity vector and speed
    vel = geocentric.velocity.km_per_s  # (vx, vy, vz)
    speed_km_s = (vel[0] ** 2 + vel[1] ** 2 + vel[2] ** 2) ** 0.5
    # Caller can log or print these values as needed.
