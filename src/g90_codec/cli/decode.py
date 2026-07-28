"""Decode or encode G90 head↔body frames."""

from __future__ import annotations

import argparse
from pathlib import Path

from g90_codec import (
    BODY_FRAME_LEN,
    HEAD_FRAME_LEN,
    Profile,
    decode_body_frame,
    decode_head_frame,
    encode_head_frame,
)


def _load_bytes(args: argparse.Namespace) -> bytes:
    if args.hex:
        h = args.hex.replace(" ", "").replace(":", "")
        return bytes.fromhex(h)
    if args.path:
        return Path(args.path).read_bytes()
    raise SystemExit("provide a frame file path or --hex")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Decode or encode G90 DE-9 head↔body frames (96 B / 372 B)."
    )
    ap.add_argument("path", nargs="?", help="raw frame file (96 or 372 bytes)")
    ap.add_argument(
        "--profile",
        "-p",
        default="1.78",
        help="payload profile: 1.78 (GSOC) or 1.79+ (alias 1.81)",
    )
    ap.add_argument("--hex", help="frame as hex string instead of a file")
    ap.add_argument(
        "--encode",
        action="store_true",
        help="build a synthetic head frame and print decode summary",
    )
    ap.add_argument("--mode", default="USB", help="VFO-A mode name or int")
    ap.add_argument("--freq", type=int, default=14_225_000, help="VFO-A Hz")
    ap.add_argument("--filter-high", type=int, default=None)
    ap.add_argument("--filter-low", type=int, default=None)
    ap.add_argument("--fft-scale", type=int, default=1)
    ap.add_argument("--volume", type=int, default=1)
    ap.add_argument(
        "--write",
        metavar="OUT",
        help="with --encode, also write the 96-byte frame to OUT",
    )
    args = ap.parse_args(argv)

    profile = Profile.parse(args.profile)

    if args.encode:
        frame = encode_head_frame(
            profile,
            freq_a_hz=args.freq,
            mode_a=args.mode,
            filter_high_hz=args.filter_high,
            filter_low_hz=args.filter_low,
            fft_scale=args.fft_scale,
            volume=args.volume,
        )
        if args.write:
            Path(args.write).write_bytes(frame)
        decoded = decode_head_frame(profile, frame)
        print(decoded.summary())
        print(f"hex={frame.hex()}")
        return 0 if decoded.crc_ok else 1

    data = _load_bytes(args)
    if len(data) == HEAD_FRAME_LEN:
        decoded = decode_head_frame(profile, data)
        print(decoded.summary())
        return 0 if decoded.crc_ok else 1
    if len(data) == BODY_FRAME_LEN:
        body = decode_body_frame(data)
        print(body.summary())
        return 0 if body.crc_ok else 1
    raise SystemExit(
        f"expected {HEAD_FRAME_LEN} or {BODY_FRAME_LEN} bytes, got {len(data)}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
