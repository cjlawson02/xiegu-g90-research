#!/usr/bin/env python3
"""One-shot CI-V fixture capture for tests/fixtures/civ_live/."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from g90_control.civ import (  # noqa: E402
    CivClient,
    CivFrame,
    CivProtocolError,
    decode_att_reply,
    decode_mode_reply,
    decode_radio_id_reply,
    encode_read_att,
    encode_read_mode,
    encode_read_radio_id,
    encode_read_vfo_freq,
    encode_read_vfo_mode,
    encode_set_att,
    encode_set_mode,
    parse_frames,
)

OUT = ROOT / "tests" / "fixtures" / "civ_live"


def _hex(b: bytes) -> str:
    return b.hex()


def _radio_reply(rx: bytes, radio: int = 0x88) -> CivFrame:
    frames, _ = parse_frames(rx)
    for fr in frames:
        if fr.from_addr == radio:
            return fr
    raise CivProtocolError(f"no reply from radio 0x{radio:02X} in {rx.hex()}")


def _save(name: str, tx: bytes, rx: bytes) -> None:
    path = OUT / f"{name}.hex"
    path.write_text(f"tx={_hex(tx)}\nrx={_hex(rx)}\n", encoding="utf-8")
    print(f"saved {path.name}  rx={len(rx)} B")


def _exchange(ser, tx: bytes, timeout: float = 1.5) -> bytes:
    ser._ser.reset_input_buffer()
    ser.send_raw(tx)
    deadline = time.monotonic() + timeout
    buf = bytearray()
    while time.monotonic() < deadline:
        chunk = ser._ser.read(256)
        if chunk:
            buf.extend(chunk)
        if b"\xfd" in buf.lower():
            time.sleep(0.05)
            buf.extend(ser._ser.read(256))
            break
        time.sleep(0.01)
    return bytes(buf)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--port",
        "-p",
        required=True,
        help="USB serial port for CI-V (DispUnit User jack @ 19200)",
    )
    args = ap.parse_args(argv)

    OUT.mkdir(parents=True, exist_ok=True)
    with CivClient(args.port, timeout_s=2.0) as civ:
        tx = encode_read_radio_id()
        rx = _exchange(civ, tx)
        _save("radio_id_1d_19_addr88", tx, rx)
        rid = decode_radio_id_reply(_radio_reply(rx))
        print(f"  radio_id={rid}")

        tx = encode_read_att()
        rx = _exchange(civ, tx)
        _save("cq2_att_off", tx, rx)
        att_off = decode_att_reply(_radio_reply(rx))
        print(f"  att_off={att_off} payload=0x{_radio_reply(rx).payload[0]:02X}")

        _exchange(civ, encode_set_att(True))
        tx = encode_read_att()
        rx = _exchange(civ, tx)
        _save("cq2_att_on", tx, rx)
        att_on = decode_att_reply(_radio_reply(rx))
        print(f"  att_on={att_on} payload=0x{_radio_reply(rx).payload[0]:02X}")

        _exchange(civ, encode_set_att(False))

        tx_set = encode_set_mode("USB", 0x02)
        rx_set = _exchange(civ, tx_set)
        _save("cq1_set_mode_usb_filter02", tx_set, rx_set)
        tx = encode_read_mode()
        rx = _exchange(civ, tx)
        _save("cq1_read_mode_after_filter02", tx, rx)
        mode, filt = decode_mode_reply(_radio_reply(rx))
        print(f"  after set filt=0x02, read mode=0x{mode:02X} filt=0x{filt:02X}")

        tx = encode_read_vfo_freq(0x01)
        rx = _exchange(civ, tx, timeout=2.0)
        _save("cq4_vfo_freq_which01", tx, rx)
        print(f"  cq4 freq which=01 rx_len={len(rx)}")

        tx = encode_read_vfo_mode(0x01)
        rx = _exchange(civ, tx, timeout=2.0)
        _save("cq4_vfo_mode_which01", tx, rx)
        print(f"  cq4 mode which=01 rx_len={len(rx)}")

    with CivClient(args.port, radio_addr=0x70, timeout_s=2.0) as civ70:
        tx = encode_read_mode(radio=0x70)
        rx = _exchange(civ70, tx)
        _save("addr70_read_mode", tx, rx)
        if rx:
            mode, filt = decode_mode_reply(_radio_reply(rx, radio=0x70))
            print(f"  addr70 mode=0x{mode:02X} filt=0x{filt:02X}")
        else:
            print("  addr70: no reply")

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
