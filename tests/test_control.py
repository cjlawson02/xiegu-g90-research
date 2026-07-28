"""Control-library tests for track-latest / v1.81.

Normative freeze gate for ``docs/protocol/control-spec.md``.
Run with: ``pytest tests/test_control.py -v``
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from g90_codec import Profile, decode_head_frame, encode_head_frame, mode_name, mode_value
from g90_control import (
    TARGET_CODEC,
    TARGET_PROFILE,
    BLOCKED_OPCODES,
    CivTxBlockedError,
    HeadControl,
    bcd_to_freq,
    bcd2_to_int,
    build_frame,
    build_head_frame,
    decode_head,
    encode_read_freq,
    encode_read_meter,
    encode_read_mode,
    encode_set_freq,
    encode_set_mode,
    encode_read_att,
    encode_set_att,
    encode_read_ext,
    encode_set_ext,
    encode_read_radio_id,
    freq_to_bcd,
    int_to_bcd2,
    parse_frames,
)
from g90_control.civ import (
    EXT_PRE,
    EXT_AGC,
    CivFrame,
    decode_freq_reply,
    decode_meter_reply,
    decode_mode_reply,
    mode_value as civ_mode_value,
)


class TestAliasAndTarget(unittest.TestCase):
    def test_181_aliases_179(self) -> None:
        """Spec P2/P3: 1.81 aliases 1.79; product default → V179."""
        self.assertIs(Profile.parse("1.81"), Profile.V179)
        self.assertIs(Profile.parse(TARGET_PROFILE), TARGET_CODEC)
        self.assertIs(HeadControl().codec_profile, Profile.V179)
        self.assertIs(HeadControl("1.81").codec_profile, Profile.V179)
        self.assertIs(HeadControl("1.79").codec_profile, Profile.V179)

    def test_classic_gsoc_mishap(self) -> None:
        """Spec P5: encode USB under 1.78, decode as 1.79 → L-D (CRC OK).

        Documents why track-latest cannot reuse the frozen-GSOC mode map:
        USB is wire value 1 on ≤1.78 and 2 on ≥1.79; value 1 is L-D post-break.
        """
        raw = encode_head_frame(
            "1.78",
            mode_a="USB",
            filter_high_hz=2725,
            filter_low_hz=200,
            fft_scale=1,
        )
        # Intentional wrong profile — the frozen-head / modern-body mismatch.
        wrong = decode_head_frame("1.79", raw)
        self.assertTrue(wrong.crc_ok)  # transport is identical
        self.assertEqual(mode_value("1.78", "USB"), 1)
        self.assertEqual(wrong.vfo_a.mode, 1)
        self.assertEqual(wrong.vfo_a.mode_name, "L-D")
        self.assertEqual(mode_name("1.79", 1), "L-D")
        # Correct decode under the encode profile still says USB.
        right = decode_head_frame("1.78", raw)
        self.assertEqual(right.vfo_a.mode_name, "USB")
        print(
            "OK  classic mishap: 1.78 USB wire→1.79 decode = "
            f"{wrong.vfo_a.mode_name} (not USB) — track-latest needs 1.79 map"
        )


class TestControlRoundTrip(unittest.TestCase):
    def test_179_roundtrip(self) -> None:
        """Spec H6: 1.79 encode→decode preserves mode/freq/filter/fft_scale."""
        ctrl = HeadControl("1.79")
        raw = ctrl.encode(
            mode="USB",
            freq_hz=14_074_000,
            filter_high_hz=2700,
            filter_low_hz=200,
            fft_scale=1,
        )
        dec = ctrl.decode(raw)
        self.assertTrue(dec.crc_ok)
        self.assertEqual(dec.vfo_a.mode_name, "USB")
        self.assertEqual(dec.vfo_a.mode, 2)
        self.assertEqual(dec.vfo_a.freq_hz, 14_074_000)
        self.assertEqual(dec.vfo_a.filter_high_hz, 2700)
        self.assertEqual(dec.vfo_a.filter_low_hz, 200)
        self.assertEqual(dec.vfo_a.fft_scale, 1)

    def test_181_roundtrip_via_api(self) -> None:
        """Spec P3/H6: default 1.81 API round-trip uses 1.79 wire semantics."""
        raw = build_head_frame(
            mode="USB",
            freq_hz=14_225_000,
            filter_high_hz=2700,
            filter_low_hz=200,
        )
        self.assertEqual(len(raw), 96)
        dec = decode_head(raw)  # default profile 1.81
        self.assertTrue(dec.crc_ok)
        self.assertEqual(dec.profile, Profile.V179)
        self.assertEqual(dec.vfo_a.mode_name, "USB")
        self.assertEqual(dec.vfo_a.mode, 2)
        # Explicit 1.81 string must match 1.79 wire semantics.
        dec179 = decode_head(raw, profile="1.79")
        self.assertEqual(dec.vfo_a.mode, dec179.vfo_a.mode)
        self.assertEqual(dec.raw, dec179.raw)

    def test_181_and_179_encode_identical_for_same_controls(self) -> None:
        """Spec P4: 1.81 and 1.79 encode identical bytes for same controls."""
        kwargs = dict(
            mode="CW",
            freq_hz=7_030_000,
            filter_high_hz=500,
            filter_low_hz=200,
            fft_scale=1,
        )
        a = build_head_frame(profile="1.81", **kwargs)
        b = build_head_frame(profile="1.79", **kwargs)
        self.assertEqual(a, b)



class TestBenchFixtures20260727(unittest.TestCase):
    """Tier B: 2026-07-27 bench captures."""

    def test_live_head_body_crc_ok(self) -> None:
        from g90_codec import decode_body_frame

        raw = Path(__file__).resolve().parent / "fixtures" / "body_181_live_head_20260727.hex"
        frame = bytes.fromhex(raw.read_text().strip())
        body = decode_body_frame(frame)
        self.assertTrue(body.crc_ok)
        self.assertEqual(body.status.po, 0)

    def test_effect_diff_manifest_summary(self) -> None:
        manifest = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "effect_diff_manifest_20260727.jsonl"
        )
        import json

        rows = [json.loads(ln) for ln in manifest.read_text().splitlines() if ln.strip()]
        self.assertEqual(len(rows), 8)
        self.assertEqual({r["Po"] for r in rows}, {0})
        self.assertEqual(rows[0]["label"], "baseline USB")
        self.assertEqual(rows[-1]["freq_hz"], 14_225_000)


class TestCivLiveFixtures(unittest.TestCase):
    """Tier B: live CI-V captures (bench 2026-07-27, User jack on DispUnit)."""

    @staticmethod
    def _load(name: str) -> tuple[bytes, bytes]:
        path = Path(__file__).resolve().parent / "fixtures" / "civ_live" / f"{name}.hex"
        tx = rx = b""
        for line in path.read_text().splitlines():
            if line.startswith("tx="):
                tx = bytes.fromhex(line[3:])
            elif line.startswith("rx="):
                rx = bytes.fromhex(line[3:])
        return tx, rx

    @staticmethod
    def _radio_reply(rx: bytes, radio: int = 0x88) -> CivFrame:
        frames, _ = parse_frames(rx)
        for fr in frames:
            if fr.from_addr == radio:
                return fr
        raise AssertionError(f"no reply from 0x{radio:02X} in {rx.hex()}")

    def test_radio_id_g90(self) -> None:
        from g90_control.civ import decode_radio_id_reply

        _, rx = self._load("radio_id_1d_19_addr88")
        self.assertEqual(decode_radio_id_reply(self._radio_reply(rx)), (0x00, 0x90))

    def test_cq2_att_live_hex(self) -> None:
        from g90_control.civ import decode_att_reply

        _, off_rx = self._load("cq2_att_off")
        _, on_rx = self._load("cq2_att_on")
        self.assertEqual(self._radio_reply(off_rx).payload[0], 0x00)
        self.assertEqual(self._radio_reply(on_rx).payload[0], 0x0C)
        self.assertFalse(decode_att_reply(self._radio_reply(off_rx)))
        self.assertTrue(decode_att_reply(self._radio_reply(on_rx)))

    def test_cq1_set_mode_ack_echo(self) -> None:
        """C-Q1 set step: radio echoes 0x06 frame then ACK (FB)."""
        tx, rx = self._load("cq1_set_mode_usb_filter02")
        self.assertEqual(tx, encode_set_mode("USB", 0x02))
        self.assertIn(bytes.fromhex("FEFE88E0060102FD"), rx)
        self.assertIn(bytes.fromhex("FEFEE088FBFD"), rx)

    def test_cq1_filter_readback_stays_01(self) -> None:
        from g90_control.civ import decode_mode_reply

        _, rx = self._load("cq1_read_mode_after_filter02")
        mode, filt = decode_mode_reply(self._radio_reply(rx))
        self.assertEqual(mode, 0x01)  # USB
        self.assertEqual(filt, 0x01)  # not the 0x02 we set

    def test_cq4_unselected_vfo_reply_shape(self) -> None:
        from g90_control.civ import decode_vfo_freq_reply, decode_vfo_mode_reply

        _, freq_rx = self._load("cq4_vfo_freq_which01")
        _, mode_rx = self._load("cq4_vfo_mode_which01")
        which_f, _ = decode_vfo_freq_reply(self._radio_reply(freq_rx))
        which_m, mode, _data, filt = decode_vfo_mode_reply(self._radio_reply(mode_rx))
        # Radio answers which=0x00 in payload (selected VFO), not the 0x01 we asked.
        self.assertEqual(which_f, 0x00)
        self.assertEqual(which_m, 0x00)
        self.assertEqual(filt, 0x01)

    def test_addr70_read_mode(self) -> None:
        from g90_control.civ import decode_mode_reply

        _, rx = self._load("addr70_read_mode")
        fr = self._radio_reply(rx, radio=0x70)
        mode, filt = decode_mode_reply(fr)
        self.assertEqual(fr.from_addr, 0x70)
        self.assertEqual(mode, 0x01)
        self.assertEqual(filt, 0x01)


class TestCivReadOnly(unittest.TestCase):
    """Synthetic CI-V vectors — no radio required. Spec C2–C4, C1."""

    def test_freq_bcd_roundtrip_icom_packing(self) -> None:
        """Spec C2: LSB-first BCD packing (14.074 MHz → 00 40 07 14 00)."""
        # civ.md XComm / Radioddity Table 2-1 packing for 14.074 MHz
        self.assertEqual(freq_to_bcd(14_074_000), bytes.fromhex("0040071400"))
        self.assertEqual(bcd_to_freq(bytes.fromhex("0040071400")), 14_074_000)
        self.assertEqual(bcd_to_freq(freq_to_bcd(7_030_000)), 7_030_000)

    def test_meter_bcd2(self) -> None:
        self.assertEqual(int_to_bcd2(145), bytes.fromhex("0145"))
        self.assertEqual(bcd2_to_int(bytes.fromhex("0145")), 145)
        self.assertEqual(bcd2_to_int(bytes.fromhex("00")), 0)

    def test_encode_read_frames(self) -> None:
        self.assertEqual(
            encode_read_freq(0x88, 0xE0),
            bytes.fromhex("FEFE88E003FD"),
        )
        self.assertEqual(
            encode_read_mode(0x88, 0xE0),
            bytes.fromhex("FEFE88E004FD"),
        )
        self.assertEqual(
            encode_read_meter(0x02, 0x88, 0xE0),
            bytes.fromhex("FEFE88E01502FD"),
        )

    def test_encode_set_freq_mode(self) -> None:
        # 14.074 MHz → FE FE 88 E0 05 00 40 07 14 00 FD
        self.assertEqual(
            encode_set_freq(14_074_000, 0x88, 0xE0),
            bytes.fromhex("FEFE88E0050040071400FD"),
        )
        # USB + filter 0x02 (flrig shape)
        self.assertEqual(
            encode_set_mode("USB", 0x02, 0x88, 0xE0),
            bytes.fromhex("FEFE88E0060102FD"),
        )
        self.assertEqual(civ_mode_value("CW"), 0x03)
        self.assertEqual(civ_mode_value("CWR"), 0x07)

    def test_encode_ext_att_radio_id(self) -> None:
        self.assertEqual(
            encode_read_att(0x88, 0xE0),
            bytes.fromhex("FEFE88E011FD"),
        )
        self.assertEqual(
            encode_set_att(True, 0x88, 0xE0),
            bytes.fromhex("FEFE88E01101FD"),
        )
        self.assertEqual(
            encode_read_ext(EXT_PRE, 0x88, 0xE0),
            bytes.fromhex("FEFE88E01602FD"),
        )
        self.assertEqual(
            encode_set_ext(EXT_AGC, 0x01, 0x88, 0xE0),
            bytes.fromhex("FEFE88E0161201FD"),
        )
        self.assertEqual(
            encode_read_radio_id(0x88, 0xE0),
            bytes.fromhex("FEFE88E01D19FD"),
        )

    def test_encode_level_split_vfo(self) -> None:
        from g90_control import (
            encode_read_level,
            encode_set_level,
            encode_read_split,
            encode_set_split,
            encode_read_vfo_freq,
            encode_set_vfo_freq,
            encode_read_vfo_mode,
            LVL_AF,
            LVL_RF_POWER,
        )
        from g90_control.civ import (
            CivFrame,
            decode_level_reply,
            level_payload_to_int,
        )

        self.assertEqual(
            encode_read_level(LVL_AF, 0x88, 0xE0),
            bytes.fromhex("FEFE88E01401FD"),
        )
        # AF=128 → BCD 01 28
        self.assertEqual(
            encode_set_level(LVL_AF, 128, 0x88, 0xE0),
            bytes.fromhex("FEFE88E014010128FD"),
        )
        self.assertEqual(
            encode_set_level(LVL_RF_POWER, 255, 0x88, 0xE0),
            bytes.fromhex("FEFE88E0140A0255FD"),
        )
        self.assertEqual(
            encode_read_split(0x88, 0xE0),
            bytes.fromhex("FEFE88E00FFD"),
        )
        self.assertEqual(
            encode_set_split(True, 0x88, 0xE0),
            bytes.fromhex("FEFE88E00F01FD"),
        )
        self.assertEqual(
            encode_read_vfo_freq(0x00, 0x88, 0xE0),
            bytes.fromhex("FEFE88E02500FD"),
        )
        self.assertEqual(
            encode_set_vfo_freq(14_074_000, 0x00, 0x88, 0xE0),
            bytes.fromhex("FEFE88E025000040071400FD"),
        )
        self.assertEqual(
            encode_read_vfo_mode(0x00, 0x88, 0xE0),
            bytes.fromhex("FEFE88E02600FD"),
        )
        # Live G90 quirk: non-BCD low byte (0x7D) must not raise
        self.assertEqual(level_payload_to_int(bytes([0x01, 0x7D])), 225)
        self.assertEqual(
            decode_level_reply(CivFrame(0xE0, 0x88, 0x14, bytes([0x01, 0x01, 0x7D]))),
            (0x01, 225),
        )

    def test_s5_po_abort_when_po_nonzero(self) -> None:
        """S5: effect-diff aborts when body Po≠0 and allow_po=False."""
        from g90_control.cli.effect_diff import po_abort_if_needed

        po_abort_if_needed(0, False)
        po_abort_if_needed(3, True)
        with self.assertRaises(RuntimeError) as ctx:
            po_abort_if_needed(3, False)
        self.assertIn("Po", str(ctx.exception))
        self.assertIn("aborting", str(ctx.exception))

    def test_att_reply_live_encoding(self) -> None:
        """C-Q2: stock G90 ATT on = 0x0C, not Icom-style 0x01."""
        from g90_control.civ import CivFrame, decode_att_reply

        off = parse_frames(bytes.fromhex("FEFEE0881100FD"))[0][0]
        on = parse_frames(bytes.fromhex("FEFEE088110CFD"))[0][0]
        self.assertFalse(decode_att_reply(off))
        self.assertTrue(decode_att_reply(on))
        self.assertEqual(on.payload[0], 0x0C)

    def test_parse_and_decode_replies(self) -> None:
        # Radio 0x88 → controller 0xE0: freq 14.074 MHz
        rx = bytes.fromhex("FEFE E0 88 03 00 40 07 14 00 FD".replace(" ", ""))
        frames, rem = parse_frames(rx)
        self.assertEqual(rem, b"")
        self.assertEqual(len(frames), 1)
        self.assertEqual(decode_freq_reply(frames[0]), 14_074_000)

        mode_rx = bytes.fromhex("FEFEE088040102FD")  # USB + filter 0x02
        fr = parse_frames(mode_rx)[0][0]
        mode, filt = decode_mode_reply(fr)
        self.assertEqual(mode, 0x01)
        self.assertEqual(filt, 0x02)

        meter_rx = bytes.fromhex("FEFEE08815020120FD")  # S-meter ~S9
        sub, val = decode_meter_reply(parse_frames(meter_rx)[0][0])
        self.assertEqual(sub, 0x02)
        self.assertEqual(val, 120)

    def test_block_0x1c_ptt(self) -> None:
        """Spec C1/S4: opcode 0x1C never encoded or sent."""
        self.assertIn(0x1C, BLOCKED_OPCODES)
        with self.assertRaises(CivTxBlockedError):
            build_frame(0x88, 0xE0, 0x1C, 0x00, 0x01)
        with self.assertRaises(CivTxBlockedError):
            build_frame(0x88, 0xE0, 0x1C)  # even bare read of 0x1C
        # send_raw path: CivClient._assert via module helper
        from g90_control.civ import _assert_tx_safe

        with self.assertRaises(CivTxBlockedError):
            _assert_tx_safe(bytes.fromhex("FEFE88E01C0001FD"))
        print("OK  CI-V 0x1C hard-blocked (PTT/ATU)")

    def test_live_session_strips_allow_tx(self) -> None:
        """Spec S3: LiveHeadSession must not honor encode extras.allow_tx."""
        from g90_codec import head_frame_requests_tx
        from g90_codec.frame import HEAD_CTRL3_OPERATE
        from g90_control.session import LiveHeadSession

        class _FakeSer:
            def __init__(self) -> None:
                self.written: list[bytes] = []

            def write(self, data: bytes) -> int:
                self.written.append(bytes(data))
                return len(data)

            def flush(self) -> None:
                return None

            def read(self, n: int) -> bytes:
                return b""

            def reset_input_buffer(self) -> None:
                return None

            def close(self) -> None:
                return None

        # allow_tx keeps operate/active; does not set PTT by itself.
        idle = encode_head_frame("1.81", mode_a="USB", extras={"allow_tx": True})
        self.assertFalse(head_frame_requests_tx(idle))
        self.assertEqual(idle[38] & HEAD_CTRL3_OPERATE, HEAD_CTRL3_OPERATE)

        ser = _FakeSer()
        sess = LiveHeadSession.__new__(LiveHeadSession)
        sess._ser = ser  # type: ignore[attr-defined]
        sess.ctrl = HeadControl("1.81")
        sess.timeout_s = 0.05
        sess.permit_arms = frozenset()

        # exchange strips allow_tx; RX guard keeps operate, clears PTT.
        er = sess.exchange(mode="USB", freq_hz=14_225_000, extras={"allow_tx": True})
        self.assertEqual(len(ser.written), 1)
        self.assertEqual(ser.written[0][38] & HEAD_CTRL3_OPERATE, HEAD_CTRL3_OPERATE)
        self.assertFalse(head_frame_requests_tx(ser.written[0]))
        self.assertIsNone(er.rx)  # no body on fake port
        # arm_atu_tune also stripped when tune not permitted
        er2 = sess.exchange(
            mode="USB",
            freq_hz=14_225_000,
            extras={"arm_atu_tune": True},
        )
        self.assertFalse(head_frame_requests_tx(er2.tx))
        print("OK  LiveHeadSession strips allow_tx")

    def test_live_session_raises_on_armed_ptt(self) -> None:
        """Spec S3: LiveHeadSession refuses PTT-armed frames."""
        from g90_codec import TransmitArmedError, head_frame_requests_tx
        from g90_control.session import LiveHeadSession

        class _FakeSer:
            def write(self, data: bytes) -> int:
                return len(data)

            def flush(self) -> None:
                return None

            def read(self, n: int) -> bytes:
                return b""

            def reset_input_buffer(self) -> None:
                return None

            def close(self) -> None:
                return None

        ser = _FakeSer()
        sess = LiveHeadSession.__new__(LiveHeadSession)
        sess._ser = ser  # type: ignore[attr-defined]
        sess.ctrl = HeadControl("1.81")
        sess.timeout_s = 0.05
        sess.permit_arms = frozenset()

        armed = encode_head_frame(
            "1.81",
            mode_a="USB",
            extras={"allow_tx": True, "arm_ptt": True},
        )
        self.assertTrue(head_frame_requests_tx(armed))
        with self.assertRaises(TransmitArmedError):
            sess.exchange(tx=armed)

    def test_civ_client_send_raw_blocks_1c(self) -> None:
        """Spec S4/C1: CivClient.send_raw rejects 0x1C frames."""
        from g90_control.civ import CivClient

        class _FakeSer:
            def __init__(self) -> None:
                self.written: list[bytes] = []

            def write(self, data: bytes) -> int:
                self.written.append(bytes(data))
                return len(data)

            def flush(self) -> None:
                return None

            def read(self, n: int) -> bytes:
                return b""

            def reset_input_buffer(self) -> None:
                return None

            def reset_output_buffer(self) -> None:
                return None

            @property
            def dtr(self) -> bool:
                return True

            @dtr.setter
            def dtr(self, value: bool) -> None:
                return None

            @property
            def rts(self) -> bool:
                return True

            @rts.setter
            def rts(self, value: bool) -> None:
                return None

            def close(self) -> None:
                return None

        client = CivClient.__new__(CivClient)
        client._ser = _FakeSer()  # type: ignore[attr-defined]
        with self.assertRaises(CivTxBlockedError):
            client.send_raw(bytes.fromhex("FEFE88E01C0001FD"))
        self.assertEqual(client._ser.written, [])  # type: ignore[attr-defined]


def main() -> int:
    print("=== g90-control selftest (track-latest / v1.81) ===")
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    # stdout so banner → golden → tests stay in order (default runner uses stderr).
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    if not result.wasSuccessful():
        print("FAIL g90-control selftest", file=sys.stderr)
        return 1
    print("=== selftest OK ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
