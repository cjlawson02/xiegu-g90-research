"""Icom CI-V (CAT) client for G90 DispUnit User jack @ 19200.

Spec sources:
  - ``docs/protocol/civ.md`` (opcodes, quirks, addr ``0x88``)
  - [Xiegu G90 CI-V reference](https://xiegu.eu/sdm_downloads/g90-civ-reference-v1-0/)
    and [Hamlib](https://github.com/Hamlib/Hamlib) rig definitions

Safety: this module **never** builds or sends opcode ``0x1C`` (PTT / ATU).
Exposed surface: freq/mode/meters, levels ``0x14``, split ``0x0F``,
``0x16`` PRE/AGC/NB/COMP/LOCK, ATT ``0x11``, radio ID ``0x1D 0x19``,
VFO ``0x25``/``0x26``. No TX key.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

# --- Defaults (stock G90 DispUnit User jack / community CAT) -----------------

DEFAULT_BAUD = 19200
DEFAULT_RADIO_ADDR = 0x88  # FLrig / Hamlib / Radioddity; also answers 0x70
DEFAULT_CONTROLLER_ADDR = 0xE0  # Icom controller; GSOC outbound uses 0x20
PREAMBLE = 0xFE
EOM = 0xFD
NAK = 0xFA
ACK = 0xFB

# Hard-blocked opcodes — never encode, never send.
BLOCKED_OPCODES = frozenset(
    {
        0x1C,  # TRX / ATU / PTT — lab policy: never key TX
    }
)

# Command set exposed by CivClient.
CMD_READ_FREQ = 0x03
CMD_READ_MODE = 0x04
CMD_SET_FREQ = 0x05
CMD_SET_MODE = 0x06
CMD_ATT = 0x11
CMD_LEVEL = 0x14
CMD_READ_METER = 0x15
CMD_EXT = 0x16
CMD_RADIO_ID = 0x1D
CMD_VFO_FREQ = 0x25  # fw ≥1.80
CMD_VFO_MODE = 0x26  # fw ≥1.80
CMD_SPLIT = 0x0F

# Meter subcommands (G90 documented subset + common Icom reads).
METER_S = 0x02
METER_PO = 0x11
METER_SWR = 0x12
METER_ALC = 0x13
METER_VD = 0x15
METER_ID = 0x16

# ``0x14`` level subcommands (Radioddity G90 set).
LVL_AF = 0x01
LVL_SQL = 0x03
LVL_NR = 0x06
LVL_CW_PITCH = 0x09
LVL_RF_POWER = 0x0A
LVL_MIC = 0x0B
LVL_CW_SPEED = 0x0C
LVL_QSK = 0x0F
LVL_NB = 0x12
LVL_VOX_GAIN = 0x16
LVL_ANTI_VOX = 0x17

# ``0x16`` extended subcommands (Radioddity / Xiegu CI-V Reference).
EXT_PRE = 0x02
EXT_AGC = 0x12
EXT_NB = 0x22
EXT_COMP = 0x44
EXT_LOCK = 0x50

# Radio ID subcmd (fw ≥1.80): ``0x1D 0x19`` → ``0x00 0x90`` for G90.
SUB_RADIO_ID = 0x19
G90_RADIO_ID = (0x00, 0x90)

# VFO select for ``0x25``/``0x26``.
VFO_SELECTED = 0x00
VFO_UNSELECTED = 0x01

# G90-documented ``0x14`` subcmds (for discovery / exhaust).
G90_LEVEL_SUBCMDS: tuple[int, ...] = (
    LVL_AF,
    LVL_SQL,
    LVL_NR,
    LVL_CW_PITCH,
    LVL_RF_POWER,
    LVL_MIC,
    LVL_CW_SPEED,
    LVL_QSK,
    LVL_NB,
    LVL_VOX_GAIN,
    LVL_ANTI_VOX,
)

# Default filter group byte when setting mode without an explicit filter
# (flrig defaults A.filter = 1).
DEFAULT_FILTER = 0x01

# Xiegu / Icom mode byte → label (G90 documented subset).
MODE_NAMES: dict[int, str] = {
    0x00: "LSB",
    0x01: "USB",
    0x02: "AM",
    0x03: "CW",
    0x05: "NFM",
    0x07: "CWR",
}

MODE_VALUES: dict[str, int] = {
    "LSB": 0x00,
    "USB": 0x01,
    "AM": 0x02,
    "CW": 0x03,
    "NFM": 0x05,
    "FM": 0x05,  # alias
    "CWR": 0x07,
    "CW-R": 0x07,
    "CW_R": 0x07,
}

# AGC value labels (Radioddity Table 1).
AGC_NAMES: dict[int, str] = {
    0x00: "OFF",
    0x01: "FAST",
    0x02: "MID",
    0x03: "SLOW",
}

AGC_VALUES: dict[str, int] = {
    "OFF": 0x00,
    "O": 0x00,
    "FAST": 0x01,
    "F": 0x01,
    "MID": 0x02,
    "MIDDLE": 0x02,
    "M": 0x02,
    "SLOW": 0x03,
    "S": 0x03,
}


class CivError(Exception):
    """Base CI-V error."""


class CivProtocolError(CivError):
    """Malformed frame or unexpected reply."""


class CivTxBlockedError(CivError):
    """Attempt to send a hard-blocked opcode (e.g. ``0x1C`` PTT)."""


class CivTimeoutError(CivError):
    """No complete reply before deadline."""


# --- BCD helpers (Icom / Radioddity Table 2-1 = XComm TX packing) -----------


def freq_to_bcd(freq_hz: int) -> bytes:
    """Encode Hz integer → 5-byte LSB-first BCD (Icom / G90 CAT).

    Example: ``14074000`` → ``00 40 07 14 00`` (matches XComm ``%010lld``
    mid-pair packing in ``docs/protocol/civ.md``).
    """
    if not 0 <= freq_hz <= 999_999_999:
        raise ValueError(f"freq_hz out of BCD range: {freq_hz}")
    s = f"{freq_hz:010d}"  # 10 digits, zero-padded
    # Pairs from least-significant: s[8:10], s[6:8], s[4:6], s[2:4], s[0:2]
    # Each pair is one BCD byte (high nibble = tens digit, low = ones).
    return bytes(
        (int(s[i], 10) << 4) | int(s[i + 1], 10) for i in (8, 6, 4, 2, 0)
    )


def bcd_to_freq(data: bytes) -> int:
    """Decode 5-byte LSB-first BCD → Hz integer."""
    if len(data) < 5:
        raise CivProtocolError(f"freq BCD needs 5 bytes, got {len(data)}")
    digits = []
    for b in data[:5]:
        hi, lo = (b >> 4) & 0x0F, b & 0x0F
        if hi > 9 or lo > 9:
            raise CivProtocolError(f"invalid BCD byte 0x{b:02X}")
        digits.append(f"{hi}{lo}")
    # data[0] is least-significant pair → reverse for decimal string
    return int("".join(reversed(digits)))


def int_to_bcd2(value: int) -> bytes:
    """Encode 0…9999 → 2-byte BCD (``145`` → ``01 45``)."""
    if not 0 <= value <= 9999:
        raise ValueError(f"value out of 2-byte BCD range: {value}")
    s = f"{value:04d}"
    return bytes(
        (
            (int(s[0], 10) << 4) | int(s[1], 10),
            (int(s[2], 10) << 4) | int(s[3], 10),
        )
    )


def bcd2_to_int(data: bytes) -> int:
    """Decode 2-byte BCD → integer (also accepts a single BCD byte)."""
    if not data:
        raise CivProtocolError("empty BCD meter payload")
    total = 0
    for b in data[:2]:
        hi, lo = (b >> 4) & 0x0F, b & 0x0F
        if hi > 9 or lo > 9:
            raise CivProtocolError(f"invalid BCD byte 0x{b:02X}")
        total = total * 100 + hi * 10 + lo
    if len(data) == 1:
        return total  # already one byte
    return total


# --- Framing ----------------------------------------------------------------


@dataclass(frozen=True)
class CivFrame:
    """Parsed CI-V frame (addresses + command body, no preamble/EOM)."""

    to_addr: int
    from_addr: int
    cmd: int
    payload: bytes  # bytes after cmd (subcmd + data)

    @property
    def subcmd(self) -> int | None:
        return self.payload[0] if self.payload else None

    @property
    def data(self) -> bytes:
        return self.payload[1:] if len(self.payload) > 1 else b""

    def summary(self) -> str:
        body = " ".join(f"{b:02X}" for b in self.payload) if self.payload else "-"
        return (
            f"to={self.to_addr:02X} from={self.from_addr:02X} "
            f"cmd={self.cmd:02X} payload=[{body}]"
        )


def build_frame(
    to_addr: int,
    from_addr: int,
    cmd: int,
    *body: int,
) -> bytes:
    """Assemble ``FE FE <to> <from> <cmd> [body…] FD``.

    Raises ``CivTxBlockedError`` if ``cmd`` is hard-blocked (``0x1C``).
    """
    cmd_i = cmd & 0xFF
    if cmd_i in BLOCKED_OPCODES:
        raise CivTxBlockedError(
            f"refusing to encode blocked CI-V opcode 0x{cmd_i:02X} "
            "(no PTT / ATU / TX key — lab policy)"
        )
    for a in (to_addr, from_addr):
        if not 0 <= a <= 0xEF:
            raise ValueError(f"CI-V address out of range: 0x{a:02X}")
    out = bytearray((PREAMBLE, PREAMBLE, to_addr & 0xFF, from_addr & 0xFF, cmd_i))
    out.extend(b & 0xFF for b in body)
    out.append(EOM)
    return bytes(out)


def parse_frames(buf: bytes) -> tuple[list[CivFrame], bytes]:
    """Extract complete ``FE FE … FD`` frames; return (frames, remainder)."""
    frames: list[CivFrame] = []
    i = 0
    n = len(buf)
    while i < n:
        # Hunt preamble
        if buf[i] != PREAMBLE:
            i += 1
            continue
        if i + 1 >= n or buf[i + 1] != PREAMBLE:
            i += 1
            continue
        # Find EOM
        j = i + 2
        while j < n and buf[j] != EOM:
            # Nested FE FE restarts hunt at that position
            if (
                buf[j] == PREAMBLE
                and j + 1 < n
                and buf[j + 1] == PREAMBLE
                and j > i + 1
            ):
                break
            j += 1
        if j >= n or buf[j] != EOM:
            # Incomplete — keep from preamble as remainder
            return frames, bytes(buf[i:])
        body = buf[i + 2 : j]
        if len(body) >= 3:
            frames.append(
                CivFrame(
                    to_addr=body[0],
                    from_addr=body[1],
                    cmd=body[2],
                    payload=bytes(body[3:]),
                )
            )
        i = j + 1
    return frames, b""


def encode_read_freq(
    radio: int = DEFAULT_RADIO_ADDR,
    controller: int = DEFAULT_CONTROLLER_ADDR,
) -> bytes:
    return build_frame(radio, controller, CMD_READ_FREQ)


def encode_read_mode(
    radio: int = DEFAULT_RADIO_ADDR,
    controller: int = DEFAULT_CONTROLLER_ADDR,
) -> bytes:
    return build_frame(radio, controller, CMD_READ_MODE)


def encode_read_meter(
    subcmd: int,
    radio: int = DEFAULT_RADIO_ADDR,
    controller: int = DEFAULT_CONTROLLER_ADDR,
) -> bytes:
    return build_frame(radio, controller, CMD_READ_METER, subcmd & 0xFF)


def encode_set_freq(
    freq_hz: int,
    radio: int = DEFAULT_RADIO_ADDR,
    controller: int = DEFAULT_CONTROLLER_ADDR,
) -> bytes:
    """``0x05`` + 5-byte BCD — same packing as Radioddity / flrig ``to_bcd_be``."""
    bcd = freq_to_bcd(freq_hz)
    return build_frame(radio, controller, CMD_SET_FREQ, *bcd)


def encode_set_mode(
    mode: int | str,
    filter_code: int = DEFAULT_FILTER,
    radio: int = DEFAULT_RADIO_ADDR,
    controller: int = DEFAULT_CONTROLLER_ADDR,
) -> bytes:
    """``0x06`` + mode + filter (flrig Xiegu-G90 shape)."""
    mode_b = mode_value(mode)
    return build_frame(
        radio, controller, CMD_SET_MODE, mode_b, filter_code & 0xFF
    )


def encode_read_att(
    radio: int = DEFAULT_RADIO_ADDR,
    controller: int = DEFAULT_CONTROLLER_ADDR,
) -> bytes:
    return build_frame(radio, controller, CMD_ATT)


def encode_set_att(
    on: bool,
    radio: int = DEFAULT_RADIO_ADDR,
    controller: int = DEFAULT_CONTROLLER_ADDR,
) -> bytes:
    """``0x11`` + ``0x00``/``0x01`` (stock G90 ATT — not GSOC TX ``0x11``)."""
    return build_frame(radio, controller, CMD_ATT, 0x01 if on else 0x00)


def encode_read_ext(
    subcmd: int,
    radio: int = DEFAULT_RADIO_ADDR,
    controller: int = DEFAULT_CONTROLLER_ADDR,
) -> bytes:
    return build_frame(radio, controller, CMD_EXT, subcmd & 0xFF)


def encode_set_ext(
    subcmd: int,
    value: int,
    radio: int = DEFAULT_RADIO_ADDR,
    controller: int = DEFAULT_CONTROLLER_ADDR,
) -> bytes:
    return build_frame(
        radio, controller, CMD_EXT, subcmd & 0xFF, value & 0xFF
    )


def encode_read_radio_id(
    radio: int = DEFAULT_RADIO_ADDR,
    controller: int = DEFAULT_CONTROLLER_ADDR,
) -> bytes:
    return build_frame(radio, controller, CMD_RADIO_ID, SUB_RADIO_ID)


def encode_read_level(
    subcmd: int,
    radio: int = DEFAULT_RADIO_ADDR,
    controller: int = DEFAULT_CONTROLLER_ADDR,
) -> bytes:
    return build_frame(radio, controller, CMD_LEVEL, subcmd & 0xFF)


def encode_set_level(
    subcmd: int,
    value_0_255: int,
    radio: int = DEFAULT_RADIO_ADDR,
    controller: int = DEFAULT_CONTROLLER_ADDR,
) -> bytes:
    """``0x14`` + subcmd + 2-byte BCD (0…255), flrig ``hexchr`` packing."""
    if not 0 <= value_0_255 <= 255:
        raise ValueError(f"level out of range 0..255: {value_0_255}")
    bcd = int_to_bcd2(value_0_255)
    return build_frame(
        radio, controller, CMD_LEVEL, subcmd & 0xFF, bcd[0], bcd[1]
    )


def encode_read_split(
    radio: int = DEFAULT_RADIO_ADDR,
    controller: int = DEFAULT_CONTROLLER_ADDR,
) -> bytes:
    return build_frame(radio, controller, CMD_SPLIT)


def encode_set_split(
    on: bool,
    radio: int = DEFAULT_RADIO_ADDR,
    controller: int = DEFAULT_CONTROLLER_ADDR,
) -> bytes:
    return build_frame(radio, controller, CMD_SPLIT, 0x01 if on else 0x00)


def encode_read_vfo_freq(
    which: int = VFO_SELECTED,
    radio: int = DEFAULT_RADIO_ADDR,
    controller: int = DEFAULT_CONTROLLER_ADDR,
) -> bytes:
    return build_frame(radio, controller, CMD_VFO_FREQ, which & 0xFF)


def encode_set_vfo_freq(
    freq_hz: int,
    which: int = VFO_SELECTED,
    radio: int = DEFAULT_RADIO_ADDR,
    controller: int = DEFAULT_CONTROLLER_ADDR,
) -> bytes:
    bcd = freq_to_bcd(freq_hz)
    return build_frame(radio, controller, CMD_VFO_FREQ, which & 0xFF, *bcd)


def encode_read_vfo_mode(
    which: int = VFO_SELECTED,
    radio: int = DEFAULT_RADIO_ADDR,
    controller: int = DEFAULT_CONTROLLER_ADDR,
) -> bytes:
    return build_frame(radio, controller, CMD_VFO_MODE, which & 0xFF)


def encode_set_vfo_mode(
    mode: int | str,
    data_mode: int = 0,
    filter_code: int = DEFAULT_FILTER,
    which: int = VFO_SELECTED,
    radio: int = DEFAULT_RADIO_ADDR,
    controller: int = DEFAULT_CONTROLLER_ADDR,
) -> bytes:
    """``0x26``: which + mode + data_flag + filter (fw ≥1.80)."""
    return build_frame(
        radio,
        controller,
        CMD_VFO_MODE,
        which & 0xFF,
        mode_value(mode),
        data_mode & 0xFF,
        filter_code & 0xFF,
    )


# --- Reply decode -----------------------------------------------------------


@dataclass(frozen=True)
class CivStatus:
    """Snapshot from CI-V polls."""

    freq_hz: int | None = None
    mode: int | None = None
    mode_name: str | None = None
    filter_code: int | None = None
    s_meter: int | None = None
    po: int | None = None
    swr: int | None = None
    alc: int | None = None
    vd: int | None = None
    id_meter: int | None = None

    def summary(self) -> str:
        parts = []
        if self.freq_hz is not None:
            parts.append(f"freq={self.freq_hz} Hz")
        if self.mode_name is not None:
            parts.append(f"mode={self.mode_name}")
        elif self.mode is not None:
            parts.append(f"mode=0x{self.mode:02X}")
        if self.filter_code is not None:
            parts.append(f"filt=0x{self.filter_code:02X}")
        for label, val in (
            ("S", self.s_meter),
            ("Po", self.po),
            ("SWR", self.swr),
            ("ALC", self.alc),
            ("Vd", self.vd),
            ("Id", self.id_meter),
        ):
            if val is not None:
                parts.append(f"{label}={val}")
        return " ".join(parts) if parts else "(empty)"


def _is_from_radio(
    frame: CivFrame,
    *,
    radio: int,
    controller: int,
) -> bool:
    if frame.from_addr != radio:
        return False
    if frame.to_addr not in (controller, 0x00):
        return False
    return True


def _is_reply_for(
    frame: CivFrame,
    *,
    radio: int,
    controller: int,
    cmd: int,
    subcmd: int | None = None,
) -> bool:
    """True if frame is radio→controller reply for cmd[/subcmd]."""
    if not _is_from_radio(frame, radio=radio, controller=controller):
        return False
    if frame.cmd in (NAK, ACK):
        return False
    if frame.cmd != cmd:
        return False
    if subcmd is not None:
        if not frame.payload or frame.payload[0] != subcmd:
            return False
    return True


def decode_freq_reply(frame: CivFrame) -> int:
    if frame.cmd != CMD_READ_FREQ and frame.cmd != 0x00:
        raise CivProtocolError(f"not a freq reply: cmd=0x{frame.cmd:02X}")
    return bcd_to_freq(frame.payload)


def decode_mode_reply(frame: CivFrame) -> tuple[int, int | None]:
    """Return (mode_byte, optional_filter_code)."""
    if frame.cmd != CMD_READ_MODE and frame.cmd != 0x01:
        raise CivProtocolError(f"not a mode reply: cmd=0x{frame.cmd:02X}")
    if not frame.payload:
        raise CivProtocolError("mode reply missing payload")
    mode = frame.payload[0]
    filt = frame.payload[1] if len(frame.payload) > 1 else None
    return mode, filt


def decode_meter_reply(frame: CivFrame) -> tuple[int, int]:
    """Return (subcmd, BCD integer value)."""
    if frame.cmd != CMD_READ_METER:
        raise CivProtocolError(f"not a meter reply: cmd=0x{frame.cmd:02X}")
    if not frame.payload:
        raise CivProtocolError("meter reply missing subcmd")
    sub = frame.payload[0]
    raw = frame.payload[1:]
    if not raw:
        return sub, 0
    return sub, bcd2_to_int(raw)


def mode_name(mode: int) -> str:
    return MODE_NAMES.get(mode, f"0x{mode:02X}")


def mode_value(mode: int | str) -> int:
    """Resolve mode name or int → CI-V mode byte."""
    if isinstance(mode, int):
        return mode & 0xFF
    key = mode.strip().upper()
    if key not in MODE_VALUES:
        known = ", ".join(sorted(MODE_NAMES.values()))
        raise ValueError(f"unknown mode {mode!r} (want: {known})")
    return MODE_VALUES[key]


def agc_name(value: int) -> str:
    return AGC_NAMES.get(value, f"0x{value:02X}")


def agc_value(agc: int | str) -> int:
    if isinstance(agc, int):
        return agc & 0xFF
    key = agc.strip().upper()
    if key not in AGC_VALUES:
        known = ", ".join(AGC_NAMES.values())
        raise ValueError(f"unknown AGC {agc!r} (want: {known})")
    return AGC_VALUES[key]


def decode_att_reply(frame: CivFrame) -> bool:
    """ATT on/off from ``0x11`` reply.

    Live v1.81: off=``0x00``, on=``0x0C`` (not Icom-style ``0x01``).
    """
    if frame.cmd != CMD_ATT:
        raise CivProtocolError(f"not an ATT reply: cmd=0x{frame.cmd:02X}")
    if not frame.payload:
        return False
    return frame.payload[0] != 0x00


def decode_ext_reply(frame: CivFrame) -> tuple[int, int]:
    """Return (subcmd, value_byte)."""
    if frame.cmd != CMD_EXT:
        raise CivProtocolError(f"not an ext reply: cmd=0x{frame.cmd:02X}")
    if not frame.payload:
        raise CivProtocolError("ext reply missing subcmd")
    sub = frame.payload[0]
    val = frame.payload[1] if len(frame.payload) > 1 else 0
    return sub, val


def decode_radio_id_reply(frame: CivFrame) -> tuple[int, ...]:
    if frame.cmd != CMD_RADIO_ID:
        raise CivProtocolError(f"not a radio-id reply: cmd=0x{frame.cmd:02X}")
    if not frame.payload or frame.payload[0] != SUB_RADIO_ID:
        raise CivProtocolError(f"unexpected radio-id payload: {frame.summary()}")
    return tuple(frame.payload[1:])


def level_byte_value(b: int) -> int:
    """Decode one level byte.

    Prefer classic BCD nibbles (flrig ``hexval``); if a nibble is >9 (seen
    live on G90, e.g. ``0x7D`` / ``0x9B``), fall back to the raw byte.
    """
    hi, lo = (b >> 4) & 0x0F, b & 0x0F
    if hi <= 9 and lo <= 9:
        return hi * 10 + lo
    return b & 0xFF


def level_payload_to_int(data: bytes) -> int:
    """Decode 1–2 level data bytes → 0…≈255 (may slightly exceed on odd wires)."""
    if not data:
        return 0
    if len(data) == 1:
        return level_byte_value(data[0])
    return 100 * level_byte_value(data[0]) + level_byte_value(data[1])


def decode_level_reply(frame: CivFrame) -> tuple[int, int]:
    """Return (subcmd, level int)."""
    if frame.cmd != CMD_LEVEL:
        raise CivProtocolError(f"not a level reply: cmd=0x{frame.cmd:02X}")
    if not frame.payload:
        raise CivProtocolError("level reply missing subcmd")
    sub = frame.payload[0]
    return sub, level_payload_to_int(frame.payload[1:])


def decode_split_reply(frame: CivFrame) -> bool:
    if frame.cmd != CMD_SPLIT:
        raise CivProtocolError(f"not a split reply: cmd=0x{frame.cmd:02X}")
    if not frame.payload:
        return False
    return frame.payload[0] != 0x00


def decode_vfo_freq_reply(frame: CivFrame) -> tuple[int, int]:
    """Return (which, freq_hz)."""
    if frame.cmd != CMD_VFO_FREQ:
        raise CivProtocolError(f"not a 0x25 reply: cmd=0x{frame.cmd:02X}")
    if len(frame.payload) < 6:
        raise CivProtocolError(f"short 0x25 payload: {frame.summary()}")
    which = frame.payload[0]
    return which, bcd_to_freq(frame.payload[1:6])


def decode_vfo_mode_reply(frame: CivFrame) -> tuple[int, int, int, int]:
    """Return (which, mode, data_flag, filter)."""
    if frame.cmd != CMD_VFO_MODE:
        raise CivProtocolError(f"not a 0x26 reply: cmd=0x{frame.cmd:02X}")
    if len(frame.payload) < 4:
        raise CivProtocolError(f"short 0x26 payload: {frame.summary()}")
    which, mode, data, filt = frame.payload[0:4]
    return which, mode, data, filt


# --- Live session -----------------------------------------------------------


class CivClient:
    """CI-V session over pyserial (19200 8N1).

    Never sends ``0x1C``. Freq/mode/meters + ``0x16`` / ATT / radio ID.
    """

    def __init__(
        self,
        port: str,
        *,
        baud: int = DEFAULT_BAUD,
        radio_addr: int = DEFAULT_RADIO_ADDR,
        controller_addr: int = DEFAULT_CONTROLLER_ADDR,
        timeout_s: float = 1.5,
    ) -> None:
        try:
            import serial  # type: ignore
        except ImportError as e:
            raise SystemExit(
                "pyserial required: python3 -m pip install pyserial"
            ) from e

        self.port = port
        self.radio_addr = radio_addr & 0xFF
        self.controller_addr = controller_addr & 0xFF
        self.timeout_s = timeout_s
        self._rx = bytearray()
        self._ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
        )
        # flrig Xiegu-G90 profile: serial_dtrplus=true, serial_rtsplus=true
        # (not used as PTT — serial_dtrptt/rtsptt stay false).
        self._ser.dtr = True
        self._ser.rts = True
        self._ser.reset_input_buffer()
        self._ser.reset_output_buffer()

    def close(self) -> None:
        self._ser.close()

    def __enter__(self) -> CivClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def send_raw(self, frame: bytes) -> None:
        """Write a pre-built frame after a final blocked-opcode scan."""
        _assert_tx_safe(frame)
        self._ser.write(frame)
        self._ser.flush()

    def _drain(self, timeout_s: float | None = None) -> list[CivFrame]:
        t = self.timeout_s if timeout_s is None else timeout_s
        deadline = time.monotonic() + t
        got: list[CivFrame] = []
        while time.monotonic() < deadline:
            chunk = self._ser.read(256)
            if chunk:
                self._rx.extend(chunk)
            frames, rem = parse_frames(bytes(self._rx))
            self._rx = bytearray(rem)
            for fr in frames:
                # Echo of our own TX (controller→radio) — ignore
                if (
                    fr.from_addr == self.controller_addr
                    and fr.to_addr == self.radio_addr
                ):
                    continue
                got.append(fr)
            if got:
                return got
            time.sleep(0.005)
        return got

    def _transact(
        self,
        tx: bytes,
        *,
        expect_cmd: int,
        expect_subcmd: int | None = None,
        timeout_s: float | None = None,
    ) -> CivFrame:
        self.send_raw(tx)
        t = self.timeout_s if timeout_s is None else timeout_s
        deadline = time.monotonic() + t
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            for fr in self._drain(timeout_s=min(0.05, remaining)):
                if fr.cmd == NAK:
                    raise CivProtocolError(f"NAK from radio: {fr.summary()}")
                if _is_reply_for(
                    fr,
                    radio=self.radio_addr,
                    controller=self.controller_addr,
                    cmd=expect_cmd,
                    subcmd=expect_subcmd,
                ):
                    return fr
            time.sleep(0.005)
        raise CivTimeoutError(
            f"timeout waiting for cmd=0x{expect_cmd:02X}"
            + (f"/{expect_subcmd:02X}" if expect_subcmd is not None else "")
            + f" on {self.port}"
        )

    def _wait_ack(self, tx: bytes, *, timeout_s: float | None = None) -> None:
        """Send a set command; expect ``FB`` ACK (flrig ``waitFB``)."""
        self._wait_set_reply(tx, prefer_ack=True, timeout_s=timeout_s)

    def _wait_set_reply(
        self,
        tx: bytes,
        *,
        prefer_ack: bool = True,
        expect_cmd: int | None = None,
        timeout_s: float | None = None,
    ) -> CivFrame | None:
        """Send a set; accept ``FB`` and/or a radio reply with ``expect_cmd``.

        Stock G90 ``0x16`` writes return ``FB``. ``0x11`` ATT writes return
        ``0x11 <state>`` (no ``FB``) — state ``0x00``=off, ``0x0C``=on (v1.81).
        """
        self.send_raw(tx)
        t = self.timeout_s if timeout_s is None else timeout_s
        deadline = time.monotonic() + t
        got_cmd: CivFrame | None = None
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            for fr in self._drain(timeout_s=min(0.05, remaining)):
                if not _is_from_radio(
                    fr, radio=self.radio_addr, controller=self.controller_addr
                ):
                    continue
                if fr.cmd == NAK:
                    raise CivProtocolError(f"NAK from radio: {fr.summary()}")
                if fr.cmd == ACK:
                    return fr
                if expect_cmd is not None and fr.cmd == expect_cmd:
                    got_cmd = fr
                    if not prefer_ack:
                        return fr
            time.sleep(0.005)
        if got_cmd is not None:
            return got_cmd
        raise CivTimeoutError(
            f"timeout waiting for ACK/set-reply on {self.port}"
        )

    def read_freq(self) -> int:
        tx = encode_read_freq(self.radio_addr, self.controller_addr)
        fr = self._transact(tx, expect_cmd=CMD_READ_FREQ)
        return decode_freq_reply(fr)

    def read_mode(self) -> tuple[int, int | None]:
        tx = encode_read_mode(self.radio_addr, self.controller_addr)
        fr = self._transact(tx, expect_cmd=CMD_READ_MODE)
        return decode_mode_reply(fr)

    def read_meter(self, subcmd: int) -> int:
        tx = encode_read_meter(subcmd, self.radio_addr, self.controller_addr)
        fr = self._transact(
            tx, expect_cmd=CMD_READ_METER, expect_subcmd=subcmd & 0xFF
        )
        _sub, value = decode_meter_reply(fr)
        return value

    def set_freq(self, freq_hz: int, *, verify: bool = True) -> int:
        """Set VFO frequency via ``0x05``; optional read-back verify."""
        tx = encode_set_freq(freq_hz, self.radio_addr, self.controller_addr)
        self._wait_ack(tx)
        if not verify:
            return freq_hz
        got = self.read_freq()
        if got != freq_hz:
            raise CivProtocolError(
                f"set_freq verify failed: wanted {freq_hz}, read {got}"
            )
        return got

    def set_mode(
        self,
        mode: int | str,
        filter_code: int | None = None,
        *,
        verify: bool = True,
    ) -> tuple[int, int | None]:
        """Set mode (+ filter) via ``0x06``; optional read-back verify.

        If ``filter_code`` is None, reuse the radio's current filter (or
        ``DEFAULT_FILTER`` if the read has no filter byte).
        """
        if filter_code is None:
            _cur_mode, cur_filt = self.read_mode()
            filter_code = cur_filt if cur_filt is not None else DEFAULT_FILTER
        mode_b = mode_value(mode)
        tx = encode_set_mode(
            mode_b, filter_code, self.radio_addr, self.controller_addr
        )
        self._wait_ack(tx)
        if not verify:
            return mode_b, filter_code
        got_mode, got_filt = self.read_mode()
        if got_mode != mode_b:
            raise CivProtocolError(
                f"set_mode verify failed: wanted 0x{mode_b:02X}, "
                f"read 0x{got_mode:02X}"
            )
        return got_mode, got_filt

    def read_att(self) -> bool:
        tx = encode_read_att(self.radio_addr, self.controller_addr)
        fr = self._transact(tx, expect_cmd=CMD_ATT)
        return decode_att_reply(fr)

    def set_att(self, on: bool, *, verify: bool = True) -> bool:
        """Set attenuator via ``0x11``.

        Live v1.81: write ``0x00``=off / non-zero=on; reply is ``0x11``
        ``<00|0C>`` (not ``FB``). Read-back uses the same encoding.
        """
        if verify:
            cur = self.read_att()
            if cur == bool(on):
                return cur
        tx = encode_set_att(on, self.radio_addr, self.controller_addr)
        self._wait_set_reply(tx, prefer_ack=False, expect_cmd=CMD_ATT)
        if not verify:
            return on
        got = self.read_att()
        if got != bool(on):
            raise CivProtocolError(
                f"set_att verify failed: wanted {on}, read {got}"
            )
        return got

    def read_ext(self, subcmd: int) -> int:
        tx = encode_read_ext(subcmd, self.radio_addr, self.controller_addr)
        fr = self._transact(
            tx, expect_cmd=CMD_EXT, expect_subcmd=subcmd & 0xFF
        )
        _sub, val = decode_ext_reply(fr)
        return val

    def set_ext(self, subcmd: int, value: int, *, verify: bool = True) -> int:
        tx = encode_set_ext(
            subcmd, value, self.radio_addr, self.controller_addr
        )
        self._wait_ack(tx)
        if not verify:
            return value & 0xFF
        got = self.read_ext(subcmd)
        # PRE ON may read back as 0x01 or 0x02
        if subcmd == EXT_PRE:
            want_on = (value & 0xFF) != 0
            got_on = got != 0
            if want_on != got_on:
                raise CivProtocolError(
                    f"set_ext PRE verify failed: wanted on={want_on}, "
                    f"read 0x{got:02X}"
                )
            return got
        if got != (value & 0xFF):
            raise CivProtocolError(
                f"set_ext 0x{subcmd:02X} verify failed: "
                f"wanted 0x{value & 0xFF:02X}, read 0x{got:02X}"
            )
        return got

    def read_pre(self) -> bool:
        return self.read_ext(EXT_PRE) != 0

    def set_pre(self, on: bool, *, verify: bool = True) -> bool:
        got = self.set_ext(EXT_PRE, 0x01 if on else 0x00, verify=verify)
        return got != 0

    def read_agc(self) -> int:
        return self.read_ext(EXT_AGC)

    def set_agc(self, agc: int | str, *, verify: bool = True) -> int:
        return self.set_ext(EXT_AGC, agc_value(agc), verify=verify)

    def read_nb(self) -> bool:
        return self.read_ext(EXT_NB) != 0

    def set_nb(self, on: bool, *, verify: bool = True) -> bool:
        got = self.set_ext(EXT_NB, 0x01 if on else 0x00, verify=verify)
        return got != 0

    def read_comp(self) -> bool:
        return self.read_ext(EXT_COMP) != 0

    def set_comp(self, on: bool, *, verify: bool = True) -> bool:
        got = self.set_ext(EXT_COMP, 0x01 if on else 0x00, verify=verify)
        return got != 0

    def read_lock(self) -> bool:
        return self.read_ext(EXT_LOCK) != 0

    def set_lock(self, on: bool, *, verify: bool = True) -> bool:
        got = self.set_ext(EXT_LOCK, 0x01 if on else 0x00, verify=verify)
        return got != 0

    def read_radio_id(self) -> tuple[int, ...]:
        tx = encode_read_radio_id(self.radio_addr, self.controller_addr)
        fr = self._transact(
            tx, expect_cmd=CMD_RADIO_ID, expect_subcmd=SUB_RADIO_ID
        )
        return decode_radio_id_reply(fr)

    def read_controls(self) -> dict[str, object]:
        """Snapshot PRE/ATT/AGC/NB/COMP/LOCK (+ optional radio ID)."""
        agc = self.read_agc()
        out: dict[str, object] = {
            "att": self.read_att(),
            "pre": self.read_pre(),
            "agc": agc,
            "agc_name": agc_name(agc),
            "nb": self.read_nb(),
            "comp": self.read_comp(),
            "lock": self.read_lock(),
        }
        try:
            out["radio_id"] = self.read_radio_id()
        except (CivTimeoutError, CivProtocolError):
            out["radio_id"] = None
        return out

    def read_level(self, subcmd: int) -> int:
        tx = encode_read_level(subcmd, self.radio_addr, self.controller_addr)
        fr = self._transact(
            tx, expect_cmd=CMD_LEVEL, expect_subcmd=subcmd & 0xFF
        )
        _sub, val = decode_level_reply(fr)
        return val

    def set_level(
        self,
        subcmd: int,
        value_0_255: int,
        *,
        verify: bool | str = "ack",
    ) -> int:
        """Set a ``0x14`` level.

        Live v1.81: writes return ``FB``, but **read-back is not 1:1** with the
        command value (nonlinear / ADC-ish). Default ``verify='ack'`` only
        checks the ACK. Pass ``verify=True``/``'read'`` for best-effort
        read-back (may fail).
        """
        tx = encode_set_level(
            subcmd, value_0_255, self.radio_addr, self.controller_addr
        )
        self._wait_ack(tx)
        mode = verify
        if mode is True:
            mode = "read"
        if mode is False or mode == "ack":
            return value_0_255
        got = self.read_level(subcmd)
        if got != value_0_255:
            raise CivProtocolError(
                f"set_level 0x{subcmd:02X} verify failed: "
                f"wanted {value_0_255}, read {got} "
                f"(G90 level readback is often inexact — use verify='ack')"
            )
        return got

    def read_af(self) -> int:
        return self.read_level(LVL_AF)

    def set_af(self, value_0_255: int, *, verify: bool | str = "ack") -> int:
        return self.set_level(LVL_AF, value_0_255, verify=verify)

    def read_sql(self) -> int:
        return self.read_level(LVL_SQL)

    def set_sql(self, value_0_255: int, *, verify: bool | str = "ack") -> int:
        return self.set_level(LVL_SQL, value_0_255, verify=verify)

    def read_rf_power(self) -> int:
        return self.read_level(LVL_RF_POWER)

    def set_rf_power(self, value_0_255: int, *, verify: bool | str = "ack") -> int:
        """RF power *level* (not PTT). Safe with no antenna."""
        return self.set_level(LVL_RF_POWER, value_0_255, verify=verify)

    def read_mic(self) -> int:
        return self.read_level(LVL_MIC)

    def set_mic(self, value_0_255: int, *, verify: bool | str = "ack") -> int:
        return self.set_level(LVL_MIC, value_0_255, verify=verify)

    def read_nr_level(self) -> int:
        return self.read_level(LVL_NR)

    def set_nr_level(self, value_0_255: int, *, verify: bool | str = "ack") -> int:
        return self.set_level(LVL_NR, value_0_255, verify=verify)

    def read_levels(self) -> dict[str, int | None]:
        """Read G90-documented ``0x14`` subcmds; ``None`` if unsupported."""
        names = {
            LVL_AF: "af",
            LVL_SQL: "sql",
            LVL_NR: "nr",
            LVL_CW_PITCH: "cw_pitch",
            LVL_RF_POWER: "rf_power",
            LVL_MIC: "mic",
            LVL_CW_SPEED: "cw_speed",
            LVL_QSK: "qsk",
            LVL_NB: "nb_level",
            LVL_VOX_GAIN: "vox_gain",
            LVL_ANTI_VOX: "anti_vox",
        }
        out: dict[str, int | None] = {}
        for sub, name in names.items():
            try:
                out[name] = self.read_level(sub)
            except (CivTimeoutError, CivProtocolError):
                out[name] = None
        return out

    def read_split(self) -> bool:
        tx = encode_read_split(self.radio_addr, self.controller_addr)
        fr = self._transact(tx, expect_cmd=CMD_SPLIT)
        return decode_split_reply(fr)

    def set_split(self, on: bool, *, verify: bool = True) -> bool:
        tx = encode_set_split(on, self.radio_addr, self.controller_addr)
        self._wait_ack(tx)
        if not verify:
            return on
        got = self.read_split()
        if got != bool(on):
            raise CivProtocolError(
                f"set_split verify failed: wanted {on}, read {got}"
            )
        return got

    def read_vfo_freq(self, which: int = VFO_SELECTED) -> int:
        tx = encode_read_vfo_freq(which, self.radio_addr, self.controller_addr)
        fr = self._transact(
            tx, expect_cmd=CMD_VFO_FREQ, expect_subcmd=which & 0xFF
        )
        _w, freq = decode_vfo_freq_reply(fr)
        return freq

    def set_vfo_freq(
        self,
        freq_hz: int,
        which: int = VFO_SELECTED,
        *,
        verify: bool = True,
    ) -> int:
        tx = encode_set_vfo_freq(
            freq_hz, which, self.radio_addr, self.controller_addr
        )
        self._wait_ack(tx)
        if not verify:
            return freq_hz
        got = self.read_vfo_freq(which)
        if got != freq_hz:
            raise CivProtocolError(
                f"set_vfo_freq verify failed: wanted {freq_hz}, read {got}"
            )
        return got

    def read_vfo_mode(
        self, which: int = VFO_SELECTED
    ) -> tuple[int, int, int]:
        """Return (mode, data_flag, filter)."""
        tx = encode_read_vfo_mode(which, self.radio_addr, self.controller_addr)
        fr = self._transact(
            tx, expect_cmd=CMD_VFO_MODE, expect_subcmd=which & 0xFF
        )
        _w, mode, data, filt = decode_vfo_mode_reply(fr)
        return mode, data, filt

    def set_vfo_mode(
        self,
        mode: int | str,
        data_mode: int = 0,
        filter_code: int = DEFAULT_FILTER,
        which: int = VFO_SELECTED,
        *,
        verify: bool = True,
    ) -> tuple[int, int, int]:
        tx = encode_set_vfo_mode(
            mode,
            data_mode,
            filter_code,
            which,
            self.radio_addr,
            self.controller_addr,
        )
        self._wait_ack(tx)
        if not verify:
            return mode_value(mode), data_mode, filter_code
        got = self.read_vfo_mode(which)
        if got[0] != mode_value(mode):
            raise CivProtocolError(
                f"set_vfo_mode verify failed: wanted mode "
                f"0x{mode_value(mode):02X}, read 0x{got[0]:02X}"
            )
        return got

    def poll(
        self, *, meters: Iterable[int] = (METER_S, METER_PO, METER_SWR)
    ) -> CivStatus:
        """Read freq + mode + selected meters into a ``CivStatus``."""
        freq = self.read_freq()
        mode, filt = self.read_mode()
        kwargs: dict = {
            "freq_hz": freq,
            "mode": mode,
            "mode_name": mode_name(mode),
            "filter_code": filt,
        }
        meter_map = {
            METER_S: "s_meter",
            METER_PO: "po",
            METER_SWR: "swr",
            METER_ALC: "alc",
            METER_VD: "vd",
            METER_ID: "id_meter",
        }
        for sub in meters:
            key = meter_map.get(sub & 0xFF)
            if key is None:
                continue
            try:
                kwargs[key] = self.read_meter(sub)
            except (CivTimeoutError, CivProtocolError):
                kwargs[key] = None
        return CivStatus(**kwargs)


def _assert_tx_safe(frame: bytes) -> None:
    """Scan wire bytes for blocked opcodes inside complete frames."""
    frames, _ = parse_frames(frame)
    if not frames and frame:
        # Incomplete / raw — still reject if 0x1C appears as likely cmd slot
        # after a preamble (FE FE xx xx 1C).
        i = 0
        while True:
            i = frame.find(bytes((PREAMBLE, PREAMBLE)), i)
            if i < 0:
                break
            if i + 5 < len(frame) and frame[i + 4] in BLOCKED_OPCODES:
                raise CivTxBlockedError(
                    f"refusing to send blocked opcode 0x{frame[i + 4]:02X}"
                )
            i += 2
        return
    for fr in frames:
        if fr.cmd in BLOCKED_OPCODES:
            raise CivTxBlockedError(
                f"refusing to send blocked opcode 0x{fr.cmd:02X}"
            )
