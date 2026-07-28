"""Lean H1 passive sniff: FT232 RX → decode with g90-codec profile 1.81.

Head→body (default): hunt ``55 AA``, take 94 more bytes → 96-byte frame.
Body→head (``--body``): 372-byte windows; community resync on byte6 ``0x75``,
then CRC check.

RX-only — do not connect FT232 TXD while the stock head is attached.
See ``docs/bench-setup.md``.
"""

from __future__ import annotations

import argparse
import glob
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


from g90_codec import (  # noqa: E402
    BODY_FRAME_LEN,
    HEAD_FRAME_LEN,
    Profile,
    decode_body_frame,
    decode_head_frame,
)

_SYNC0 = 0x55
_SYNC1 = 0xAA
_BODY_HINT = 0x75  # community R3 — unverified semantics


def _candidate_ports() -> list[str]:
    patterns = (
        "/dev/cu.usbserial*",
        "/dev/cu.usbmodem*",
        "/dev/tty.usbserial*",
        "/dev/ttyUSB*",
        "/dev/ttyACM*",
    )
    found: list[str] = []
    for pat in patterns:
        found.extend(sorted(glob.glob(pat)))
    # Prefer cu.* over tty.* duplicates on macOS
    cu = [p for p in found if "/cu." in p]
    return cu or found


def _resolve_port(explicit: str | None) -> str:
    if explicit:
        return explicit
    cands = _candidate_ports()
    if not cands:
        raise SystemExit(
            "no USB serial ports found; plug the FT232 and pass --port /dev/cu.usbserial-…"
        )
    if len(cands) > 1:
        listed = "\n  ".join(cands)
        raise SystemExit(
            "multiple USB serial ports — pass --port explicitly (avoid guessing "
            f"a leftover flasher):\n  {listed}"
        )
    return cands[0]


def _save_frame(save_dir: Path, prefix: str, frame: bytes, n: int) -> Path:
    save_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = save_dir / f"{prefix}_{ts}_{n:03d}"
    bin_path = base.with_suffix(".bin")
    hex_path = base.with_suffix(".hex")
    bin_path.write_bytes(frame)
    hex_path.write_text(frame.hex() + "\n", encoding="utf-8")
    return hex_path


def _read_exact(ser, n: int, timeout_s: float) -> bytes | None:
    deadline = time.monotonic() + timeout_s
    buf = bytearray()
    while len(buf) < n:
        if time.monotonic() > deadline:
            return None
        chunk = ser.read(n - len(buf))
        if chunk:
            buf.extend(chunk)
        else:
            time.sleep(0.001)
    return bytes(buf)


def _hunt_head_frame(ser, timeout_s: float) -> bytes | None:
    """Hunt 55 AA then read 94 more bytes."""
    deadline = time.monotonic() + timeout_s
    prev: int | None = None
    while time.monotonic() < deadline:
        b = ser.read(1)
        if not b:
            continue
        cur = b[0]
        if prev == _SYNC0 and cur == _SYNC1:
            rest = _read_exact(ser, HEAD_FRAME_LEN - 2, timeout_s=2.0)
            if rest is None:
                return None
            return bytes([_SYNC0, _SYNC1]) + rest
        prev = cur
    return None


def _hunt_body_frame(ser, timeout_s: float) -> tuple[bytes | None, int]:
    """Slide for a CRC-valid 372-byte window until timeout.

    Returns ``(frame_or_None, raw_bytes_seen)``. Checks the deadline while
    sliding so a busy non-matching stream cannot hang past ``timeout_s``.
    """
    deadline = time.monotonic() + timeout_s
    buf = bytearray()
    raw = 0
    while time.monotonic() < deadline:
        chunk = ser.read(4096)
        if chunk:
            buf.extend(chunk)
            raw += len(chunk)
        else:
            time.sleep(0.001)
            continue
        while len(buf) >= BODY_FRAME_LEN and time.monotonic() < deadline:
            window = bytes(buf[:BODY_FRAME_LEN])
            if decode_body_frame(window).crc_ok:
                return window, raw
            buf.pop(0)
        # Cap buffer so a long non-matching stream cannot grow without bound
        if len(buf) > BODY_FRAME_LEN * 4:
            del buf[: len(buf) - BODY_FRAME_LEN * 2]
    return None, raw


def _print_head(profile: Profile, frame: bytes, n: int) -> bool:
    dec = decode_head_frame(profile, frame)
    a = dec.vfo_a
    print(
        f"[{n}] HEAD {dec.summary()} "
        f"freq_a={a.freq_hz} freq_b={dec.vfo_b.freq_hz}"
    )
    print(f"     hex={frame.hex()}")
    return dec.crc_ok


def _print_body(frame: bytes, n: int) -> bool:
    dec = decode_body_frame(frame)
    hdr13_47 = frame[13:48]
    nonzero = sum(1 for b in hdr13_47 if b != 0)
    print(
        f"[{n}] {dec.summary()} "
        f"byte6=0x{frame[6]:02x} byte12=0x{frame[12]:02x} "
        f"hdr13-47_nonzero={nonzero}/35"
    )
    print(f"     hdr[0:48]={frame[:48].hex()}")
    return dec.crc_ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--port",
        "-p",
        default=None,
        help="serial device (required if more than one USB-serial is present)",
    )
    ap.add_argument(
        "--profile",
        default="1.81",
        help="payload profile for head decode (default: 1.81 → 1.79 codec)",
    )
    ap.add_argument(
        "--body",
        action="store_true",
        help="sniff body→head 372-byte frames (RXD on DE-9 pin 3)",
    )
    ap.add_argument(
        "--count",
        "-n",
        type=int,
        default=5,
        help="frames to capture then exit (default: 5; 0 = until Ctrl-C)",
    )
    ap.add_argument(
        "--save",
        metavar="DIR",
        help="write timestamped .bin/.hex frames into DIR",
    )
    ap.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="baud rate (default: 115200)",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="seconds to wait for each frame before giving up (default: 5)",
    )
    ap.add_argument(
        "--list-ports",
        action="store_true",
        help="list candidate USB-serial ports and exit",
    )
    args = ap.parse_args(argv)

    if args.list_ports:
        cands = _candidate_ports()
        if not cands:
            print("no USB serial ports found")
            return 1
        for p in cands:
            print(p)
        return 0

    try:
        import serial  # type: ignore
    except ImportError as e:
        raise SystemExit(
            "pyserial required for live sniff: python3 -m pip install pyserial"
        ) from e

    port = _resolve_port(args.port)
    profile = Profile.parse(args.profile)
    save_dir = Path(args.save) if args.save else None
    prefix = "body" if args.body else "head"
    target = args.count if args.count > 0 else None

    print(
        f"opening {port} @ {args.baud} 8N1 "
        f"{'body→head 372' if args.body else 'head→body 96'} "
        f"profile={profile.value}",
        file=sys.stderr,
    )
    print(
        "RX-only: confirm FT232 TXD is disconnected from the radio",
        file=sys.stderr,
    )

    ser = serial.Serial(
        port=port,
        baudrate=args.baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.05,
    )
    ok = 0
    bad = 0
    n = 0
    raw_total = 0
    try:
        while target is None or n < target:
            if args.body:
                frame, raw = _hunt_body_frame(ser, args.timeout)
                raw_total += raw
            else:
                frame = _hunt_head_frame(ser, args.timeout)
            if frame is None:
                print(
                    f"timeout waiting for frame after {n} captured "
                    f"(raw_bytes={raw_total}; check wiring: GND→pin5, "
                    f"RXD→pin{'3' if args.body else '2'})",
                    file=sys.stderr,
                )
                return 2
            n += 1
            good = (
                _print_body(frame, n) if args.body else _print_head(profile, frame, n)
            )
            if good:
                ok += 1
            else:
                bad += 1
            if save_dir is not None:
                path = _save_frame(save_dir, prefix, frame, n)
                print(f"     saved {path}")
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
    finally:
        ser.close()

    print(
        f"done: {ok} crc_ok, {bad} crc_fail, {n} total, raw_bytes={raw_total}",
        file=sys.stderr,
    )
    return 0 if bad == 0 and ok > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
