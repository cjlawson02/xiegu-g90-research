"""FFT bench helpers — peak bin, display dB proxy, Hz/bin marker.

Does not close absolute dBm calibration (**O2** open). See ``docs/fft-bench.md``.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

from g90_codec import decode_body_frame, fft_fingerprint

# GSOC remote display affine (community / GSOC UI) — not proven absolute dBm.
DISPLAY_DB_SLOPE = 1.2
DISPLAY_DB_OFFSET = -110.0


def bins_u16(fft320: bytes) -> list[int]:
    return list(struct.unpack("<160H", fft320))


def signed_bin(u16: int) -> int:
    return u16 if u16 < 0x8000 else u16 - 0x10000


def display_db_proxy(u16: int) -> float:
    """GSOC-style display affine: ``−110 + 1.2 × (int16)bin``."""
    return DISPLAY_DB_OFFSET + DISPLAY_DB_SLOPE * signed_bin(u16)


def load_body_hex(*, path: Path | None = None, hex_str: str | None = None) -> bytes:
    if hex_str:
        raw = bytes.fromhex(hex_str.replace(" ", "").strip())
    elif path is not None:
        text = path.read_text().strip().splitlines()[0]
        raw = bytes.fromhex(text.replace(" ", ""))
    else:
        raise ValueError("need hex_str or path")
    if len(raw) != 372:
        raise ValueError(f"expected 372-byte body, got {len(raw)}")
    return raw


def analyze_body(
    body: bytes,
    *,
    dbm_true: float | None = None,
    label: str = "",
    expect_offset_hz: float | None = None,
    vfo_bin: int = 80,
) -> dict[str, Any]:
    dec = decode_body_frame(body)
    if not dec.crc_ok:
        raise ValueError("CRC fail")
    bins = bins_u16(dec.fft)
    peak_i = max(range(160), key=lambda i: bins[i])
    peak_u = bins[peak_i]
    st = dec.status
    out: dict[str, Any] = {
        "label": label,
        "crc_ok": dec.crc_ok,
        "s_meter": st.s_meter,
        "po": st.po,
        "fft_fp": fft_fingerprint(dec.fft),
        "peak_bin": peak_i,
        "peak_u16": peak_u,
        "peak_signed": signed_bin(peak_u),
        "db_proxy_y": display_db_proxy(peak_u),
        "dbm_true": dbm_true,
        "mean_u16": sum(bins) / 160.0,
        "bins": bins,
    }
    if expect_offset_hz is not None:
        dbin = peak_i - vfo_bin
        out["vfo_bin"] = vfo_bin
        out["delta_bin"] = dbin
        out["hz_per_bin"] = abs(expect_offset_hz / dbin) if dbin else float("nan")
        out["expect_offset_hz"] = expect_offset_hz
    return out
