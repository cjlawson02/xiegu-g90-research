"""STM32 hardware CRC-32 (CRC-32/MPEG-2 over little-endian words).

Matches GSOC ``xcrc::calCRC32_STM32`` and the community zeroping recipe:
byte-swap each 32-bit LE word, then CRC-32/MPEG-2 (poly ``0x04C11DB7``,
init ``0xFFFFFFFF``, no final XOR). Verified by ``TestCrc`` / fixture CRC checks.
"""

from __future__ import annotations

_POLY = 0x04C11DB7


def crc32_mpeg2(data: bytes) -> int:
    """CRC-32/MPEG-2 over a byte stream (MSB-first per byte)."""
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            if crc & 0x80000000:
                crc = ((crc << 1) & 0xFFFFFFFF) ^ _POLY
            else:
                crc = (crc << 1) & 0xFFFFFFFF
    return crc


def _swap_words(data: bytes) -> bytes:
    if len(data) % 4:
        raise ValueError(f"STM32 CRC length must be a multiple of 4, got {len(data)}")
    out = bytearray(len(data))
    for i in range(0, len(data), 4):
        out[i] = data[i + 3]
        out[i + 1] = data[i + 2]
        out[i + 2] = data[i + 1]
        out[i + 3] = data[i]
    return bytes(out)


def stm32_crc32(data: bytes) -> int:
    """STM32 CRC-32 over ``data`` (little-endian uint32 words on the wire)."""
    return crc32_mpeg2(_swap_words(data))
