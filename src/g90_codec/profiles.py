"""Versioned payload semantics for the G90 head↔body register mirror.

Source of truth: ``docs/protocol/firmware-break.md`` and ``docs/verification.md``.
"""

from __future__ import annotations

from enum import Enum
from typing import Mapping


class Profile(str, Enum):
    """Codec profile selecting payload register semantics."""

    V178 = "1.78"
    """GSOC / DispUnit ≤1.78 — mode 0–5, filter ÷25, fft_scale 1–10."""

    V179 = "1.79"
    """DispUnit ≥1.79b02 (path toward v1.81) — mode 0–7, filter ÷50, AUTO fft."""

    @classmethod
    def parse(cls, value: str | Profile) -> Profile:
        if isinstance(value, Profile):
            return value
        key = value.strip().lower()
        aliases = {
            "1.78": cls.V178,
            "178": cls.V178,
            "v178": cls.V178,
            "gsoc": cls.V178,
            "1.79": cls.V179,
            "1.79+": cls.V179,
            "179": cls.V179,
            "v179": cls.V179,
            "1.81": cls.V179,  # quiet ladder — see docs/verification.md tier D
        }
        if key not in aliases:
            raise ValueError(
                f"unknown profile {value!r}; use '1.78' (GSOC) or '1.79' (post-break)"
            )
        return aliases[key]


# §7.1 — L-D/U-D inserted after LSB/USB in 1.79 (not appended).
MODE_NAMES: Mapping[Profile, tuple[str, ...]] = {
    Profile.V178: ("LSB", "USB", "CW", "CWR", "AM", "NFM"),
    Profile.V179: ("LSB", "L-D", "USB", "U-D", "CW", "CWR", "AM", "NFM"),
}

_NAME_ALIASES = {
    "LSB-DIG": "L-D",
    "LSB_DIG": "L-D",
    "LD": "L-D",
    "USB-DIG": "U-D",
    "USB_DIG": "U-D",
    "UD": "U-D",
    "CWR": "CWR",
    "CW-R": "CWR",
}


def mode_name(profile: Profile | str, value: int) -> str:
    p = Profile.parse(profile)
    names = MODE_NAMES[p]
    if not 0 <= value < len(names):
        raise ValueError(f"mode {value} out of range for profile {p.value} (0..{len(names)-1})")
    return names[value]


def mode_value(profile: Profile | str, name: str) -> int:
    p = Profile.parse(profile)
    key = _NAME_ALIASES.get(name.upper(), name.upper())
    # Accept both "L-D" and "L_D"
    key = key.replace("_", "-")
    names = MODE_NAMES[p]
    try:
        return names.index(key)
    except ValueError as exc:
        raise ValueError(
            f"mode {name!r} not in profile {p.value}: {', '.join(names)}"
        ) from exc


def encode_filter(
    profile: Profile | str,
    high_hz: int,
    low_hz: int,
) -> tuple[int, int]:
    """Hz → ``(filter_high_raw, filter_low_raw)`` for bytes 12–13.

    * **1.78:** ``high = (Hz − 125) / 25``, ``low = (Hz − 100) / 25``
    * **1.79+:** both edges ``(Hz − 50) / 50`` (Hz < 50 → 0; raw clamped to 255)
    """
    p = Profile.parse(profile)
    if p is Profile.V178:
        hi = (int(high_hz) - 125) // 25
        lo = (int(low_hz) - 100) // 25
    else:
        hi = 0 if high_hz < 50 else (int(high_hz) - 50) // 50
        lo = 0 if low_hz < 50 else (int(low_hz) - 50) // 50
    return max(0, min(255, hi)), max(0, min(255, lo))


def decode_filter(
    profile: Profile | str,
    high_raw: int,
    low_raw: int,
) -> tuple[int, int]:
    """Raw bytes 12–13 → ``(filter_high_hz, filter_low_hz)``."""
    p = Profile.parse(profile)
    hi_r = high_raw & 0xFF
    lo_r = low_raw & 0xFF
    if p is Profile.V178:
        return 125 + hi_r * 25, 100 + lo_r * 25
    return 50 + hi_r * 50, 50 + lo_r * 50


def validate_fft_scale(profile: Profile | str, value: int) -> int:
    """Clamp / validate ``fft_scale`` (byte 19 / 35).

    * **1.78:** 1 = AUTO, 2–10 manual
    * **1.79+:** library encode allows only ``1`` (**H5**). The body still
      applies 1–10; this function does not emit those values.
    """
    p = Profile.parse(profile)
    v = int(value) & 0x0F
    if p is Profile.V178:
        if not 1 <= v <= 10:
            raise ValueError(f"fft_scale {value} out of range for 1.78 (1..10)")
        return v
    if v != 1:
        raise ValueError(
            f"fft_scale {value} invalid for 1.79+ library encode (H5 allows only 1)"
        )
    return 1
