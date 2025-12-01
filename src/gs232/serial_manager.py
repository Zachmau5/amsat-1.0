#!/usr/bin/env python3
"""
GS-232B Serial Manager.

Purpose
-------
Wrap pyserial to provide:
  - Auto-open across a list of candidate ports.
  - Basic retries on write failures.
  - Convenience methods for GS-232B commands.

Role in System
--------------
- Used by main_gs232b.py as the hardware-facing layer.
- Uses commands.format_move() for W-commands.
"""

from __future__ import annotations

import time
import serial
from serial import Serial, SerialException
from gs232.commands import format_move


class SerialManager:
    """
    Simple serial manager for GS-232B.

    Methods
    -------
    ensure_open()
        Ensure that the port is open (reopen if needed).
    write_cmd(cmd_str, expect_reply=False, retries=1)
        Send a raw command string plus CR and optionally read a line.
    send_move(az_deg, el_deg, echo_c2=False)
        Format and send a W-command; optionally read C2.
    query_c2()
        Issue C2 and return the reply.
    stop()
        Issue S (all stop).
    """

    def __init__(
        self,
        candidates=("/dev/ttyUSB0", "/dev/ttyUSB1", "COM3", "COM4"),
        baud=9600,
        timeout=1.0,
    ):
        self.candidates = list(candidates)
        self.baud = baud
        self.timeout = timeout
        self.ser: Serial | None = None
        self.last_open_port: str | None = None
        self._open_any()

    def _open_any(self) -> bool:
        """Try last-good port first, then the remaining candidates."""
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

    def ensure_open(self) -> bool:
        """Return True if the port is open or can be re-opened."""
        if self.ser and self.ser.is_open:
            return True
        return self._open_any()

    def close(self) -> None:
        """Best-effort close of the serial port."""
        try:
            if self.ser:
                self.ser.close()
                print("[SER] Closed port")
        except Exception:
            pass
        self.ser = None

    def _write_raw(self, bcmd: bytes) -> None:
        if not self.ensure_open():
            raise SerialException("Port not open")
        self.ser.write(bcmd)
        self.ser.flush()

    def _readline(self) -> str:
        if not self.ensure_open():
            return ""
        try:
            b = self.ser.read_until(b"\r")
            if not b:
                return ""
            return b.rstrip(b"\r").decode("ascii", errors="ignore").strip()
        except Exception:
            return ""

    def write_cmd(self, cmd_str: str, expect_reply=False, retries=1) -> str:
        """
        Send 'cmd_str\\r' to the controller and optionally read one reply line.
        Retries once on SerialException by reopening the port.
        """
        cmd_str = cmd_str.rstrip()
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

    def send_move(self, az_deg: float, el_deg: float, echo_c2: bool = False):
        """
        Format and send a W-command.

        Returns
        -------
        (cmd_str, reply_str)
            reply_str is either empty or the C2 echo if echo_c2=True.
        """
        cmd = format_move(az_deg, el_deg)
        reply = ""
        try:
            _ = self.write_cmd(cmd, expect_reply=False, retries=1)
            if echo_c2:
                reply = self.write_cmd("C2", expect_reply=True, retries=1)
        except Exception:
            self.close()
            self.ensure_open()
        return cmd, reply

    def query_c2(self) -> str:
        """Send C2 and return the reply."""
        return self.write_cmd("C2", expect_reply=True, retries=1)

    def stop(self):
        """Send 'S' (all stop) to the GS-232B."""
        return self.write_cmd("S", expect_reply=False)
