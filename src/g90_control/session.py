"""Live FT232 head session — TX 96-byte frames, RX 372-byte body replies.

Requires pyserial. Stock head must be unplugged; see ``docs/bench-setup.md``.
"""

from __future__ import annotations

import glob
import time
from dataclasses import dataclass
from typing import Any

from g90_codec import TransmitArmedError, head_frame_requests_tx, head_frame_tx_arms

from .head import BODY_FRAME_LEN, HeadControl

def candidate_ports() -> list[str]:
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
    cu = [p for p in found if "/cu." in p]
    return cu or found


def resolve_port(explicit: str | None) -> str:
    if explicit:
        return explicit
    cands = candidate_ports()
    if not cands:
        raise SystemExit("no USB serial ports found; pass --port")
    if len(cands) > 1:
        listed = "\n  ".join(cands)
        raise SystemExit(
            "multiple USB serial ports — pass --port explicitly:\n  " + listed
        )
    return cands[0]


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


@dataclass
class ExchangeResult:
    tx: bytes
    rx: bytes | None
    body: Any  # DecodedBodyFrame | None


class LiveHeadSession:
    """Full-duplex fake-head over a serial port (115200 8N1, 3.3 V)."""

    def __init__(
        self,
        port: str,
        *,
        baud: int = 115200,
        profile: str = "1.81",
        timeout_s: float = 3.0,
        permit_arms: frozenset[str] | set[str] | None = None,
    ) -> None:
        try:
            import serial  # type: ignore
        except ImportError as e:
            raise SystemExit(
                "pyserial required: python3 -m pip install pyserial"
            ) from e

        self.port = port
        self.timeout_s = timeout_s
        self.ctrl = HeadControl(profile)
        # Lab default: refuse PTT/tune/VOX. Opt-in e.g. frozenset({"tune"}).
        self.permit_arms = frozenset(permit_arms or ())
        bad = self.permit_arms - frozenset({"ptt", "tune", "vox"})
        if bad:
            raise ValueError(f"unknown permit_arms: {sorted(bad)}")
        self._ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
        )
        self._ser.reset_input_buffer()
        self._ser.reset_output_buffer()

    def close(self) -> None:
        self._ser.close()

    def __enter__(self) -> LiveHeadSession:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def hunt_body(self, timeout_s: float | None = None) -> bytes | None:
        """Read until a CRC-valid 372-byte body frame or timeout."""
        t = self.timeout_s if timeout_s is None else timeout_s
        deadline = time.monotonic() + t
        buf = bytearray()
        while time.monotonic() < deadline:
            chunk = self._ser.read(512)
            if chunk:
                buf.extend(chunk)
                # Keep a sliding window; body frames are fixed 372 bytes.
                if len(buf) > BODY_FRAME_LEN * 4:
                    del buf[: len(buf) - BODY_FRAME_LEN * 2]
                for i in range(max(0, len(buf) - BODY_FRAME_LEN + 1)):
                    frame = bytes(buf[i : i + BODY_FRAME_LEN])
                    if self.ctrl.decode_body(frame).crc_ok:
                        return frame
            else:
                time.sleep(0.002)
        return None

    def exchange(self, tx: bytes | None = None, **encode_kw: Any) -> ExchangeResult:
        """TX one head frame (given or encoded from kwargs), then hunt a body reply.

        Refuses frames that request RF transmit / ATU-tune / VOX unless the
        matching name is in ``permit_arms`` (e.g. ``{"tune"}``). ``allow_tx`` /
        ``arm_ptt`` extras are always stripped on the live path; ``arm_atu_tune``
        is kept only when ``tune`` is permitted.
        """
        if tx is None:
            kw = dict(encode_kw)
            extras = kw.get("extras")
            if extras:
                extras = dict(extras)
                extras.pop("allow_tx", None)
                extras.pop("arm_ptt", None)
                extras.pop("ptt", None)
                if "tune" not in self.permit_arms:
                    extras.pop("allow_tune", None)
                    extras.pop("arm_atu_tune", None)
                if "vox" not in self.permit_arms:
                    extras.pop("allow_vox", None)
                    extras.pop("arm_vox", None)
                kw["extras"] = extras
            tx = self.ctrl.encode(**kw)
        arms = head_frame_tx_arms(tx)
        forbidden = arms - self.permit_arms
        if forbidden:
            raise TransmitArmedError(
                "refusing head frame with armed bits "
                f"{sorted(forbidden)} (permit_arms={sorted(self.permit_arms)}; "
                "byte36 PTT / byte37 tune / VOX)"
            )
        self._ser.write(tx)
        self._ser.flush()
        rx = self.hunt_body()
        body = self.ctrl.decode_body(rx) if rx is not None else None
        return ExchangeResult(tx=tx, rx=rx, body=body)
