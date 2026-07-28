"""Live fake-head via g90-control — TX 96 / RX 372 on the DE-9.

Wiring (stock head UNPLUGGED, FT232 is the head):
  GND → DE-9 pin 5
  TXD → DE-9 pin 2  (head→body)
  RXD → DE-9 pin 3  (body→head)
  3.3 V logic only; never connect adapter VCC to pin 1 (+8 V).

See ``docs/bench-setup.md``.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


from g90_control.session import LiveHeadSession, candidate_ports, resolve_port  # noqa: E402


def _save_frame(save_dir: Path, prefix: str, frame: bytes, n: int) -> Path:
    save_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = save_dir / f"{prefix}_{ts}_{n:03d}"
    base.with_suffix(".bin").write_bytes(frame)
    hex_path = base.with_suffix(".hex")
    hex_path.write_text(frame.hex() + "\n", encoding="utf-8")
    return hex_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default=None)
    ap.add_argument("--profile", default="1.81")
    ap.add_argument("--count", "-n", type=int, default=10)
    ap.add_argument("--interval-ms", type=float, default=30.0)
    ap.add_argument("--save", metavar="DIR")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--mode", default="USB")
    ap.add_argument("--freq", type=int, default=14_225_000)
    ap.add_argument("--filter-high", type=int, default=2700)
    ap.add_argument("--filter-low", type=int, default=200)
    ap.add_argument("--list-ports", action="store_true")
    args = ap.parse_args(argv)

    if args.list_ports:
        ports = candidate_ports()
        for p in ports:
            print(p)
        return 0 if ports else 1

    port = resolve_port(args.port)
    save_dir = Path(args.save) if args.save else None
    interval = args.interval_ms / 1000.0

    print(
        f"live-head {port} @ {args.baud} profile={args.profile} "
        f"mode={args.mode} freq={args.freq} filter={args.filter_low}-{args.filter_high}",
        file=sys.stderr,
    )
    print(
        "confirm: stock head UNPLUGGED; TXD→pin2 RXD→pin3 GND→pin5; 3.3V logic; "
        "RX-only (PTT/tune/VOX blocked)",
        file=sys.stderr,
    )

    tx_ok = 0
    rx_ok = 0
    rx_fail = 0

    with LiveHeadSession(
        port, baud=args.baud, profile=args.profile, timeout_s=args.timeout
    ) as sess:
        try:
            for n in range(1, args.count + 1):
                er = sess.exchange(
                    mode=args.mode,
                    freq_hz=args.freq,
                    filter_high_hz=args.filter_high,
                    filter_low_hz=args.filter_low,
                )
                tx_ok += 1
                if save_dir is not None and n == 1:
                    path = _save_frame(save_dir, "head_tx", er.tx, n)
                    print(f"[{n}] TX saved {path}", file=sys.stderr)

                if er.rx is None or er.body is None or not er.body.crc_ok:
                    rx_fail += 1
                    print(
                        f"[{n}] TX ok, no body frame within {args.timeout}s",
                        file=sys.stderr,
                    )
                else:
                    rx_ok += 1
                    print(f"[{n}] {er.body.summary()}")
                    if save_dir is not None:
                        path = _save_frame(save_dir, "body_rx", er.rx, n)
                        print(f"     saved {path}")

                time.sleep(interval)
        except KeyboardInterrupt:
            print("\ninterrupted", file=sys.stderr)

    print(
        f"done: tx={tx_ok} body_crc_ok={rx_ok} body_miss={rx_fail}",
        file=sys.stderr,
    )
    return 0 if rx_ok > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
