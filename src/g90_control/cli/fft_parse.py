"""Offline FFT bench helper: body hex → peak bin + display dB proxy.

See ``docs/fft-bench.md``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from g90_control.fft_bench import analyze_body, display_db_proxy, load_body_hex


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hex-file", type=Path, required=True)
    ap.add_argument("--label", default="")
    ap.add_argument("--dbm-true", type=float, default=None, metavar="DBM")
    ap.add_argument("--expect-offset-hz", type=float, default=None, metavar="HZ")
    ap.add_argument("--vfo-bin", type=int, default=80)
    args = ap.parse_args(argv)

    raw = load_body_hex(path=args.hex_file)
    r = analyze_body(
        raw,
        dbm_true=args.dbm_true,
        label=args.label,
        expect_offset_hz=args.expect_offset_hz,
        vfo_bin=args.vfo_bin,
    )

    tag = f" [{r['label']}]" if r["label"] else ""
    print(f"FFT parse{tag}  CRC_ok={r['crc_ok']}  S={r['s_meter']}  Po={r['po']}")
    fp = r["fft_fp"]
    print(f"  fft_fp={fp}  mean_u16={r['mean_u16']:.1f}")
    print(
        f"  peak bin={r['peak_bin']}  u16={r['peak_u16']}  "
        f"signed={r['peak_signed']}  dB~={r['db_proxy_y']:.2f}"
    )
    if args.dbm_true is not None:
        print(f"  dbm_true={args.dbm_true}")
    if args.expect_offset_hz is not None:
        print(
            f"  offset={args.expect_offset_hz} Hz  "
            f"Δbin={r['delta_bin']} (vs vfo_bin={args.vfo_bin})  "
            f"Hz/bin≈{r['hz_per_bin']:.2f}"
        )

    bins = r["bins"]
    ranked = sorted(range(160), key=lambda i: bins[i], reverse=True)[:5]
    print("  top5:")
    for i in ranked:
        print(f"    bin {i:3d}  u16={bins[i]:5d}  dB~={display_db_proxy(bins[i]):7.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
