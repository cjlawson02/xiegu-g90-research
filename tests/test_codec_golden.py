"""Golden-vector tests for the dual-codec G90 frame codec.

Run with: ``pytest tests/test_codec_golden.py -v``
"""

from __future__ import annotations

import struct
import unittest
from pathlib import Path

from g90_codec import (
    Profile,
    decode_body_frame,
    decode_filter,
    decode_head_frame,
    encode_body_frame,
    encode_filter,
    encode_head_frame,
    fft_fingerprint,
    header_diffs,
    mode_name,
    mode_value,
    parse_body_status,
    stm32_crc32,
    validate_fft_scale,
)


class TestCrc(unittest.TestCase):
    def test_empty_words(self) -> None:
        # STM32 CRC of zero words is the init value (no feed).
        self.assertEqual(stm32_crc32(b""), 0xFFFFFFFF)

    def test_known_word(self) -> None:
        # Single LE word 0x00000000 → swapped still zeros; CRC-32/MPEG-2 of 4×0.
        self.assertEqual(stm32_crc32(b"\x00\x00\x00\x00"), 0xC704DD7B)

    def test_head_coverage_length(self) -> None:
        frame = encode_head_frame(Profile.V178, mode_a="USB", volume=2)
        self.assertEqual(len(frame), 96)
        self.assertEqual(stm32_crc32(frame[:92]), struct.unpack_from("<I", frame, 92)[0])


class TestModes(unittest.TestCase):
    def test_178_map(self) -> None:
        self.assertEqual(mode_value("1.78", "USB"), 1)
        self.assertEqual(mode_value("1.78", "NFM"), 5)
        self.assertEqual(mode_name("1.78", 2), "CW")
        with self.assertRaises(ValueError):
            mode_value("1.78", "L-D")

    def test_179_insert_not_append(self) -> None:
        # The GSOC-break: USB moves from 1 → 2; NFM from 5 → 7.
        self.assertEqual(mode_value("1.79", "USB"), 2)
        self.assertEqual(mode_value("1.79", "L-D"), 1)
        self.assertEqual(mode_value("1.79", "U-D"), 3)
        self.assertEqual(mode_value("1.79", "NFM"), 7)
        self.assertEqual(mode_name("1.79", 5), "CWR")

    def test_cross_profile_mishap(self) -> None:
        """GSOC sends USB=1 into a 1.79 body → lands on L-D."""
        gsoc_usb = mode_value("1.78", "USB")
        self.assertEqual(gsoc_usb, 1)
        self.assertEqual(mode_name("1.79", gsoc_usb), "L-D")


class TestFilter(unittest.TestCase):
    def test_178_roundtrip(self) -> None:
        # high=125+100*25=2625, low=100+4*25=200 (zeroping default raws)
        hi, lo = encode_filter("1.78", 2625, 200)
        self.assertEqual((hi, lo), (100, 4))
        self.assertEqual(decode_filter("1.78", 100, 4), (2625, 200))

    def test_179_div50(self) -> None:
        hi, lo = encode_filter("1.79", 2700, 200)
        self.assertEqual((hi, lo), (53, 3))  # (2700-50)/50, (200-50)/50
        self.assertEqual(decode_filter("1.79", 53, 3), (2700, 200))

    def test_179_floor_and_clamp(self) -> None:
        self.assertEqual(encode_filter("1.79", 49, 0), (0, 0))
        self.assertEqual(encode_filter("1.79", 50 + 255 * 50, 50), (255, 0))


class TestFftScale(unittest.TestCase):
    def test_178_manual(self) -> None:
        self.assertEqual(validate_fft_scale("1.78", 10), 10)
        with self.assertRaises(ValueError):
            validate_fft_scale("1.78", 11)

    def test_179_auto_only(self) -> None:
        self.assertEqual(validate_fft_scale("1.79", 1), 1)
        with self.assertRaises(ValueError):
            validate_fft_scale("1.79", 2)


class TestHeadEncodeDecode(unittest.TestCase):
    def test_178_roundtrip(self) -> None:
        raw = encode_head_frame(
            "1.78",
            freq_a_hz=14_225_000,
            mode_a="CW",
            filter_high_hz=2725,
            filter_low_hz=200,
            fft_scale=5,
            volume=3,
        )
        dec = decode_head_frame("1.78", raw)
        self.assertTrue(dec.crc_ok)
        self.assertEqual(dec.vfo_a.mode_name, "CW")
        self.assertEqual(dec.vfo_a.mode, 2)
        self.assertEqual(dec.vfo_a.filter_high_hz, 2725)
        self.assertEqual(dec.vfo_a.filter_low_hz, 200)
        self.assertEqual(dec.vfo_a.fft_scale, 5)
        self.assertEqual(dec.volume, 3)
        # Re-encode from decoded fields
        raw2 = encode_head_frame(
            "1.78",
            freq_a_hz=dec.vfo_a.freq_hz,
            mode_a=dec.vfo_a.mode,
            filter_high_raw=dec.vfo_a.filter_high_raw,
            filter_low_raw=dec.vfo_a.filter_low_raw,
            fft_scale=dec.vfo_a.fft_scale,
            volume=dec.volume,
            freq_b_hz=dec.vfo_b.freq_hz,
            mode_b=dec.vfo_b.mode,
            filter_high_raw_b=dec.vfo_b.filter_high_raw,
            filter_low_raw_b=dec.vfo_b.filter_low_raw,
            fft_scale_b=dec.vfo_b.fft_scale,
        )
        self.assertEqual(raw, raw2)

    def test_179_roundtrip(self) -> None:
        raw = encode_head_frame(
            "1.79",
            freq_a_hz=14_074_000,
            mode_a="U-D",
            filter_high_hz=2700,
            filter_low_hz=200,
            fft_scale=1,
            volume=8,
        )
        dec = decode_head_frame("1.79", raw)
        self.assertTrue(dec.crc_ok)
        self.assertEqual(dec.vfo_a.mode_name, "U-D")
        self.assertEqual(dec.vfo_a.mode, 3)
        self.assertEqual(dec.vfo_a.filter_high_hz, 2700)
        self.assertEqual(dec.vfo_a.filter_low_hz, 200)
        self.assertEqual(dec.vfo_a.fft_scale, 1)

    def test_179_rejects_manual_fft(self) -> None:
        with self.assertRaises(ValueError):
            encode_head_frame("1.79", mode_a="USB", fft_scale=5)

    def test_fixture_files(self) -> None:
        fix = Path(__file__).parent / "fixtures"
        for name, profile in (
            ("head_178_usb.hex", "1.78"),
            ("head_179_usb.hex", "1.79"),
        ):
            path = fix / name
            self.assertTrue(path.exists(), f"missing {path}")
            raw = bytes.fromhex(path.read_text().strip())
            dec = decode_head_frame(profile, raw)
            self.assertTrue(dec.crc_ok, name)
            self.assertEqual(dec.vfo_a.mode_name, "USB")
            print(dec.summary())


class TestBodyMinimal(unittest.TestCase):
    def test_crc_and_fft(self) -> None:
        fft = bytes(range(256)) + bytes(64)
        raw = encode_body_frame(fft=fft)
        self.assertEqual(len(raw), 372)
        dec = decode_body_frame(raw)
        self.assertTrue(dec.crc_ok)
        self.assertEqual(dec.fft[: len(fft)], fft)
        self.assertEqual(dec.status.s_meter, 0)
        self.assertEqual(dec.status.atu, 0)
        print(dec.summary())

    def test_corrupt_crc_detected(self) -> None:
        """Spec T4: flipped CRC byte fails crc_ok on head and body."""
        head = encode_head_frame("1.81", mode_a="USB", freq_a_hz=14_225_000)
        bad_head = bytearray(head)
        bad_head[-1] ^= 0xFF
        self.assertTrue(decode_head_frame("1.81", bytes(head)).crc_ok)
        self.assertFalse(decode_head_frame("1.81", bytes(bad_head)).crc_ok)

        body = encode_body_frame()
        bad_body = bytearray(body)
        bad_body[-2] ^= 0xFF
        self.assertTrue(decode_body_frame(bytes(body)).crc_ok)
        self.assertFalse(decode_body_frame(bytes(bad_body)).crc_ok)

    def test_rx_only_guard_clears_ptt(self) -> None:
        from g90_codec import (
            HEAD_CTRL3_OPERATE,
            HEAD_PTT_BIT,
            HEAD_PTT_BYTE,
            apply_rx_only_guard,
            arm_head_ptt,
            head_frame_requests_tx,
            encode_head_frame,
            stm32_crc32,
        )

        safe = encode_head_frame("1.81", mode_a="USB")
        self.assertFalse(head_frame_requests_tx(safe))
        self.assertEqual(safe[HEAD_PTT_BYTE] & HEAD_PTT_BIT, 0)
        self.assertTrue(safe[38] & HEAD_CTRL3_OPERATE)  # operate/active ON

        # Stock-like key-down: PTT + operate (MainUnit gate).
        armed = bytearray(
            encode_head_frame(
                "1.81", mode_a="USB", extras={"allow_tx": True, "arm_ptt": True}
            )
        )
        self.assertTrue(head_frame_requests_tx(bytes(armed)))
        self.assertTrue(armed[HEAD_PTT_BYTE] & HEAD_PTT_BIT)
        self.assertTrue(armed[38] & HEAD_CTRL3_OPERATE)
        self.assertEqual(armed[36:40], bytes([0x80, 0x00, 0x03, 0x7D]))

        # allow_tx alone keeps operate; does not set PTT.
        idle_tx = encode_head_frame("1.81", extras={"allow_tx": True})
        self.assertFalse(head_frame_requests_tx(idle_tx))
        self.assertTrue(idle_tx[38] & HEAD_CTRL3_OPERATE)

        # Guard strips PTT/VOX/tune; keeps operate.
        armed[37] |= 0x20
        armed[38] |= 0x08  # vox
        apply_rx_only_guard(armed)
        self.assertFalse(head_frame_requests_tx(bytes(armed)))
        self.assertTrue(armed[38] & HEAD_CTRL3_OPERATE)

        # Manual arm helper + CRC.
        buf = bytearray(encode_head_frame("1.81"))
        arm_head_ptt(buf)
        struct.pack_into("<I", buf, 92, stm32_crc32(bytes(buf[:92])))
        self.assertTrue(head_frame_requests_tx(bytes(buf)))
        self.assertEqual(buf[36:39], bytes([0x80, 0x00, 0x03]))

    def test_head_controls_pack_and_overlay(self) -> None:
        from g90_codec import (
            HEAD_CTRL1_HEADPHONES,
            HEAD_CTRL1_NB_EN,
            HEAD_CTRL1_SPLIT,
            HEAD_CTRL3_AUDIO_LINE,
            HEAD_CTRL3_CW_DISP,
            HEAD_CTRL3_OPERATE,
            HEAD_CTRL6_VFO_B,
            head_frame_requests_tx,
            pack_af_gain_beep,
            pack_ctrl7,
            pack_ctrl8,
            pack_rclk_hz,
            pack_rit_hz,
            unpack_rclk_hz,
            unpack_rit_hz,
        )

        self.assertEqual(pack_af_gain_beep(15, beep_en=True), (0x78, 0x08))
        for hz in (-100, 0, 100, 500, -500):
            lo, hi = pack_rclk_hz(hz)
            self.assertEqual(unpack_rclk_hz(lo, hi), hz)
            lo, hi = pack_rit_hz(hz)
            self.assertEqual(unpack_rit_hz(lo, hi), hz)

        raw = encode_head_frame(
            "1.81",
            mode_a="USB",
            controls={
                "nb_en": True,
                "nb_level": 7,
                "nb_width": 2,
                "split_en": True,
                "vfo_b_en": True,
                "headphones": True,
                "audio_in_line_en": True,
                "cw_disp_en": True,
                "sql_level": 5,
                "rclk_hz": 100,
                "rit_hz": -50,
                "af_gain": 31,
                "beep_en": False,
                "cw_wpm": 25,
                "cw_qsk": False,
                "mic_gain": 15,
            },
        )
        self.assertFalse(head_frame_requests_tx(raw))
        self.assertTrue(raw[38] & HEAD_CTRL3_OPERATE)
        self.assertTrue(raw[36] & HEAD_CTRL1_NB_EN)
        self.assertTrue(raw[36] & HEAD_CTRL1_SPLIT)
        self.assertTrue(raw[36] & HEAD_CTRL1_HEADPHONES)
        self.assertTrue(raw[38] & HEAD_CTRL3_AUDIO_LINE)
        self.assertTrue(raw[38] & HEAD_CTRL3_CW_DISP)
        self.assertFalse(raw[38] & 0x01)  # cw_qsk off
        self.assertTrue(raw[54] & HEAD_CTRL6_VFO_B)
        self.assertEqual(raw[45], 5)
        self.assertEqual(raw[46], 0x72)  # level7 width2
        self.assertEqual(raw[49], 15)
        self.assertEqual(raw[60], 25)
        lo, hi = pack_rclk_hz(100)
        self.assertEqual((raw[38] >> 5) & 7, lo)
        self.assertEqual(raw[39], hi)
        rlo, rhi = pack_rit_hz(-50)
        self.assertEqual(raw[84], rlo)
        self.assertEqual(raw[85] & 3, rhi)
        self.assertEqual(raw[42], pack_af_gain_beep(31, beep_en=False)[0])
        self.assertEqual(raw[43], pack_af_gain_beep(31, beep_en=False)[1])

        shut = encode_head_frame(
            "1.81", mode_a="USB", controls={"shutdown_req": True, "panel_lock": True}
        )
        self.assertFalse(head_frame_requests_tx(shut))
        self.assertEqual(shut[37] & 0x80, 0x80)
        self.assertEqual(shut[36] & 0x01, 0x01)

        tune = encode_head_frame(
            "1.81",
            mode_a="USB",
            controls={"tuner_en": True},
            extras={"arm_atu_tune": True},
        )
        self.assertTrue(head_frame_requests_tx(tune))
        self.assertEqual(tune[37] & 0x20, 0x20)
        self.assertEqual(tune[36] & 0x80, 0)  # no PTT
        self.assertEqual(tune[36] & 0x20, 0x20)  # tuner_en
        self.assertTrue(tune[38] & HEAD_CTRL3_OPERATE)

        self.assertEqual(pack_ctrl7(cw_ratio_raw=5, cw_mode_b=True, cw_mlr=2), 0x56)
        self.assertEqual(pack_ctrl8(swr_threshold_raw=1, aux_out_vol=9, aux_in_vol=8), (0x01, 0x98))
        rest = encode_head_frame(
            "1.81",
            mode_a="CW",
            controls={
                "mem_en": True,
                "mem_ch": 12,
                "mic_compression": True,
                "cw_ratio_raw": 5,
                "cw_mode_b": True,
                "cw_mlr": 2,
                "swr_threshold_raw": 4,
                "aux_out_vol": 12,
                "aux_in_vol": 3,
                "band_stack_full": True,
            },
        )
        self.assertFalse(head_frame_requests_tx(rest))
        self.assertEqual(rest[36] & 0x48, 0x48)  # mem + mic_comp
        self.assertEqual(rest[53], 12)
        self.assertEqual(rest[61], 0x56)
        self.assertEqual(rest[82:84], bytes([0x04, 0xC3]))
        self.assertTrue(rest[43] & 0x04)  # band_stack
        vox = encode_head_frame("1.81", extras={"arm_vox": True})
        self.assertTrue(head_frame_requests_tx(vox))
        self.assertEqual(vox[38] & 0x08, 0x08)

    def test_status_from_synthetic_header(self) -> None:
        header = bytearray(48)
        header[0] = 0x9C  # signed -100
        header[1] = 10
        header[2] = 20
        header[3] = 132  # 13.2 V
        header[4] = 0x25  # ATU=1, cw bit5, bit2 clear
        header[5] = 40
        header[6] = 0x81
        header[12] = 0x00
        struct.pack_into("<I", header, 8, 0xC0000000)  # link + handshake
        st = parse_body_status(bytes(header))
        self.assertEqual(st.s_meter, -100)
        self.assertEqual(st.po, 10)
        self.assertEqual(st.swr, 20)
        self.assertAlmostEqual(st.voltage_v, 13.2)
        self.assertEqual(st.alc, 40)
        self.assertEqual(st.atu, 1)
        self.assertTrue(st.cw_flag)
        self.assertTrue(st.link_flag)
        self.assertTrue(st.rx_handshake)
        self.assertEqual(st.info6, 0x81)

    def test_header_diffs_and_fft_fp(self) -> None:
        a = bytes(48)
        b = bytearray(48)
        b[0] = 1
        b[20] = 0x53
        self.assertEqual(header_diffs(a, bytes(b)), [(0, 0, 1), (20, 0, 0x53)])
        fft = struct.pack("<HH", 3, 9) + bytes(316)
        self.assertEqual(fft_fingerprint(fft), (12, 9, 1))

    def test_live_181_fixture(self) -> None:
        """Live V1.81 body→head capture (headless pin-3 sniff, 2026-07-24)."""
        path = Path(__file__).parent / "fixtures" / "body_181_live.hex"
        self.assertTrue(path.exists(), f"missing {path}")
        raw = bytes.fromhex(path.read_text().strip())
        self.assertEqual(len(raw), 372)
        dec = decode_body_frame(raw)
        self.assertTrue(dec.crc_ok)
        # Headless v1.81 stream: byte6 observed 0x81 (not community 0x75)
        self.assertEqual(raw[6], 0x81)
        self.assertEqual(dec.status.info6, 0x81)
        self.assertAlmostEqual(dec.status.voltage_v, 13.5)  # 0x87
        print(dec.summary())

    def test_fake_head_181_fixtures(self) -> None:
        """Live fake-head TX + body RX (FT232 as head, V1.81, 2026-07-24)."""
        fix = Path(__file__).parent / "fixtures"
        head = bytes.fromhex((fix / "head_181_fake_usb.hex").read_text().strip())
        early = bytes.fromhex((fix / "body_181_fake_rx_early.hex").read_text().strip())
        partial = bytes.fromhex(
            (fix / "body_181_fake_rx_hdr_partial.hex").read_text().strip()
        )
        late = bytes.fromhex((fix / "body_181_fake_rx.hex").read_text().strip())

        hdec = decode_head_frame("1.81", head)
        self.assertTrue(hdec.crc_ok)
        self.assertEqual(hdec.vfo_a.mode_name, "USB")
        self.assertEqual(hdec.vfo_a.freq_hz, 14_225_000)
        self.assertEqual(hdec.vfo_a.filter_high_hz, 2700)

        for name, raw in (("early", early), ("partial", partial), ("late", late)):
            self.assertEqual(len(raw), 372, name)
            bdec = decode_body_frame(raw)
            self.assertTrue(bdec.crc_ok, name)
            self.assertEqual(raw[6], 0x81, name)
            self.assertEqual(bdec.status.info6, 0x81, name)
            self.assertEqual(raw[12], 0x00, name)
            # First fake-head session fixtures are the "S " fill only — no "E ".
            for i in range(13, 47):
                self.assertFalse(
                    raw[i] == 0x45 and raw[i + 1] == 0x20,
                    f"{name}: unexpected 0x45 0x20 at {i}",
                )
            print(name, bdec.summary())

        # Progressive hdr 13–47 fill under fake-head soak (no antenna):
        # early all-zero → partial [20]=0x53 → late [20..21]=0x53 0x20 ("S ").
        self.assertEqual(early[13:48], bytes(35))
        self.assertEqual(decode_body_frame(early).status.header_nz_13_47, 0)
        self.assertEqual(partial[20], 0x53)
        self.assertEqual(partial[21], 0x00)
        self.assertEqual(partial[13:20] + partial[21:48], bytes(34))
        self.assertEqual(decode_body_frame(partial).status.header_nz_13_47, 1)
        self.assertEqual(late[20:22], b"S ")
        self.assertEqual(late[13:20] + late[22:48], bytes(33))
        self.assertEqual(decode_body_frame(late).status.header_nz_13_47, 2)
        # MainUnit writer map: bytes 20–47 are a rolling C-string (NUL-terminated).
        self.assertEqual(decode_body_frame(early).status.cw_text, "")
        self.assertEqual(decode_body_frame(partial).status.cw_text, "S")
        self.assertEqual(decode_body_frame(late).status.cw_text, "S ")

        e, l = decode_body_frame(early), decode_body_frame(late)
        diffs = header_diffs(e.status.header, l.status.header)
        self.assertTrue(diffs)  # early vs late header moved
        self.assertEqual(l.status.s_meter, 0x64)  # unsigned view was 0x64; signed +100
        print("early→late header diffs", diffs[:8])

    def test_effect_diff_181_cw_E_march_fixtures(self) -> None:
        """effect_diff --extended --save (2026-07-24): lab 0x45 0x20 = ASCII 'E '."""
        fix = Path(__file__).parent / "fixtures"
        single = bytes.fromhex((fix / "body_181_effect_cw_E.hex").read_text().strip())
        mid = bytes.fromhex((fix / "body_181_effect_cw_E_mid.hex").read_text().strip())
        march = bytes.fromhex(
            (fix / "body_181_effect_cw_E_march.hex").read_text().strip()
        )
        for name, raw, text, nz in (
            ("E", single, "E ", 2),
            ("E_mid", mid, "E E E E ", 8),
            ("E_march", march, "E E E E E ", 10),
        ):
            self.assertEqual(len(raw), 372, name)
            dec = decode_body_frame(raw)
            self.assertTrue(dec.crc_ok, name)
            self.assertEqual(raw[6], 0x81, name)
            self.assertEqual(dec.status.cw_text, text, name)
            self.assertEqual(dec.status.header_nz_13_47, nz, name)
            self.assertEqual(raw[20 : 20 + len(text)], text.encode("ascii"), name)
            self.assertIn(b"\x45\x20", raw[13:48], name)
            print(name, dec.summary())
        self.assertTrue(mid[20:28].startswith(b"E E E E"))
        self.assertTrue(march[20:30].startswith(b"E E E E E"))
        self.assertEqual(march[13:20], bytes(7))  # 13–19 still empty


class TestProfileAlias(unittest.TestCase):
    def test_181_alias_maps_to_179_codec(self) -> None:
        # Until ladder drift (P0 #1c), 1.81 uses the post-break codec.
        self.assertIs(Profile.parse("1.81"), Profile.V179)


class TestZeropingOracle(unittest.TestCase):
    """Byte-identical to zeroping Construct builds (stdlib; no Construct dep).

    Provenance: xiegug90head.py + generate_head_data.py CRC recipe, captured
    2026-07-26. See docs/protocol/wire-format.md (community corroboration).
    Defaults: pad2b=0x55, unknown2=2, filter raws 100/4, USB, fft_scale=1.
    """

    # Wire hex from Construct; CRC field is the last 8 hex digits.
    _SAFE = {
        # schema-default ctrl3 operate/active ON (== our RX-only guard; zeroping
        # misnames this bit tx_disable=True)
        0: "55aa0000680ed900000100006404000000000001587b6d000000005564040000000000010000037d0000780801003300000a500202060000ffffffff0a01ff0fffffffffffffffff1f00003000000000003201980000ffff00000000dd33f76e",
        1: "55aa0000680ed900000100006404000000000001587b6d000000005564040000000000010000037d0000780801003300010a500202060000ffffffff0a01ff0fffffffffffffffff1f00003000000000003201980000ffff00000000806ba4ae",
        2: "55aa0000680ed900000100006404000000000001587b6d000000005564040000000000010000037d0000780801003300020a500202060000ffffffff0a01ff0fffffffffffffffff1f00003000000000003201980000ffff00000000d09e90ea",
        3: "55aa0000680ed900000100006404000000000001587b6d000000005564040000000000010000037d0000780801003300030a500202060000ffffffff0a01ff0fffffffffffffffff1f00003000000000003201980000ffff000000008dc6c32a",
        4: "55aa0000680ed900000100006404000000000001587b6d000000005564040000000000010000037d0000780801003300040a500202060000ffffffff0a01ff0fffffffffffffffff1f00003000000000003201980000ffff000000007074f962",
        5: "55aa0000680ed900000100006404000000000001587b6d000000005564040000000000010000037d0000780801003300050a500202060000ffffffff0a01ff0fffffffffffffffff1f00003000000000003201980000ffff000000002d2caaa2",
    }
    # Construct tx_disable=False samples — operate CLEAR. Historical oracle only;
    # MainUnit FUN_08009188 nulls TX when this bit is clear. Do not use live.
    _CONSTRUCT_CLEAR_OPERATE = {
        0: "55aa0000680ed900000100006404000000000001587b6d000000005564040000000000010000017d0000780801003300000a500202060000ffffffff0a01ff0fffffffffffffffff1f00003000000000003201980000ffff000000006ab860dc",
        2: "55aa0000680ed900000100006404000000000001587b6d000000005564040000000000010000017d0000780801003300020a500202060000ffffffff0a01ff0fffffffffffffffff1f00003000000000003201980000ffff0000000067150758",
        5: "55aa0000680ed900000100006404000000000001587b6d000000005564040000000000010000017d0000780801003300050a500202060000ffffffff0a01ff0fffffffffffffffff1f00003000000000003201980000ffff000000009aa73d10",
    }

    def _encode(self, volume: int, *, clear_operate: bool = False) -> bytes:
        extras: dict = {"pad2b": 0x55, "unknown2": 2}
        if clear_operate:
            extras["clear_operate"] = True
        return encode_head_frame(
            "1.78",
            volume=volume,
            filter_high_raw=100,
            filter_low_raw=4,
            fft_scale=1,
            extras=extras,
        )

    def test_safe_volume_ladder_matches_construct(self) -> None:
        for vol, hex_s in self._SAFE.items():
            expect = bytes.fromhex(hex_s)
            got = self._encode(vol, clear_operate=False)
            self.assertEqual(got, expect, f"safe vol={vol}")
            self.assertTrue(decode_head_frame("1.78", got).crc_ok)

    def test_construct_clear_operate_oracle(self) -> None:
        for vol, hex_s in self._CONSTRUCT_CLEAR_OPERATE.items():
            expect = bytes.fromhex(hex_s)
            got = self._encode(vol, clear_operate=True)
            self.assertEqual(got, expect, f"clear_operate vol={vol}")
            self.assertTrue(decode_head_frame("1.78", got).crc_ok)
            self.assertEqual(got[38] & 0x02, 0)

    def test_comment_checksums_are_wire_crc_bytes(self) -> None:
        """generate_head_data.py comments = wire CRC hex (BE display), not LE ints."""
        comments = {
            0: 0xDD33F76E,
            1: 0x806BA4AE,
            2: 0xD09E90EA,
            3: 0x8DC6C32A,
            4: 0x7074F962,
            5: 0x2D2CAAA2,
        }
        for vol, comment in comments.items():
            raw = bytes.fromhex(self._SAFE[vol])
            self.assertEqual(int.from_bytes(raw[92:96], "big"), comment, f"vol={vol}")
            # LE unpack differs — don't treat comment as struct '<I' value.
            self.assertNotEqual(struct.unpack_from("<I", raw, 92)[0], comment)


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
