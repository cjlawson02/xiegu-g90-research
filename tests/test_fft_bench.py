"""Tests for FFT bench helpers (offline)."""

from __future__ import annotations

import struct
import unittest
from pathlib import Path

from g90_codec import encode_body_frame
from g90_control.fft_bench import analyze_body, display_db_proxy, load_body_hex, signed_bin

_FIX = Path(__file__).resolve().parent / "fixtures" / "fft_bench"


class TestFftBenchHelpers(unittest.TestCase):
    def test_signed_bin_and_proxy(self) -> None:
        self.assertEqual(signed_bin(100), 100)
        self.assertEqual(signed_bin(0x8001), -32767)
        self.assertAlmostEqual(display_db_proxy(100), -110.0 + 1.2 * 100)

    def test_analyze_body_peak_at_vfo_bin(self) -> None:
        fft = bytearray(320)
        struct.pack_into("<H", fft, 80 * 2, 500)
        raw = encode_body_frame(fft=bytes(fft))
        r = analyze_body(raw, expect_offset_hz=10_000.0, vfo_bin=80)
        self.assertTrue(r["crc_ok"])
        self.assertEqual(r["peak_bin"], 80)
        self.assertEqual(r["delta_bin"], 0)

    def test_analyze_body_offset_bin(self) -> None:
        fft = bytearray(320)
        struct.pack_into("<H", fft, 113 * 2, 800)  # +33 bins from 80
        raw = encode_body_frame(fft=bytes(fft))
        r = analyze_body(raw, expect_offset_hz=10_000.0, vfo_bin=80)
        self.assertEqual(r["peak_bin"], 113)
        self.assertEqual(r["delta_bin"], 33)
        self.assertAlmostEqual(r["hz_per_bin"], 10_000 / 33, places=1)


class TestFftBenchFixtures(unittest.TestCase):
    """Tier B — lab captures 2026-07-31 (fake-head v1.81, FT8 7.073 MHz)."""

    def test_hz_per_bin_vfo_peak_near_center(self) -> None:
        raw = load_body_hex(path=_FIX / "body_181_fft_vfo_7073mhz_20260731.hex")
        r = analyze_body(raw)
        self.assertTrue(r["crc_ok"])
        self.assertEqual(r["peak_bin"], 80)
        self.assertGreater(r["peak_u16"], 0)

    def test_hz_per_bin_plus10k(self) -> None:
        raw = load_body_hex(path=_FIX / "body_181_fft_plus10k_7073mhz_20260731.hex")
        r = analyze_body(raw, expect_offset_hz=10_000.0, vfo_bin=80)
        self.assertEqual(r["peak_bin"], 47)
        self.assertAlmostEqual(r["hz_per_bin"], 303.0, delta=15.0)

    def test_hz_per_bin_minus10k(self) -> None:
        raw = load_body_hex(path=_FIX / "body_181_fft_minus10k_7073mhz_20260731.hex")
        r = analyze_body(raw, expect_offset_hz=-10_000.0, vfo_bin=80)
        self.assertEqual(r["peak_bin"], 114)
        self.assertAlmostEqual(r["hz_per_bin"], 294.0, delta=15.0)

    def test_att_lowers_peak(self) -> None:
        off = analyze_body(
            load_body_hex(path=_FIX / "body_181_fft_att_off_20260731.hex")
        )
        on = analyze_body(
            load_body_hex(path=_FIX / "body_181_fft_att_on_20260731.hex")
        )
        self.assertGreater(off["peak_u16"], on["peak_u16"])


if __name__ == "__main__":
    unittest.main()
