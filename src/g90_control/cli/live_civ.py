"""Live CI-V against stock G90 DispUnit User jack @ 19200.

Stock head **on** the body; CAT cable on the DispUnit **User** port (person
icon — not rear/base COMM, not DE-9). Reads freq/mode/meters; optional set freq/mode; ``--controls`` /
``--levels`` snapshots. Never sends ``0x1C`` (PTT/ATU). No antenna / never
key TX.

Spec: ``docs/protocol/civ.md``.

Examples::

  g90-live-civ --list-ports
  g90-live-civ --port /dev/cu.usbserial-XXX
  g90-live-civ --port /dev/cu.usbserial-XXX \\
      --set-freq 14225000 --set-mode USB --restore
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent

from g90_control import (  # noqa: E402
    DEFAULT_BAUD,
    DEFAULT_CONTROLLER_ADDR,
    DEFAULT_RADIO_ADDR,
    METER_ALC,
    METER_ID,
    METER_PO,
    METER_S,
    METER_SWR,
    METER_VD,
    MODE_NAMES,
    CivClient,
    CivProtocolError,
    CivTimeoutError,
    CivTxBlockedError,
    candidate_ports,
    mode_name,
    resolve_port,
)


def _parse_addr(s: str) -> int:
    return int(s, 0) & 0xFF


def main() -> int:
    ap = argparse.ArgumentParser(
        description="CI-V poll / set freq+mode — no 0x1C / no TX"
    )
    ap.add_argument("--port", help="serial device (CAT cable on DispUnit User jack)")
    ap.add_argument("--list-ports", action="store_true")
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument(
        "--addr",
        type=_parse_addr,
        default=DEFAULT_RADIO_ADDR,
        help="radio CI-V address (default 0x88; try 0x70 if no reply)",
    )
    ap.add_argument(
        "--controller",
        type=_parse_addr,
        default=DEFAULT_CONTROLLER_ADDR,
        help="controller address (default 0xE0)",
    )
    ap.add_argument("--count", type=int, default=1, help="poll iterations")
    ap.add_argument(
        "--interval", type=float, default=0.5, help="seconds between polls"
    )
    ap.add_argument(
        "--meters",
        default="s,po,swr",
        help="comma list: s,po,swr,alc,vd,id (default s,po,swr)",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=1.5,
        help="per-command reply timeout seconds",
    )
    ap.add_argument(
        "--debug",
        action="store_true",
        help="dump RX hex on timeout",
    )
    ap.add_argument(
        "--set-freq",
        type=int,
        default=None,
        metavar="HZ",
        help="set VFO frequency (Hz) via 0x05; verified by read-back",
    )
    ap.add_argument(
        "--set-mode",
        default=None,
        metavar="MODE",
        help=f"set mode via 0x06 ({', '.join(MODE_NAMES.values())})",
    )
    ap.add_argument(
        "--filter",
        type=lambda s: int(s, 0) & 0xFF,
        default=None,
        help="filter group byte for --set-mode (default: keep current)",
    )
    ap.add_argument(
        "--restore",
        action="store_true",
        help="after --set-*, restore previous freq/mode",
    )
    ap.add_argument(
        "--controls",
        action="store_true",
        help="print ATT/PRE/AGC/NB/COMP/LOCK + radio ID snapshot",
    )
    ap.add_argument(
        "--levels",
        action="store_true",
        help="print 0x14 level snapshot (AF/SQL/power/…)",
    )
    args = ap.parse_args()

    if args.list_ports:
        for p in candidate_ports():
            print(p)
        return 0

    meter_names = {
        "s": METER_S,
        "po": METER_PO,
        "swr": METER_SWR,
        "alc": METER_ALC,
        "vd": METER_VD,
        "id": METER_ID,
    }
    meters: list[int] = []
    for tok in args.meters.split(","):
        tok = tok.strip().lower()
        if not tok:
            continue
        if tok not in meter_names:
            print(
                f"unknown meter '{tok}' (want: {', '.join(meter_names)})",
                file=sys.stderr,
            )
            return 2
        meters.append(meter_names[tok])

    port = resolve_port(args.port)
    writing = args.set_freq is not None or args.set_mode is not None
    print(
        f"CI-V  port={port}  baud={args.baud}  "
        f"radio=0x{args.addr:02X}  ctrl=0x{args.controller:02X}",
        flush=True,
    )
    print(
        "policy: no 0x1C — never key TX"
        + (" ; set freq/mode enabled" if writing else " ; poll only"),
        flush=True,
    )

    try:
        with CivClient(
            port,
            baud=args.baud,
            radio_addr=args.addr,
            controller_addr=args.controller,
            timeout_s=args.timeout,
        ) as civ:
            before = civ.poll(meters=meters)
            print(f"before  {before.summary()}", flush=True)
            if before.po not in (None, 0):
                print(
                    f"WARN: Po={before.po} (expected 0 while RX / no antenna)",
                    file=sys.stderr,
                )

            if args.controls:
                print(f"controls {civ.read_controls()}", flush=True)
            if args.levels:
                print(f"levels   {civ.read_levels()}", flush=True)

            if writing:
                if args.set_freq is not None:
                    got = civ.set_freq(args.set_freq)
                    print(f"set-freq {args.set_freq} → verify {got}", flush=True)
                if args.set_mode is not None:
                    got_m, got_f = civ.set_mode(args.set_mode, args.filter)
                    print(
                        f"set-mode {args.set_mode} → "
                        f"verify {mode_name(got_m)} filt=0x{(got_f or 0):02X}",
                        flush=True,
                    )
                after = civ.poll(meters=meters)
                print(f"after   {after.summary()}", flush=True)
                if after.po not in (None, 0):
                    print(
                        f"WARN: Po={after.po} after set (expected 0)",
                        file=sys.stderr,
                    )
                if args.restore:
                    if before.freq_hz is not None:
                        civ.set_freq(before.freq_hz)
                    if before.mode is not None:
                        civ.set_mode(before.mode, before.filter_code)
                    print(
                        f"restore {civ.poll(meters=meters).summary()}",
                        flush=True,
                    )
                print("OK  set/verify complete", flush=True)
                return 0

            ok = 1  # counted `before`
            for i in range(1, max(1, args.count)):
                try:
                    st = civ.poll(meters=meters)
                    print(f"[{i + 1}/{args.count}] {st.summary()}", flush=True)
                    if st.po not in (None, 0):
                        print(
                            f"WARN: Po={st.po} (expected 0 while RX / no antenna)",
                            file=sys.stderr,
                        )
                    ok += 1
                except CivTimeoutError as e:
                    print(f"[{i + 1}/{args.count}] TIMEOUT {e}", file=sys.stderr)
                    if args.debug:
                        raw = bytes(civ._rx)
                        print(
                            f"  RX leftover({len(raw)}) {raw.hex() or '-'}",
                            file=sys.stderr,
                        )
                if i + 1 < args.count:
                    time.sleep(args.interval)
            print(f"OK  {ok}/{args.count} polls", flush=True)
            return 0 if ok else 1

    except CivTxBlockedError as e:
        print(f"BLOCKED {e}", file=sys.stderr)
        return 3
    except (CivTimeoutError, CivProtocolError, ValueError) as e:
        print(f"FAIL {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"FAIL {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
