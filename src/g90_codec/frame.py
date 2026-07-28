"""96-byte head→body and minimal 372-byte body→head framing."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

from .crc import stm32_crc32
from .profiles import (
    Profile,
    decode_filter,
    encode_filter,
    mode_name,
    mode_value,
    validate_fft_scale,
)

HEAD_FRAME_LEN = 96
BODY_FRAME_LEN = 372
_SYNC = b"\x55\xaa"
_FFT_OFF = 48
_FFT_LEN = 320  # bytes 48..367

# Head→body control bits that can key RF (community zeroping schema).
# See docs/protocol/wire-format.md — ctrl1.transmit @ byte 36.
HEAD_PTT_BYTE = 36
HEAD_PTT_BIT = 0x80  # ctrl1.transmit
HEAD_CTRL2_TUNING_BIT = 0x20  # byte 37 — ATU tune (may key TX)
HEAD_CTRL2_SHUTDOWN_BIT = 0x80  # byte 37 — community shutdown_req
# ctrl3 (byte 38), Construct MSB-first: bit1 / wire 0x02 = uint32 bit17.
# Zeroping names this ``tx_disable``; MainUnit/DispUnit treat it as
# **operate/active enable** (``0x20000``). Must be SET for body TX with PTT.
# Clearing it makes MainUnit FUN_08009188 zero the TX state machine.
HEAD_CTRL3_OPERATE = 0x02
HEAD_CTRL3_TX_DISABLE = HEAD_CTRL3_OPERATE  # deprecated alias (misnomer)
HEAD_CTRL3_VOX_EN = 0x08
HEAD_CTRL3_CW_QSK = 0x01
HEAD_CTRL3_AUDIO_LINE = 0x04  # audio_in_line_en
HEAD_CTRL3_CW_DISP = 0x10  # cw_disp_en
# ctrl3 bits 7–5 = rclk_raw_low (3-bit); see pack_rclk_hz.

# ctrl1 (byte 36) — Construct MSB-first BitStruct (community zeroping).
HEAD_CTRL1_MEM_EN = 0x40
HEAD_CTRL1_TUNER_EN = 0x20
HEAD_CTRL1_NB_EN = 0x10
HEAD_CTRL1_MIC_COMP = 0x08
HEAD_CTRL1_HEADPHONES = 0x04
HEAD_CTRL1_SPLIT = 0x02
HEAD_CTRL1_PANEL_LOCK = 0x01

# ctrl6 (byte 54): Padding(7) + vfo_b_en → LSB.
HEAD_CTRL6_VFO_B = 0x01


def pack_rclk_hz(hz: int) -> tuple[int, int]:
    """Pack RCLK offset Hz → (rclk_raw_low 3-bit, rclk_raw_high).

    Community: ``((high << 3) + low) - 1000`` Hz. Clamped to raw 0..2047.
    """
    raw = max(0, min(2047, 1000 + int(hz)))
    return raw & 7, (raw >> 3) & 0xFF


def unpack_rclk_hz(low3: int, high: int) -> int:
    return ((int(high) << 3) + (int(low3) & 7)) - 1000


def pack_rit_hz(hz: int) -> tuple[int, int]:
    """Pack RIT Hz (−512..511) → (ritval_low, ritval_high 2-bit).

    Community Construct: ``(((high<<8)|low + 512) % 1024) - 512``.
    Wire is 10-bit two's complement (``v & 0x3FF``).
    """
    v = max(-512, min(511, int(hz)))
    wire = v & 0x3FF
    return wire & 0xFF, (wire >> 8) & 3


def unpack_rit_hz(low: int, high2: int) -> int:
    wire = ((int(high2) & 3) << 8) | (int(low) & 0xFF)
    return ((wire + 512) % 1024) - 512


def pack_af_gain_beep(gain: int, *, beep_en: bool = True, band_stack_full: bool = False) -> tuple[int, int]:
    """Pack AF ``gain`` (0..127) + beep into ctrl4 bytes 42–43 (VOX fields 0)."""
    g = max(0, min(127, int(gain)))
    gain_low = g & 0x1F
    gain_high = (g >> 5) & 3
    b42 = (gain_low << 3) & 0xF8  # vox_delay_high = 0
    b43 = (
        ((1 if beep_en else 0) << 3)
        | ((1 if band_stack_full else 0) << 2)
        | gain_high
    )
    return b42, b43


def pack_ctrl7(*, cw_ratio_raw: int = 0, cw_mode_b: bool = False, cw_mlr: int = 1) -> int:
    """Pack CW ctrl7 byte (community MSB BitStruct)."""
    ratio = int(cw_ratio_raw) & 0xF
    mlr = int(cw_mlr) & 3
    return ((ratio << 4) | ((1 if cw_mode_b else 0) << 2) | mlr) & 0xFF


def pack_ctrl8(
    *,
    swr_threshold_raw: int = 1,
    aux_out_vol: int = 9,
    aux_in_vol: int = 8,
) -> tuple[int, int]:
    """Pack ctrl8 bytes 82–83 (pad4+swr / aux_out+aux_in)."""
    b82 = int(swr_threshold_raw) & 0xF
    b83 = ((int(aux_out_vol) & 0xF) << 4) | (int(aux_in_vol) & 0xF)
    return b82, b83


def apply_head_controls(buf: bytearray, controls: dict[str, Any]) -> None:
    """Overlay RX-safe community control fields onto a 96-byte head buffer.

    Does **not** set PTT / ATU-tune / VOX (lab TX-arm bits). Call before the
    RX-only guard / CRC. Unknown keys raise ``ValueError``.
    """
    if len(buf) < 86:
        raise ValueError("head buffer too short for controls")
    known = {
        "nb_en",
        "nb_level",
        "nb_width",
        "split_en",
        "vfo_b_en",
        "headphones",
        "audio_in_line_en",
        "mic_compression",
        "tuner_en",
        "panel_lock",
        "mem_en",
        "mem_ch",
        "shutdown_req",
        "cw_disp_en",
        "cw_qsk",
        "cw_wpm",
        "cw_qsk_time_raw",
        "cw_ratio_raw",
        "cw_mode_b",
        "cw_mlr",
        "swr_threshold_raw",
        "aux_out_vol",
        "aux_in_vol",
        "band_stack_full",
        "sql_level",
        "rclk_hz",
        "rit_hz",
        "af_gain",
        "beep_en",
        "mic_gain",
        "rf_power",
        "volume",  # also a top-level encode kw; allowed here for ladder dicts
    }
    unknown = set(controls) - known
    if unknown:
        raise ValueError(f"unknown head controls: {sorted(unknown)}")

    c1 = buf[36]
    if "nb_en" in controls:
        c1 = (c1 | HEAD_CTRL1_NB_EN) if controls["nb_en"] else (c1 & ~HEAD_CTRL1_NB_EN)
    if "split_en" in controls:
        c1 = (c1 | HEAD_CTRL1_SPLIT) if controls["split_en"] else (c1 & ~HEAD_CTRL1_SPLIT)
    if "headphones" in controls:
        c1 = (
            (c1 | HEAD_CTRL1_HEADPHONES)
            if controls["headphones"]
            else (c1 & ~HEAD_CTRL1_HEADPHONES)
        )
    if "mic_compression" in controls:
        c1 = (
            (c1 | HEAD_CTRL1_MIC_COMP)
            if controls["mic_compression"]
            else (c1 & ~HEAD_CTRL1_MIC_COMP)
        )
    if "tuner_en" in controls:
        c1 = (
            (c1 | HEAD_CTRL1_TUNER_EN)
            if controls["tuner_en"]
            else (c1 & ~HEAD_CTRL1_TUNER_EN)
        )
    if "panel_lock" in controls:
        c1 = (
            (c1 | HEAD_CTRL1_PANEL_LOCK)
            if controls["panel_lock"]
            else (c1 & ~HEAD_CTRL1_PANEL_LOCK)
        )
    if "mem_en" in controls:
        c1 = (c1 | HEAD_CTRL1_MEM_EN) if controls["mem_en"] else (c1 & ~HEAD_CTRL1_MEM_EN)
    buf[36] = c1 & 0xFF

    if "shutdown_req" in controls:
        if controls["shutdown_req"]:
            buf[37] = (buf[37] | HEAD_CTRL2_SHUTDOWN_BIT) & 0xFF
        else:
            buf[37] = buf[37] & ~HEAD_CTRL2_SHUTDOWN_BIT & 0xFF

    c3 = buf[38]
    if "audio_in_line_en" in controls:
        c3 = (
            (c3 | HEAD_CTRL3_AUDIO_LINE)
            if controls["audio_in_line_en"]
            else (c3 & ~HEAD_CTRL3_AUDIO_LINE)
        )
    if "cw_disp_en" in controls:
        c3 = (
            (c3 | HEAD_CTRL3_CW_DISP)
            if controls["cw_disp_en"]
            else (c3 & ~HEAD_CTRL3_CW_DISP)
        )
    if "cw_qsk" in controls:
        c3 = (c3 | HEAD_CTRL3_CW_QSK) if controls["cw_qsk"] else (c3 & ~HEAD_CTRL3_CW_QSK)
    if "rclk_hz" in controls:
        low, high = pack_rclk_hz(int(controls["rclk_hz"]))
        c3 = (c3 & 0x1F) | ((low & 7) << 5)
        buf[39] = high
    buf[38] = c3 & 0xFF

    if "nb_level" in controls or "nb_width" in controls:
        level = int(controls.get("nb_level", (buf[46] >> 4) & 0xF)) & 0xF
        width = int(controls.get("nb_width", buf[46] & 0xF)) & 0xF
        buf[46] = ((level << 4) | width) & 0xFF

    if "sql_level" in controls:
        buf[45] = int(controls["sql_level"]) & 0xFF
    if "mic_gain" in controls:
        buf[49] = int(controls["mic_gain"]) & 0xFF
    if "rf_power" in controls:
        buf[44] = int(controls["rf_power"]) & 0xFF
    if "volume" in controls:
        buf[48] = int(controls["volume"]) & 0xFF
    if "cw_wpm" in controls:
        buf[60] = int(controls["cw_wpm"]) & 0xFF
    if "cw_qsk_time_raw" in controls:
        buf[51] = int(controls["cw_qsk_time_raw"]) & 0xFF
    if "mem_ch" in controls:
        buf[53] = int(controls["mem_ch"]) & 0xFF

    if "vfo_b_en" in controls:
        if controls["vfo_b_en"]:
            buf[54] = (buf[54] | HEAD_CTRL6_VFO_B) & 0xFF
        else:
            buf[54] = buf[54] & ~HEAD_CTRL6_VFO_B & 0xFF

    if any(k in controls for k in ("cw_ratio_raw", "cw_mode_b", "cw_mlr")):
        cur = buf[61]
        ratio = int(controls["cw_ratio_raw"]) if "cw_ratio_raw" in controls else (cur >> 4) & 0xF
        mode_b = (
            bool(controls["cw_mode_b"]) if "cw_mode_b" in controls else bool(cur & 0x04)
        )
        mlr = int(controls["cw_mlr"]) if "cw_mlr" in controls else cur & 3
        buf[61] = pack_ctrl7(cw_ratio_raw=ratio, cw_mode_b=mode_b, cw_mlr=mlr)

    if any(k in controls for k in ("swr_threshold_raw", "aux_out_vol", "aux_in_vol")):
        swr = (
            int(controls["swr_threshold_raw"])
            if "swr_threshold_raw" in controls
            else buf[82] & 0xF
        )
        aout = (
            int(controls["aux_out_vol"]) if "aux_out_vol" in controls else (buf[83] >> 4) & 0xF
        )
        ain = int(controls["aux_in_vol"]) if "aux_in_vol" in controls else buf[83] & 0xF
        buf[82], buf[83] = pack_ctrl8(
            swr_threshold_raw=swr, aux_out_vol=aout, aux_in_vol=ain
        )

    if "rit_hz" in controls:
        low, high = pack_rit_hz(int(controls["rit_hz"]))
        buf[84] = low
        # ctrl9: ritval_low @84; byte85 bits1–0 = ritval_high (MSB BitStruct pad6)
        buf[85] = (buf[85] & 0xFC) | (high & 3)

    if "af_gain" in controls or "beep_en" in controls or "band_stack_full" in controls:
        cur_gain = ((buf[43] & 3) << 5) | ((buf[42] >> 3) & 0x1F)
        cur_beep = bool(buf[43] & 0x08)
        cur_stack = bool(buf[43] & 0x04)
        gain = int(controls["af_gain"]) if "af_gain" in controls else cur_gain
        beep = bool(controls["beep_en"]) if "beep_en" in controls else cur_beep
        stack = (
            bool(controls["band_stack_full"])
            if "band_stack_full" in controls
            else cur_stack
        )
        buf[42], buf[43] = pack_af_gain_beep(
            gain, beep_en=beep, band_stack_full=stack
        )


def head_frame_tx_arms(data: bytes) -> frozenset[str]:
    """Which TX-request bits are set: ``ptt``, ``tune``, ``vox``."""
    if len(data) < 39:
        return frozenset()
    arms: set[str] = set()
    if data[HEAD_PTT_BYTE] & HEAD_PTT_BIT:
        arms.add("ptt")
    if data[37] & HEAD_CTRL2_TUNING_BIT:
        arms.add("tune")
    if data[38] & HEAD_CTRL3_VOX_EN:
        arms.add("vox")
    return frozenset(arms)


def head_frame_requests_tx(data: bytes) -> bool:
    """True if the 96-byte head frame asks the body to transmit / tune / VOX.

    Community map: ``ctrl1.transmit`` (byte 36 bit 0x80), ``ctrl2.tuning``
    (byte 37 bit 0x20), ``ctrl3.vox_en`` (byte 38 bit 0x08).

    Note: MainUnit also requires operate/active (byte 38 bit1 / ``0x02``) or it
    nulls TX state — see ``arm_head_ptt``. This helper only detects *request*
    bits for the live RX-only guard. ``shutdown_req`` is separate (not RF).
    """
    return bool(head_frame_tx_arms(data))


def apply_rx_only_guard(buf: bytearray) -> None:
    """Force RX-only control bits before CRC (no antenna / lab safety).

    Clears PTT, ATU-tune, and VOX; **sets operate/active** (byte38 ``0x02``).
    Operate must stay set for a healthy head stream; it is *not* a TX inhibit
    despite the zeroping ``tx_disable`` label. Does **not** clear
    ``shutdown_req``. Call before CRC.
    """
    if len(buf) < 39:
        raise ValueError("head buffer too short for RX-only guard")
    buf[HEAD_PTT_BYTE] &= ~HEAD_PTT_BIT & 0xFF
    buf[37] &= ~HEAD_CTRL2_TUNING_BIT & 0xFF
    buf[38] = (buf[38] | HEAD_CTRL3_OPERATE) & ~HEAD_CTRL3_VOX_EN & 0xFF


def arm_head_ptt(buf: bytearray) -> None:
    """Stock-like key-down bits: PTT + operate/active. Call before CRC.

    DispUnit ORs ``0x80`` and keeps ``0x20000`` (byte38 ``0x02``). MainUnit
    ``FUN_08009188`` ignores PTT when operate is clear.
    """
    if len(buf) < 39:
        raise ValueError("head buffer too short to arm PTT")
    buf[HEAD_PTT_BYTE] |= HEAD_PTT_BIT
    buf[37] &= ~HEAD_CTRL2_TUNING_BIT & 0xFF
    buf[38] = (buf[38] | HEAD_CTRL3_OPERATE) & ~HEAD_CTRL3_VOX_EN & 0xFF


def arm_head_atu_tune(buf: bytearray) -> None:
    """Set ATU ``tuning`` (byte37 ``0x20``) + operate; clear PTT/VOX.

    Community: may key RF while the tuner runs. Call before CRC.
    """
    if len(buf) < 39:
        raise ValueError("head buffer too short to arm ATU tune")
    buf[HEAD_PTT_BYTE] &= ~HEAD_PTT_BIT & 0xFF
    buf[37] = (buf[37] | HEAD_CTRL2_TUNING_BIT) & ~HEAD_CTRL2_SHUTDOWN_BIT & 0xFF
    buf[38] = (buf[38] | HEAD_CTRL3_OPERATE) & ~HEAD_CTRL3_VOX_EN & 0xFF


def arm_head_vox(buf: bytearray) -> None:
    """Set VOX enable (byte38 ``0x08``) + operate; clear PTT/ATU-tune.

    May key RF if audio trips VOX. Call before CRC.
    """
    if len(buf) < 39:
        raise ValueError("head buffer too short to arm VOX")
    buf[HEAD_PTT_BYTE] &= ~HEAD_PTT_BIT & 0xFF
    buf[37] &= ~HEAD_CTRL2_TUNING_BIT & 0xFF
    buf[38] = (buf[38] | HEAD_CTRL3_OPERATE | HEAD_CTRL3_VOX_EN) & 0xFF


class TransmitArmedError(RuntimeError):
    """Raised when a live path would send a TX-requesting head frame."""

    pass


# Zeroping / stock-head defaults for non-semantic padding (community schema).
# Kept so synthetic frames resemble live head-emulation packets.
_DEFAULT_UNKNOWN3 = bytes([0xFF, 0xFF, 0xFF, 0xFF])
_DEFAULT_UNKNOWN4 = bytes(
    [
        0xFF,
        0x0F,
        0xFF,
        0xFF,
        0xFF,
        0xFF,
        0xFF,
        0xFF,
        0xFF,
        0xFF,
        0x1F,
        0x00,
        0x00,
        0x30,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x32,
    ]
)
_DEFAULT_UNKNOWN5 = bytes([0xFF, 0xFF, 0x00, 0x00, 0x00, 0x00])


@dataclass(frozen=True)
class HeadVFO:
    freq_hz: int
    mode: int
    mode_name: str
    att_mode: int
    agc: int
    filter_high_raw: int
    filter_low_raw: int
    filter_high_hz: int
    filter_low_hz: int
    fft_scale: int


@dataclass(frozen=True)
class DecodedHeadFrame:
    profile: Profile
    vfo_a: HeadVFO
    vfo_b: HeadVFO
    volume: int
    crc: int
    crc_ok: bool
    raw: bytes = field(repr=False)

    def summary(self) -> str:
        a, b = self.vfo_a, self.vfo_b
        return (
            f"profile={self.profile.value} crc_ok={self.crc_ok} "
            f"VFO-A mode={a.mode_name}({a.mode}) "
            f"filter={a.filter_low_hz}-{a.filter_high_hz}Hz "
            f"(raw {a.filter_low_raw}/{a.filter_high_raw}) "
            f"fft_scale={a.fft_scale} "
            f"VFO-B mode={b.mode_name}({b.mode}) "
            f"filter={b.filter_low_hz}-{b.filter_high_hz}Hz "
            f"fft_scale={b.fft_scale} volume={self.volume}"
        )


@dataclass(frozen=True)
class BodyStatus:
    """Mapped body→head header fields (see ``docs/protocol/control-spec.md`` §5).

    Bytes 13–19: no MainUnit writer found (fixtures stay zero). Bytes 20–47:
    MainUnit rolling C-string (``cw_text``); head parsers still unread (H4).
    Absolute FFT dBm is H5.
    """

    s_meter: int  # signed byte 0
    po: int  # byte 1 TX power raw
    swr: int  # byte 2
    voltage_raw: int  # byte 3, ×0.1 V
    alc: int  # byte 5
    byte4: int
    atu: int  # byte4 bits 0–1
    version_ready: bool  # bit 2
    status_icon: bool  # bit 3 (DispUnit)
    band_bypass: bool  # bit 4 (DispUnit)
    cw_flag: bool  # bit 5 (DispUnit / community)
    info6: int  # byte 6 — BASE FW minor (%-2X); v1.81 = 0x81
    info7: int  # byte 7 — BASE FW major (%-2X); v1.81 = 0x01 → "1.81"
    link_flag: bool  # word@8 bit 30
    rx_handshake: bool  # word@8 bit 31
    version: int  # byte 12
    header: bytes = field(repr=False)  # bytes 0..47

    @property
    def voltage_v(self) -> float:
        return self.voltage_raw / 10.0

    @property
    def header_nz_13_47(self) -> int:
        return sum(1 for b in self.header[13:48] if b)

    @property
    def cw_text(self) -> str:
        """NUL-terminated ASCII from header bytes 20–47 (MainUnit writer map).

        Body appends into this window (max 26 payload chars + trailing NUL on
        overflow). Head/GSOC parsers do not read it. CW-source attribution is
        **static-RE** (Morse LUT path); live fixtures prove the rolling window
        only — see ``docs/verification.md`` tier D (static RE). Not a v1 freeze
        semantic decode (convenience property name).
        """
        raw = self.header[20:48]
        end = raw.find(b"\x00")
        if end < 0:
            end = len(raw)
        return raw[:end].decode("ascii", errors="replace")

    def summary(self) -> str:
        cw = self.cw_text
        cw_s = f" cw_text={cw!r}" if cw else ""
        return (
            f"S={self.s_meter} Po={self.po} SWR={self.swr} "
            f"V={self.voltage_v:.1f} ALC={self.alc} ATU={self.atu} "
            f"cw={int(self.cw_flag)} link={int(self.link_flag)} "
            f"ver=0x{self.version:02x} byte6=0x{self.info6:02x} "
            f"hdr13-47_nz={self.header_nz_13_47}/35{cw_s}"
        )


def parse_body_status(header48: bytes) -> BodyStatus:
    """Parse the 48-byte body header into mapped meter/status fields."""
    if len(header48) != _FFT_OFF:
        raise ValueError(f"body header must be {_FFT_OFF} bytes")
    (s_meter,) = struct.unpack_from("<b", header48, 0)
    byte4 = header48[4]
    (w8,) = struct.unpack_from("<I", header48, 8)
    return BodyStatus(
        s_meter=s_meter,
        po=header48[1],
        swr=header48[2],
        voltage_raw=header48[3],
        alc=header48[5],
        byte4=byte4,
        atu=byte4 & 0x3,
        version_ready=bool(byte4 & 0x4),
        status_icon=bool(byte4 & 0x8),
        band_bypass=bool(byte4 & 0x10),
        cw_flag=bool(byte4 & 0x20),
        info6=header48[6],
        info7=header48[7],
        link_flag=bool(w8 & 0x40000000),
        rx_handshake=bool(w8 & 0x80000000),
        version=header48[12],
        header=bytes(header48),
    )


def fft_fingerprint(fft: bytes) -> tuple[int, int, int]:
    """Cheap FFT identity: (sum_u16, max_u16, argmax_bin).

    Used by effect differentials — not calibrated dBm (H5).
    """
    if len(fft) < 2:
        return (0, 0, 0)
    n = len(fft) // 2
    total = 0
    peak = 0
    argmax = 0
    for i in range(n):
        (v,) = struct.unpack_from("<H", fft, i * 2)
        total += v
        if v > peak:
            peak = v
            argmax = i
    return (total, peak, argmax)


def header_diffs(a: bytes, b: bytes) -> list[tuple[int, int, int]]:
    """Return ``(offset, old, new)`` for each differing header byte."""
    n = min(len(a), len(b), _FFT_OFF)
    return [(i, a[i], b[i]) for i in range(n) if a[i] != b[i]]


@dataclass(frozen=True)
class DecodedBodyFrame:
    crc: int
    crc_ok: bool
    fft: bytes
    status: BodyStatus
    raw: bytes = field(repr=False)

    def summary(self) -> str:
        fp = fft_fingerprint(self.fft)
        return (
            f"body crc_ok={self.crc_ok} {self.status.summary()} "
            f"fft_sum={fp[0]} fft_peak={fp[1]}@{fp[2]}"
        )


def _resolve_mode(profile: Profile, mode: int | str | None, default: int) -> int:
    if mode is None:
        return default
    if isinstance(mode, int):
        _ = mode_name(profile, mode)  # range check
        return mode
    return mode_value(profile, mode)


def _pack_vfo(
    buf: bytearray,
    base: int,
    *,
    freq_hz: int,
    mode: int,
    att_mode: int,
    agc: int,
    filter_high_raw: int,
    filter_low_raw: int,
    fft_scale: int,
    pad_byte: int = 0,
) -> None:
    struct.pack_into("<I", buf, base, freq_hz & 0xFFFFFFFF)
    buf[base + 4] = att_mode & 0xFF
    buf[base + 5] = mode & 0xFF
    buf[base + 6] = agc & 0xFF
    buf[base + 7] = pad_byte & 0xFF
    buf[base + 8] = filter_high_raw & 0xFF
    buf[base + 9] = filter_low_raw & 0xFF
    # bytes base+10 .. base+14 left as pad / unused filter word high half
    buf[base + 15] = fft_scale & 0x0F


def encode_head_frame(
    profile: Profile | str,
    *,
    freq_a_hz: int = 14_225_000,
    mode_a: int | str = "USB",
    filter_high_hz: int | None = None,
    filter_low_hz: int | None = None,
    filter_high_raw: int | None = None,
    filter_low_raw: int | None = None,
    fft_scale: int = 1,
    freq_b_hz: int = 7_175_000,
    mode_b: int | str = "LSB",
    filter_high_hz_b: int | None = None,
    filter_low_hz_b: int | None = None,
    filter_high_raw_b: int | None = None,
    filter_low_raw_b: int | None = None,
    fft_scale_b: int | None = None,
    att_mode: int = 0,
    agc: int = 0,
    volume: int = 1,
    mic_gain: int = 10,
    rf_power: int = 1,
    sql_level: int = 0,
    controls: dict[str, Any] | None = None,
    extras: dict[str, Any] | None = None,
) -> bytes:
    """Build a 96-byte head→body frame for ``profile`` and append STM32 CRC-32.

    Prefer Hz for filters; raw bytes override when both are given. Defaults for
    unused control bytes follow the community head schema so frames are
    CRC-valid against live head-emulation tooling.

    ``controls`` overlays RX-safe community knobs (NB/split/RIT/RCLK/SQL/…)
    via ``apply_head_controls`` before the RX-only guard.
    """
    p = Profile.parse(profile)
    ma = _resolve_mode(p, mode_a, 1 if p is Profile.V178 else 2)
    mb = _resolve_mode(p, mode_b, 0)

    # Default filter edges (SSB-ish) if caller omits both Hz and raw.
    if filter_high_raw is None and filter_high_hz is None:
        filter_high_hz = 2725 if p is Profile.V178 else 2700
    if filter_low_raw is None and filter_low_hz is None:
        filter_low_hz = 200 if p is Profile.V178 else 200
    if filter_high_raw is None:
        assert filter_high_hz is not None and filter_low_hz is not None
        hi_a, lo_a = encode_filter(p, filter_high_hz, filter_low_hz)
    else:
        hi_a = filter_high_raw & 0xFF
        lo_a = (filter_low_raw if filter_low_raw is not None else 0) & 0xFF

    if filter_high_raw_b is None and filter_high_hz_b is None:
        filter_high_hz_b = filter_high_hz if filter_high_hz is not None else (
            125 + hi_a * 25 if p is Profile.V178 else 50 + hi_a * 50
        )
        filter_low_hz_b = filter_low_hz if filter_low_hz is not None else (
            100 + lo_a * 25 if p is Profile.V178 else 50 + lo_a * 50
        )
    if filter_high_raw_b is None:
        assert filter_high_hz_b is not None and filter_low_hz_b is not None
        hi_b, lo_b = encode_filter(p, filter_high_hz_b, filter_low_hz_b)
    else:
        hi_b = filter_high_raw_b & 0xFF
        lo_b = (filter_low_raw_b if filter_low_raw_b is not None else 0) & 0xFF

    fs_a = validate_fft_scale(p, fft_scale)
    fs_b = validate_fft_scale(p, fft_scale if fft_scale_b is None else fft_scale_b)

    buf = bytearray(HEAD_FRAME_LEN)
    buf[0:2] = _SYNC
    # pad1 @ 2..3 already 0

    _pack_vfo(
        buf,
        4,
        freq_hz=freq_a_hz,
        mode=ma,
        att_mode=att_mode,
        agc=agc,
        filter_high_raw=hi_a,
        filter_low_raw=lo_a,
        fft_scale=fs_a,
    )
    # Community generators often set pad2b (byte 27) = 0x55
    pad2b = 0x55
    if extras and "pad2b" in extras:
        pad2b = int(extras["pad2b"]) & 0xFF
    _pack_vfo(
        buf,
        20,
        freq_hz=freq_b_hz,
        mode=mb,
        att_mode=att_mode,
        agc=agc,
        filter_high_raw=hi_b,
        filter_low_raw=lo_b,
        fft_scale=fs_b,
        pad_byte=pad2b,
    )

    # Control block defaults (zeroping-compatible enough for CRC/golden tests).
    # ctrl3: cw_qsk=1; operate/active set below (RX guard or allow_tx path).
    buf[38] = HEAD_CTRL3_CW_QSK
    buf[39] = 125  # rclk_raw_high
    # ctrl4: gain_low=15, beep_en → 0x78 / 0x08 (Construct MSB BitStruct)
    buf[42] = 0x78
    buf[43] = 0x08
    buf[44] = rf_power & 0xFF
    buf[45] = sql_level & 0xFF
    buf[46] = 0x33  # nb_level=3, nb_width=3
    buf[48] = volume & 0xFF
    buf[49] = mic_gain & 0xFF
    buf[50] = 0x50
    buf[51] = 2
    buf[52] = int(extras.get("unknown2", 2) if extras else 2) & 0xFF
    buf[53] = 6
    buf[56:60] = _DEFAULT_UNKNOWN3
    buf[60] = 10
    buf[61] = 0x01  # cw_mlr=1
    buf[62:82] = _DEFAULT_UNKNOWN4
    buf[82] = 0x01
    buf[83] = 0x98
    buf[86:92] = _DEFAULT_UNKNOWN5

    if extras:
        for key, val in extras.items():
            if key in (
                "pad2b",
                "unknown2",
                "allow_tx",
                "allow_tune",
                "allow_vox",
                "arm_ptt",
                "arm_atu_tune",
                "arm_vox",
                "ptt",
                "clear_operate",
            ):
                continue
            if isinstance(key, int) and 0 <= key < 92:
                buf[key] = int(val) & 0xFF

    if controls:
        apply_head_controls(buf, controls)

    # Lab default: never leave PTT / tune / VOX armed on synthetic frames.
    # ``allow_tx``: skip PTT clear; keep operate/active SET (MainUnit gate).
    # ``allow_tune`` / ``arm_atu_tune``: ATU tuning bit (may key RF).
    # ``allow_vox`` / ``arm_vox``: VOX enable (may key RF on audio).
    # ``clear_operate``: Construct-oracle only (zeroping tx_disable=False) —
    # NOT MainUnit-correct; do not use for live TX.
    # ``arm_ptt`` / ``ptt``: stock-like key-down (PTT + operate).
    if extras and extras.get("clear_operate"):
        buf[HEAD_PTT_BYTE] &= ~HEAD_PTT_BIT & 0xFF
        buf[37] &= ~HEAD_CTRL2_TUNING_BIT & 0xFF
        buf[38] = (buf[38] & ~HEAD_CTRL3_OPERATE & ~HEAD_CTRL3_VOX_EN) & 0xFF
    elif extras and extras.get("allow_tx"):
        buf[38] = (buf[38] | HEAD_CTRL3_OPERATE) & ~HEAD_CTRL3_VOX_EN & 0xFF
        if extras.get("arm_ptt") or extras.get("ptt"):
            arm_head_ptt(buf)
    elif extras and (extras.get("allow_tune") or extras.get("arm_atu_tune")):
        apply_rx_only_guard(buf)
        if extras.get("arm_atu_tune"):
            arm_head_atu_tune(buf)
    elif extras and (extras.get("allow_vox") or extras.get("arm_vox")):
        apply_rx_only_guard(buf)
        if extras.get("arm_vox"):
            arm_head_vox(buf)
    else:
        apply_rx_only_guard(buf)

    crc = stm32_crc32(bytes(buf[:92]))
    struct.pack_into("<I", buf, 92, crc)
    return bytes(buf)


def decode_head_frame(profile: Profile | str, data: bytes) -> DecodedHeadFrame:
    """Decode a 96-byte head→body frame under ``profile`` semantics."""
    p = Profile.parse(profile)
    if len(data) != HEAD_FRAME_LEN:
        raise ValueError(f"head frame must be {HEAD_FRAME_LEN} bytes, got {len(data)}")
    if data[0:2] != _SYNC:
        raise ValueError(f"bad sync {data[0:2].hex()} (expected 55aa)")

    expected = stm32_crc32(data[:92])
    (crc,) = struct.unpack_from("<I", data, 92)

    def _vfo(base: int) -> HeadVFO:
        (freq,) = struct.unpack_from("<I", data, base)
        mode = data[base + 5]
        hi_r = data[base + 8]
        lo_r = data[base + 9]
        hi_hz, lo_hz = decode_filter(p, hi_r, lo_r)
        try:
            mname = mode_name(p, mode)
        except ValueError:
            mname = f"UNK({mode})"
        return HeadVFO(
            freq_hz=freq,
            mode=mode,
            mode_name=mname,
            att_mode=data[base + 4],
            agc=data[base + 6],
            filter_high_raw=hi_r,
            filter_low_raw=lo_r,
            filter_high_hz=hi_hz,
            filter_low_hz=lo_hz,
            fft_scale=data[base + 15] & 0x0F,
        )

    return DecodedHeadFrame(
        profile=p,
        vfo_a=_vfo(4),
        vfo_b=_vfo(20),
        volume=data[48],
        crc=crc,
        crc_ok=crc == expected,
        raw=bytes(data),
    )


def encode_body_frame(*, fft: bytes | None = None, header: bytes | None = None) -> bytes:
    """Build a minimal 372-byte body→head frame (CRC + FFT @48).

    Status/CW fields are zeros unless ``header`` (48 bytes) is supplied.
    """
    buf = bytearray(BODY_FRAME_LEN)
    if header is not None:
        if len(header) != _FFT_OFF:
            raise ValueError(f"body header must be {_FFT_OFF} bytes")
        buf[:_FFT_OFF] = header
    if fft is not None:
        if len(fft) > _FFT_LEN:
            raise ValueError(f"FFT region is {_FFT_LEN} bytes max")
        buf[_FFT_OFF : _FFT_OFF + len(fft)] = fft
    crc = stm32_crc32(bytes(buf[:368]))
    struct.pack_into("<I", buf, 368, crc)
    return bytes(buf)


def decode_body_frame(data: bytes) -> DecodedBodyFrame:
    """Decode a 372-byte body→head frame (CRC + meters/status + FFT)."""
    if len(data) != BODY_FRAME_LEN:
        raise ValueError(f"body frame must be {BODY_FRAME_LEN} bytes, got {len(data)}")
    expected = stm32_crc32(data[:368])
    (crc,) = struct.unpack_from("<I", data, 368)
    return DecodedBodyFrame(
        crc=crc,
        crc_ok=crc == expected,
        fft=bytes(data[_FFT_OFF:368]),
        status=parse_body_status(data[:_FFT_OFF]),
        raw=bytes(data),
    )
