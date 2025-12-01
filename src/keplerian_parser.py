#!/usr/bin/env python3
"""
Keplerian orbital element parser for TLE files.

Purpose
-------
Parse a local Two-Line Element (TLE) text file and return a dictionary
mapping each satellite name to a NumPy array of orbital elements.

Role in System
--------------
- Used by analysis scripts or legacy Keplerian propagation code.
- Independent of the Skyfield-based path.

High-level Flow (Pseudocode)
----------------------------
  1. Read the file and split into lines.
  2. Walk lines with a counter modulo 3:
       - counter == 0: satellite name line.
       - counter == 1: first data line (epoch + drag term).
       - counter == 2: second data line (i, RAAN, e, ω, M, n).
  3. For each 3-line group:
       a. Extract epoch year + fractional day and drag term.
       b. Extract inclination, RAAN, eccentricity, argument of perigee,
          mean anomaly, and mean motion.
       c. Store them in a length-9 float array in a fixed order.
       d. Insert the array into a dictionary keyed by satellite name.
  4. Return the dictionary.
"""

import numpy as np
def ParseTwoLineElementFile(filename: str = "amateur.tle"):
    """
    Parse a TLE text file organized in sets of three lines per satellite and
    return a dictionary mapping satellite name to a NumPy array containing:
        0: epoch year (YY)
        1: epoch day-of-year (fractional)
        2: inclination (deg)
        3: RAAN (deg)
        4: eccentricity
        5: argument of perigee (deg)
        6: mean anomaly (deg)
        7: mean motion (rev/day)
        8: drag term (B*)
    """
    with open(filename, "r") as f:
        lines = f.read().splitlines()

    counter = 0
    results = np.zeros(9, dtype=float)
    results_dict = {}

    for line in lines:
        split_line = line.split()

        if counter == 0:
            sat_name = line.strip() if line.strip() else "UNKNOWN"

        elif counter == 1:
            split_line = list(filter(None, split_line))
            epoch_info = split_line[3]    # "YYDDD.DDDDDDDD"
            epoch_year = epoch_info[0:2]
            epoch_remainder = epoch_info[2:]
            drag_term = split_line[4]

            results[0] = float(epoch_year)
            results[1] = float(epoch_remainder)
            results[8] = float(drag_term)

        elif counter == 2:
            split_line = list(filter(None, split_line))
            inclination = split_line[2]
            raan = split_line[3]

            ecc_str = split_line[4]
            if not ecc_str.startswith("."):
                ecc_str = "." + ecc_str

            arg_perigee = split_line[5]
            mean_anomaly = split_line[6]
            mean_motion = split_line[7]

            results[2] = float(inclination)
            results[3] = float(raan)
            results[4] = float(ecc_str)
            results[5] = float(arg_perigee)
            results[6] = float(mean_anomaly)
            results[7] = float(mean_motion)

            results_dict[sat_name] = results
            results = np.zeros(9, dtype=float)

        counter = (counter + 1) % 3

    return results_dict
