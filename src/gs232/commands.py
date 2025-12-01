#!/usr/bin/env python3
"""
GS-232B command formatting and parsing helpers.

Purpose
-------
Provide small pure functions for formatting and parsing GS-232B commands
and replies. No serial I/O is performed here.

Role in System
--------------
- Used by serial_manager.py and other GS-232B helpers.
- Keeps string-format details in one place.
"""

def format_move(az_deg: float, el_deg: float) -> str:
    """
    Format an absolute move command.

    Example
    -------
    >>> format_move(180.2, 45.7)
    'W180 046'
    """
    az = int(round(az_deg))
    el = int(round(el_deg))
    return f"W{az:03d} {el:03d}"


def parse_c2_reply(reply: str):
    """
    Parse a GS-232B C2 reply into (az_deg, el_deg).

    Accepts several common formats such as:
      - '+0180+0090'
      - 'AZ=180 EL=90'
      - '180 90'
    Returns (az, el) or None on failure.
    """
    if not reply:
        return None

    try:
        line = reply.strip()
        if line.startswith("+") or line.startswith("-"):
            az = int(line[0:4])
            el = int(line[4:8])
            return float(az), float(el)

        line = line.replace("AZ", "").replace("EL", "")
        line = line.replace("=", " ").replace(",", " ")
        parts = line.split()
        nums = [float(p) for p in parts if p]
        if len(nums) == 2:
            return nums[0], nums[1]
    except Exception:
        pass
    return None


# Common static commands
STOP_CMD = "S"
STATUS_CMD = "C2"
HELP_CMD = "H"
HELP2_CMD = "H2"
MODE_450_CMD = "P45"
