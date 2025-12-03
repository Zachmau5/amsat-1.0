#!/usr/bin/env python3
# =============================================================================
# File: funcube_parser.py
# Author: Josh Brown
# Date: 2025-04-07
#
# Description:
#   This module implements a complete telemetry parser for FUNcube-1 (AO-73)
#   256-byte AO-40-FEC frames. It decodes:
#       • Satellite ID
#       • Frame type (Whole-Orbit, High-Resolution, Fitter messages)
#       • 440-bit Real-Time telemetry block (55 bytes)
#       • 200-byte payload block
#
#   Input is a raw byte stream saved by GNU Radio:
#       AO40 FEC Deframer → PDU to Tagged Stream → File Sink
#
#   Output is a parsed structure per frame and an optional CSV file.
#
# Revision History:
#   v1.0  (2025-04-07) – Initial implementation for senior project documentation.
# =============================================================================

from dataclasses import dataclass
from typing import Dict, List, Tuple
import numpy as np
import csv
import sys
from pathlib import Path


# =============================================================================
# Symbolic Constants
# =============================================================================

FRAME_LEN = 256          # Length of a FUNcube downlink frame in bytes
RT_LEN_BYTES = 55        # Length of Real-Time telemetry block in bytes
RT_LEN_BITS = 440        # Length of Real-Time telemetry block in bits

# Satellite ID codes from FUNcube specification
SAT_ID_MAP = {
    0b00: "FUNcube-1 Engineering Model",
    0b01: "FUNcube-2 / UKube-1",
    0b10: "FUNcube-1 Flight Model (AO-73)",
    0b11: "Extended protocol",
}

# Frame type lookup table, following the published frame schedule
FRAME_TYPE_SCHEDULE: Dict[int, Tuple[str, str]] = {}
for i in range(1, 13):
    FRAME_TYPE_SCHEDULE[i] = ("WO", f"WO{i}")     # Whole Orbit
FRAME_TYPE_SCHEDULE.update({
    13: ("HR", "HR1"), 17: ("HR", "HR2"), 21: ("HR", "HR3"),
    14: ("FM", "FM1"), 15: ("FM", "FM2"), 16: ("FM", "FM3"),
    18: ("FM", "FM4"), 19: ("FM", "FM5"), 20: ("FM", "FM6"),
    22: ("FM", "FM7"), 23: ("FM", "FM8"), 24: ("FM", "FM9"),
})

# Scaling table
RT_SCALE = {
    "eps_photo_v1": 0.0006103515625,
    "eps_photo_v2": 0.0006103515625,
    "eps_photo_v3": 0.0006103515625,
    "eps_battery_voltage": 0.0006103515625,

    "eps_total_photo_current": 0.24414,
    "eps_total_system_current": 0.24414,
    "pa_board_current": 0.24414,
}




# =============================================================================
# Real-Time Telemetry Layout (55 bytes = 440 bits)
# =============================================================================
# Each tuple is: (field_name, number_of_bits)
RT_LAYOUT: List[Tuple[str, int]] = [
    # EPS Section --------------------------------------------------------------
    ("eps_photo_v1", 16), ("eps_photo_v2", 16), ("eps_photo_v3", 16),
    ("eps_total_photo_current", 16), ("eps_battery_voltage", 16),
    ("eps_total_system_current", 16), ("eps_reboot_count", 16),
    ("eps_software_errors", 16),
    ("eps_boost_temp_1", 8), ("eps_boost_temp_2", 8), ("eps_boost_temp_3", 8),
    ("eps_battery_temp", 8), ("eps_latchup_5v1", 8),
    ("eps_latchup_3v3_1", 8), ("eps_reset_cause", 8),
    ("eps_ppt_mode", 8),

    # BOB Section --------------------------------------------------------------
    ("bob_sun_sensor_xp", 10), ("bob_sun_sensor_yp", 10),
    ("bob_sun_sensor_zp", 10), ("bob_panel_temp_xp", 10),
    ("bob_panel_temp_xm", 10), ("bob_panel_temp_yp", 10),
    ("bob_panel_temp_ym", 10), ("bob_bus_3v3_voltage", 10),
    ("bob_bus_3v3_current", 10), ("bob_bus_5v_voltage", 10),

    # RF Section ---------------------------------------------------------------
    ("rf_rx_doppler", 8), ("rf_rx_rssi", 8), ("rf_temperature", 8),
    ("rf_rx_current", 8), ("rf_tx_current_3v3", 8), ("rf_tx_current_5v", 8),

    # PA Section ---------------------------------------------------------------
    ("pa_reverse_power", 8), ("pa_forward_power", 8),
    ("pa_board_temp", 8), ("pa_board_current", 8),

    # ANTS Section -------------------------------------------------------------
    ("ants_temp_0", 8), ("ants_temp_1", 8),
    ("ants_deploy_0", 1), ("ants_deploy_1", 1),
    ("ants_deploy_2", 1), ("ants_deploy_3", 1),

    # Software Section ---------------------------------------------------------
    ("sw_sequence_number", 24),
    ("sw_dtmf_cmd_count", 6), ("sw_dtmf_last_cmd", 5),
    ("sw_dtmf_success", 1), ("sw_data_valid_asib", 1),
    ("sw_data_valid_eps", 1), ("sw_data_valid_pa", 1),
    ("sw_data_valid_rf", 1), ("sw_data_valid_mse", 1),
    ("sw_data_valid_ants_b", 1), ("sw_data_valid_ants_a", 1),
    ("sw_in_eclipse_mode", 1), ("sw_in_safe_mode", 1),
    ("sw_hardware_abf", 1), ("sw_software_abf", 1),
    ("sw_deploy_wait_next_boot", 1),
]

# Ensure RT layout matches published specification
assert sum(bits for _, bits in RT_LAYOUT) == RT_LEN_BITS


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class FuncubeFrame:
    """
    Structure representing one parsed FUNcube-1 frame.

    Inputs:
        sat_id              : 2-bit ID field
        sat_name            : Satellite name derived from SAT_ID_MAP
        frame_type_value    : 6-bit frame type
        frame_class         : "WO", "HR", "FM", or "UNKNOWN"
        frame_label         : Human-readable label ("WO3", "FM1", etc.)
        rt                  : Dictionary of raw RT telemetry values
        payload             : 200-byte payload section

    Outputs:
        None (object container)
    """
    sat_id: int
    sat_name: str
    frame_type_value: int
    frame_class: str
    frame_label: str
    rt: Dict[str, int]
    payload: bytes

    def is_ao73(self) -> bool:
        """Return True if this frame corresponds to AO-73."""
        return self.sat_id == 0b10


# =============================================================================
# Utility Functions
# =============================================================================

def bytes_to_bits_msb_first(data: bytes) -> List[int]:
    """
    Convert a byte array into a bit list (MSB first).

    Inputs:
        data : bytes array

    Outputs:
        List[int] : each element is 0 or 1
    """
    bits = []
    for byte in data:
        for i in range(8):
            bits.append((byte >> (7 - i)) & 1)
    return bits


def take_bits(bits: List[int], nbits: int, pos: int) -> Tuple[int, int]:
    """
    Extract nbits starting from position 'pos' in an MSB-first bitstream.

    Inputs:
        bits : List[int] containing 0/1 values
        nbits : number of bits to extract
        pos : starting index

    Outputs:
        (value, new_pos)
    """
    value = 0
    for _ in range(nbits):
        value = (value << 1) | bits[pos]
        pos += 1
    return value, pos


# =============================================================================
# Parsing Functions
# =============================================================================

def parse_rt_telemetry(rt_bytes: bytes) -> Dict[str, int]:
    """
    Parse the 55-byte Real-Time telemetry block.

    Inputs:
        rt_bytes : 55-byte sequence

    Outputs:
        dict : field → integer value
    """
    if len(rt_bytes) != RT_LEN_BYTES:
        raise ValueError("Incorrect RT block length")

    bits = bytes_to_bits_msb_first(rt_bytes)
    if len(bits) != RT_LEN_BITS:
        raise RuntimeError("RT block bit length mismatch")

    pos = 0
    parsed = {}
    for field, nbits in RT_LAYOUT:
        val, pos = take_bits(bits, nbits, pos)
        if field in RT_SCALE:
            parsed[field] = val * RT_SCALE[field]
        else:
            parsed[field] = val


    return parsed


def parse_frame(frame_bytes: bytes) -> FuncubeFrame:
    """
    Parse a complete 256-byte FUNcube frame.

    Inputs:
        frame_bytes : raw 256-byte frame

    Outputs:
        FuncubeFrame : structured representation
    """
    if len(frame_bytes) != FRAME_LEN:
        raise ValueError("Frame size incorrect")

    header = frame_bytes[0]
    sat_id = (header >> 6) & 0b11
    frame_type_value = header & 0x3F

    sat_name = SAT_ID_MAP.get(sat_id, "Unknown Satellite")
    frame_class, frame_label = FRAME_TYPE_SCHEDULE.get(
        frame_type_value, ("UNKNOWN", "UNKNOWN")
    )

    rt_block = frame_bytes[1:1 + RT_LEN_BYTES]
    payload = frame_bytes[1 + RT_LEN_BYTES:]

    rt = parse_rt_telemetry(rt_block)

    return FuncubeFrame(
        sat_id=sat_id,
        sat_name=sat_name,
        frame_type_value=frame_type_value,
        frame_class=frame_class,
        frame_label=frame_label,
        rt=rt,
        payload=payload,
    )


def read_frames_from_file(path: str) -> List[FuncubeFrame]:
    """
    Read and parse all frames in a raw file.

    Inputs:
        path : path to raw byte file from GNU Radio

    Outputs:
        List[FuncubeFrame]
    """
    raw = np.fromfile(path, dtype=np.uint8)
    total_bytes = raw.size

    if total_bytes < FRAME_LEN:
        raise RuntimeError("File too small")

    n_frames = total_bytes // FRAME_LEN
    raw = raw[: n_frames * FRAME_LEN].reshape(n_frames, FRAME_LEN)

    parsed = []
    for i in range(n_frames):
        fb = bytes(raw[i].tolist())
        parsed.append(parse_frame(fb))

    return parsed


def write_frames_csv(frames: List[FuncubeFrame], csv_path: str) -> None:
    """
    Write RT telemetry to CSV (one row per frame).

    Inputs:
        frames : list of parsed frames
        csv_path : path to output CSV file

    Outputs:
        None
    """
    if not frames:
        return

    rt_fields = [name for name, _ in RT_LAYOUT]

    fieldnames = [
        "index", "sat_id", "sat_name",
        "frame_type_value", "frame_class", "frame_label",
    ] + rt_fields

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for idx, fr in enumerate(frames):
            row = {
                "index": idx,
                "sat_id": fr.sat_id,
                "sat_name": fr.sat_name,
                "frame_type_value": fr.frame_type_value,
                "frame_class": fr.frame_class,
                "frame_label": fr.frame_label,
            }
            for key in rt_fields:
                row[key] = fr.rt.get(key, None)
            writer.writerow(row)


# =============================================================================
# Main Entry Point
# =============================================================================

def main(argv: List[str]) -> None:
    """
    Command-line interface.

    Usage:
        python funcube_parser.py decoded_out.dat [frames.csv]

    Inputs:
        argv : command-line argument list

    Outputs:
        Prints summary & optionally writes CSV
    """
    if len(argv) < 2:
        print("Usage: funcube_parser.py decoded_out.dat [frames.csv]")
        return

    in_path = argv[1]
    frames = read_frames_from_file(in_path)

    print(f"Read {len(frames)} frames")

    # Quick breakdown by frame type
    counts = {}
    for fr in frames:
        counts[fr.frame_label] = counts.get(fr.frame_label, 0) + 1

    print("Frame type counts:")
    for k in sorted(counts.keys()):
        print(f"  {k:4s}: {counts[k]}")

    if frames:
        f0 = frames[0]
        print("\nFirst Frame Summary:")
        print(f"  Satellite: {f0.sat_name}")
        print(f"  Frame:     {f0.frame_label}")
        print(f"  Seq No:    {f0.rt.get('sw_sequence_number')}")

    if len(argv) >= 3:
        write_frames_csv(frames, argv[2])
        print(f"CSV written to {argv[2]}")


if __name__ == "__main__":
    main(sys.argv)
