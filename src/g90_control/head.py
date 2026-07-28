"""Head→body control helpers for track-latest / v1.81.

Reuses ``g90_codec`` for all payload packing; does not reimplement CRC, mode
maps, or filter encoding.
"""

from __future__ import annotations

from typing import Any

from g90_codec import (
    BODY_FRAME_LEN,
    HEAD_FRAME_LEN,
    BodyStatus,
    DecodedBodyFrame,
    DecodedHeadFrame,
    Profile,
    decode_body_frame,
    decode_head_frame,
    encode_body_frame,
    encode_head_frame,
    fft_fingerprint,
    header_diffs,
)

# Fork lock: product speaks v1.81; wire profile is post-break 1.79 (quiet ladder).
TARGET_PROFILE = "1.81"
TARGET_CODEC = Profile.V179

# Sensible SSB defaults for the post-break codec (÷50 filter edges, AUTO fft).
_DEFAULT_FILTER_HIGH_HZ = 2700
_DEFAULT_FILTER_LOW_HZ = 200
_DEFAULT_FFT_SCALE = 1


class HeadControl:
    """Construct and decode head→body frames for a codec profile.

    Defaults to ``1.81`` (aliases ``1.79``). Pass ``profile="1.78"`` only for
    differential / mishap demos — not the product target.
    """

    def __init__(self, profile: str | Profile = TARGET_PROFILE) -> None:
        self.profile = Profile.parse(profile)

    @property
    def codec_profile(self) -> Profile:
        """Resolved codec enum (``1.81`` → ``Profile.V179``)."""
        return self.profile

    def encode(
        self,
        *,
        mode: int | str = "USB",
        freq_hz: int = 14_225_000,
        filter_high_hz: int = _DEFAULT_FILTER_HIGH_HZ,
        filter_low_hz: int = _DEFAULT_FILTER_LOW_HZ,
        fft_scale: int = _DEFAULT_FFT_SCALE,
        **kwargs: Any,
    ) -> bytes:
        """Build a CRC-valid 96-byte head→body frame.

        ``mode`` / filter Hz / ``fft_scale`` / ``freq_hz`` map through the
        selected profile. Extra kwargs forward to ``encode_head_frame``
        (VFO-B, volume, att, agc, …).
        """
        return encode_head_frame(
            self.profile,
            freq_a_hz=freq_hz,
            mode_a=mode,
            filter_high_hz=filter_high_hz,
            filter_low_hz=filter_low_hz,
            fft_scale=fft_scale,
            **kwargs,
        )

    def decode(self, data: bytes) -> DecodedHeadFrame:
        """Decode a 96-byte head frame under this instance's profile."""
        return decode_head_frame(self.profile, data)

    def encode_body(self, *, fft: bytes | None = None, header: bytes | None = None) -> bytes:
        """Minimal 372-byte body→head frame (CRC + FFT @48) — codec passthrough."""
        return encode_body_frame(fft=fft, header=header)

    def decode_body(self, data: bytes) -> DecodedBodyFrame:
        """Decode a 372-byte body frame (CRC + FFT slice)."""
        return decode_body_frame(data)


def build_head_frame(
    *,
    mode: int | str = "USB",
    freq_hz: int = 14_225_000,
    filter_high_hz: int = _DEFAULT_FILTER_HIGH_HZ,
    filter_low_hz: int = _DEFAULT_FILTER_LOW_HZ,
    fft_scale: int = _DEFAULT_FFT_SCALE,
    profile: str | Profile = TARGET_PROFILE,
    **kwargs: Any,
) -> bytes:
    """One-shot: build a v1.81 (default) head frame with the given controls."""
    return HeadControl(profile).encode(
        mode=mode,
        freq_hz=freq_hz,
        filter_high_hz=filter_high_hz,
        filter_low_hz=filter_low_hz,
        fft_scale=fft_scale,
        **kwargs,
    )


def decode_head(
    data: bytes,
    profile: str | Profile = TARGET_PROFILE,
) -> DecodedHeadFrame:
    """One-shot decode under ``profile`` (default ``1.81`` → 1.79 codec)."""
    return HeadControl(profile).decode(data)


def decode_body(data: bytes) -> DecodedBodyFrame:
    """One-shot minimal body decode (CRC + FFT)."""
    return decode_body_frame(data)


# Re-export lengths / telemetry helpers for callers that want them without
# importing the codec package directly.
__all__ = [
    "TARGET_PROFILE",
    "TARGET_CODEC",
    "HEAD_FRAME_LEN",
    "BODY_FRAME_LEN",
    "BodyStatus",
    "HeadControl",
    "build_head_frame",
    "decode_head",
    "decode_body",
    "fft_fingerprint",
    "header_diffs",
]
