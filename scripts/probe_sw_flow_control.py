#!/usr/bin/env python3
"""Verify that a Thread RCP dongle honors XON/XOFF software flow control."""

from __future__ import annotations

import argparse
import fcntl
import os
import select
import struct
import sys
import termios
import time

# Read-only Spinel PROP_VALUE_GET(HWADDR) with double leading 0x7e to guarantee HDLC resync.
_GET_HWADDR = b"\x7e\x7e\x83\x02\x08\xbc\x9a\x7e"
_CONTROL_BYTE = b"\x42"
_XON = b"\x11"
_XOFF = b"\x13"


def _read_all(fd: int, timeout: float) -> bytes:
    out = bytearray()
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            break
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        out.extend(chunk)
        if out.count(0x7E) >= 2:
            deadline = min(deadline, time.monotonic() + 0.05)
    return bytes(out)


def probe(device: str, baudrate: int) -> int:
    """Run baseline, 0x42 control, XOFF pause, and XON resume checks against an RCP."""
    speed = getattr(termios, f"B{baudrate}", None)
    if speed is None:
        print(f"ERROR: Unsupported baudrate {baudrate}", file=sys.stderr)
        return 1

    fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        attrs = termios.tcgetattr(fd)
        attrs[0] = 0
        attrs[1] = 0
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[3] = 0
        attrs[4] = speed
        attrs[5] = speed
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)

        try:
            flags = struct.pack("I", termios.TIOCM_DTR | termios.TIOCM_RTS)
            fcntl.ioctl(fd, termios.TIOCMBIC, flags)
        except OSError:
            pass

        termios.tcflush(fd, termios.TCIOFLUSH)
        os.write(fd, _XON)
        time.sleep(0.05)
        _read_all(fd, 0.1)

        os.write(fd, _GET_HWADDR)
        baseline = _read_all(fd, 1.0)
        print(f"1. Baseline GET_HWADDR ({len(baseline)} bytes): {baseline.hex()}")
        if not baseline:
            print("FAIL: No response to baseline Spinel query (check device and baudrate).")
            return 1

        os.write(fd, _CONTROL_BYTE)
        time.sleep(0.05)
        os.write(fd, _GET_HWADDR)
        control = _read_all(fd, 1.0)
        print(f"2. Control 0x42 + GET_HWADDR ({len(control)} bytes): {control.hex()}")
        if not control:
            print("FAIL: Control query after 0x42 produced no response.")
            return 1

        os.write(fd, _XOFF)
        time.sleep(0.05)
        os.write(fd, _GET_HWADDR)
        while_xoff = _read_all(fd, 0.5)
        print(f"3. After XOFF (0x13) + GET_HWADDR ({len(while_xoff)} bytes): {while_xoff.hex()}")

        os.write(fd, _XON)
        after_xon = _read_all(fd, 1.0)
        print(f"4. After XON (0x11) ({len(after_xon)} bytes): {after_xon.hex()}")

        if while_xoff:
            print("FAIL: Dongle ignored XOFF (0x13); software flow control is not enabled on the RCP.")
            return 2
        if after_xon:
            print("PASS: Dongle paused TX on XOFF (0x13) and flushed the held reply on XON (0x11).")
            return 0

        os.write(fd, _GET_HWADDR)
        followup = _read_all(fd, 1.0)
        print(f"5. Follow-up GET_HWADDR after XON ({len(followup)} bytes): {followup.hex()}")
        if followup:
            print("PASS: Dongle paused TX on XOFF (0x13) and resumed cleanly after XON (0x11).")
            return 0

        print("FAIL: Dongle stayed silent after XON (0x11).")
        return 1
    finally:
        try:
            os.write(fd, _XON)
        except OSError:
            pass
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "device",
        nargs="?",
        default="/dev/ttyACM0",
        help="Serial device path (default: /dev/ttyACM0)",
    )
    parser.add_argument(
        "baudrate",
        nargs="?",
        type=int,
        default=460800,
        help="Serial baudrate (default: 460800)",
    )
    args = parser.parse_args(argv)
    return probe(args.device, args.baudrate)


if __name__ == "__main__":
    raise SystemExit(main())
