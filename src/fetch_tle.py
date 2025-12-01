#!/usr/bin/env python3
"""
TLE fetch utilities for CelesTrak sources.

Purpose
-------
Download and cache Two-Line Element (TLE) sets by group name.

Role in System
--------------
- Used at startup by the main GUI to populate the local `tle/` directory.
- Provides `fetch_group()` as the single entry point:
    - Fetches one of the preconfigured CelesTrak groups.
    - Writes the TLE file under `src/tle/<group>.tle`.
    - Returns the filesystem path as a string.

High-level Flow (Pseudocode)
----------------------------
  1. Define GROUP_URLS mapping group labels to CelesTrak URLs.
  2. On fetch_and_save_tle(url, filename):
       a. Ensure TLE_DIR exists.
       b. Issue HTTP GET with a short User-Agent.
       c. On success, write raw TLE text to filename and log timing.
       d. On failure:
            - Warn with elapsed time.
            - If a cached file exists, keep using it.
            - If no cache exists, re-raise the exception.
  3. On fetch_group(group_name):
       a. Validate group_name against GROUP_URLS.
       b. Call fetch_and_save_tle() with the configured URL.
       c. Return the path as a string.
"""

from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import time

RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
RESET = "\033[0m"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GROUP_URLS = {
    "Amateur": "https://celestrak.org/NORAD/elements/gp.php?GROUP=AMATEUR&FORMAT=TLE",
    "NOAA":    "https://celestrak.org/NORAD/elements/gp.php?GROUP=NOAA&FORMAT=TLE",
    "GOES":    "https://celestrak.org/NORAD/elements/gp.php?GROUP=GOES&FORMAT=TLE",
    "Weather": "https://celestrak.org/NORAD/elements/gp.php?GROUP=WEATHER&FORMAT=TLE",
    "CUBESAT": "https://celestrak.org/NORAD/elements/gp.php?GROUP=CUBESAT&FORMAT=TLE",
    "SATNOGS": "https://celestrak.org/NORAD/elements/gp.php?GROUP=satnogs&FORMAT=TLE",
}

# Base directory for TLE files: <repo_root>/src/tle
TLE_DIR = Path(__file__).parent / "tle"


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def fetch_and_save_tle(url: str, filename: Path, timeout: int = 30) -> None:
    """
    Download a TLE set from `url` and save it to `filename`.

    On failure, log a warning and fall back to any existing cached file.
    """
    TLE_DIR.mkdir(parents=True, exist_ok=True)

    start = time.perf_counter()
    try:
        req = Request(url, headers={"User-Agent": "amsat-1.0"})
        with urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8")

        with open(filename, "w", encoding="utf-8") as f:
            f.write(text)

        elapsed = time.perf_counter() - start
        print(f"{GREEN}[TLE] Downloaded fresh TLE → {filename} ({elapsed:.1f}s){RESET}")

    except (URLError, HTTPError, OSError, TimeoutError) as e:
        elapsed = time.perf_counter() - start
        print(f"{RED}[TLE] WARNING: Failed to download {url} after {elapsed:.1f}s: {e}{RESET}")

        if filename.exists():
            print(f"{YELLOW}[TLE] Using cached TLE file instead → {filename}{RESET}")
        else:
            print(f"{RED}[TLE] ERROR: No cached TLE file exists at {filename}{RESET}")
            raise


def fetch_group(group_name: str, timeout: int = 30) -> str:
    """
    Fetch a TLE group (Amateur, NOAA, GOES, Weather, CUBESAT, SATNOGS).

    Returns
    -------
    str
        Path to the TLE file.
    """
    if group_name not in GROUP_URLS:
        raise ValueError(f"Unknown group: {group_name}")

    url = GROUP_URLS[group_name]
    filename = TLE_DIR / f"{group_name.lower()}.tle"
    fetch_and_save_tle(url, filename, timeout=timeout)
    return str(filename)


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    fname = fetch_group("Amateur")
    print(f"Saved to {fname}")
