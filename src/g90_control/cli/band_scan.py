"""RX-only HF band scan via fake-head — hunt peaks in S-meter + remote FFT.

Stock head UNPLUGGED; FT232 is the head (same wiring as ``g90-live-head``).
Steps VFO across amateur segments, settles a few body frames per step, and
ranks hits by FFT peak / S-meter vs a noise baseline.

Not a calibrated SNR meter (**O2** open). FFT peak uses wire uint16; optional
``--dbm`` reports GSOC display proxy ``−110+1.2×i16`` for the peak bin.

    g90-band-scan --port /dev/cu.usbserial-XXX
    g90-band-scan --port … --preset cw --top 15
    g90-band-scan --port … --bands 20m,40m --step-hz 2500

See ``docs/bench-setup.md``.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from dataclasses import dataclass

from g90_codec import fft_fingerprint
from g90_control.fft_bench import display_db_proxy
from g90_control.session import LiveHeadSession, candidate_ports, resolve_port

_BANDS_HZ: dict[str, tuple[int, int]] = {
    "160m": (1_800_000, 2_000_000),
    "80m": (3_500_000, 4_000_000),
    "60m": (5_330_000, 5_405_000),
    "40m": (7_000_000, 7_300_000),
    "30m": (10_100_000, 10_150_000),
    "20m": (14_000_000, 14_350_000),
    "17m": (18_068_000, 18_168_000),
    "15m": (21_000_000, 21_450_000),
    "12m": (24_890_000, 24_990_000),
    "10m": (28_000_000, 29_700_000),
}

_CW_SLICES: dict[str, tuple[int, int]] = {
    "80m": (3_500_000, 3_600_000),
    "40m": (7_000_000, 7_125_000),
    "30m": (10_100_000, 10_150_000),
    "20m": (14_000_000, 14_100_000),
    "17m": (18_068_000, 18_110_000),
    "15m": (21_000_000, 21_100_000),
    "12m": (24_890_000, 24_930_000),
    "10m": (28_000_000, 28_100_000),
}

_PRESET_BANDS = {
    "hf": list(_BANDS_HZ.keys()),
    "cw": list(_CW_SLICES.keys()),
    "lean": ["40m", "20m", "15m"],
}


@dataclass(frozen=True)
class Sample:
    freq_hz: int
    band: str
    s_meter: int
    fft_sum: int
    fft_peak: int
    peak_bin: int
    peak_dbm: float
    miss: int


def _peak_dbm(peak_u16: int) -> float:
    return display_db_proxy(peak_u16)


def _freq_grid(lo: int, hi: int, step: int) -> list[int]:
    if step <= 0:
        raise ValueError("step-hz must be > 0")
    out: list[int] = []
    f = lo
    while f <= hi:
        out.append(f)
        f += step
    return out


def _resolve_segments(
    preset: str, bands: list[str] | None
) -> list[tuple[str, int, int]]:
    names = bands if bands else _PRESET_BANDS[preset]
    table = _CW_SLICES if preset == "cw" and not bands else _BANDS_HZ
    if bands and preset == "cw":
        segs = []
        for n in names:
            if n in _CW_SLICES:
                lo, hi = _CW_SLICES[n]
            elif n in _BANDS_HZ:
                lo, hi = _BANDS_HZ[n]
            else:
                raise SystemExit(f"unknown band: {n}")
            segs.append((n, lo, hi))
        return segs
    segs = []
    for n in names:
        if n not in table and n not in _BANDS_HZ:
            raise SystemExit(f"unknown band: {n}")
        lo, hi = table.get(n, _BANDS_HZ[n])
        segs.append((n, lo, hi))
    return segs


def _settle(
    sess: LiveHeadSession,
    *,
    freq_hz: int,
    mode: str,
    filter_high: int,
    filter_low: int,
    settle: int,
    interval_s: float,
) -> Sample | None:
    s_vals: list[int] = []
    peaks: list[int] = []
    sums: list[int] = []
    bins: list[int] = []
    miss = 0
    for _ in range(settle):
        er = sess.exchange(
            mode=mode,
            freq_hz=freq_hz,
            filter_high_hz=filter_high,
            filter_low_hz=filter_low,
            rf_power=1,
        )
        if er.body is None or not er.body.crc_ok:
            miss += 1
            time.sleep(interval_s)
            continue
        if er.body.status.po != 0:
            raise RuntimeError(
                f"body Po={er.body.status.po} at {freq_hz} Hz — abort (no-TX)"
            )
        st = er.body.status
        fp = fft_fingerprint(er.body.fft)
        s_vals.append(st.s_meter)
        sums.append(fp[0])
        peaks.append(fp[1])
        bins.append(fp[2])
        time.sleep(interval_s)
    if not peaks:
        return None
    peak = int(statistics.median(peaks))
    peak_bin = bins[peaks.index(max(peaks))]
    for p, b in zip(peaks, bins):
        if p == peak:
            peak_bin = b
            break
    return Sample(
        freq_hz=freq_hz,
        band="",
        s_meter=int(statistics.median(s_vals)),
        fft_sum=int(statistics.median(sums)),
        fft_peak=peak,
        peak_bin=peak_bin,
        peak_dbm=_peak_dbm(peak),
        miss=miss,
    )


def _baseline(samples: list[Sample]) -> tuple[float, float, float, float]:
    if not samples:
        return 0.0, 0.0, 0.0, 0.0
    s = [x.s_meter for x in samples]
    p = [x.fft_peak for x in samples]
    s_med = statistics.median(s)
    p_med = statistics.median(p)
    s_mad = statistics.median([abs(v - s_med) for v in s]) or 1.0
    p_mad = statistics.median([abs(v - p_med) for v in p]) or 1.0
    return float(s_med), float(s_mad), float(p_med), float(p_mad)


def _score(sample: Sample, s_med: float, s_mad: float, p_med: float, p_mad: float) -> float:
    zp = (sample.fft_peak - p_med) / p_mad
    zs = (sample.s_meter - s_med) / s_mad
    return 0.75 * zp + 0.25 * zs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default=None)
    ap.add_argument("--profile", default="1.81")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--mode", default="CW", help="head mode (CW/USB/LSB/…)")
    ap.add_argument("--filter-high", type=int, default=500)
    ap.add_argument("--filter-low", type=int, default=200)
    ap.add_argument(
        "--preset",
        choices=sorted(_PRESET_BANDS),
        default="lean",
        help="freq set: lean=40/20/15 full; cw=CW slices; hf=all HF",
    )
    ap.add_argument(
        "--bands",
        default=None,
        help="comma list overriding preset (e.g. 20m,40m,30m)",
    )
    ap.add_argument("--step-hz", type=int, default=10_000)
    ap.add_argument("--settle", type=int, default=3, help="frames per step")
    ap.add_argument("--interval-ms", type=float, default=30.0)
    ap.add_argument("--top", type=int, default=12, help="print top N hits")
    ap.add_argument(
        "--min-score",
        type=float,
        default=2.5,
        help="hit threshold in robust z-units (peak/S vs scan median)",
    )
    ap.add_argument("--list-ports", action="store_true")
    args = ap.parse_args(argv)

    if args.list_ports:
        ports = candidate_ports()
        for p in ports:
            print(p)
        return 0 if ports else 1

    band_names = (
        [b.strip() for b in args.bands.split(",") if b.strip()]
        if args.bands
        else None
    )
    segments = _resolve_segments(args.preset, band_names)
    grid: list[tuple[str, int]] = []
    for name, lo, hi in segments:
        for f in _freq_grid(lo, hi, args.step_hz):
            grid.append((name, f))

    port = resolve_port(args.port)
    interval_s = args.interval_ms / 1000.0
    print(
        f"band-scan {port} profile={args.profile} mode={args.mode} "
        f"preset={args.preset} steps={len(grid)} step={args.step_hz}Hz "
        f"settle={args.settle}",
        flush=True,
    )
    print(
        "confirm: stock head UNPLUGGED; TXD→2 RXD→3 GND→5; 3.3V; RX-only",
        flush=True,
    )

    samples: list[Sample] = []
    t0 = time.monotonic()
    with LiveHeadSession(
        port, baud=args.baud, profile=args.profile, timeout_s=args.timeout
    ) as sess:
        for i, (band, freq) in enumerate(grid, 1):
            try:
                samp = _settle(
                    sess,
                    freq_hz=freq,
                    mode=args.mode,
                    filter_high=args.filter_high,
                    filter_low=args.filter_low,
                    settle=args.settle,
                    interval_s=interval_s,
                )
            except RuntimeError as e:
                print(f"ABORT: {e}", file=sys.stderr)
                return 2
            if samp is None:
                print(f"[{i}/{len(grid)}] {band} {freq/1e6:.3f} miss", flush=True)
                continue
            samp = Sample(
                freq_hz=samp.freq_hz,
                band=band,
                s_meter=samp.s_meter,
                fft_sum=samp.fft_sum,
                fft_peak=samp.fft_peak,
                peak_bin=samp.peak_bin,
                peak_dbm=samp.peak_dbm,
                miss=samp.miss,
            )
            samples.append(samp)
            if i == 1 or i % 10 == 0 or i == len(grid):
                print(
                    f"[{i}/{len(grid)}] {band} {freq/1e6:.3f}  "
                    f"S={samp.s_meter} peak={samp.fft_peak}@{samp.peak_bin} "
                    f"~{samp.peak_dbm:.0f}dB",
                    flush=True,
                )

    elapsed = time.monotonic() - t0
    if len(samples) < 5:
        print("too few samples for baseline", file=sys.stderr)
        return 1

    s_med, s_mad, p_med, p_mad = _baseline(samples)
    ranked = sorted(
        samples,
        key=lambda s: _score(s, s_med, s_mad, p_med, p_mad),
        reverse=True,
    )
    hits = [s for s in ranked if _score(s, s_med, s_mad, p_med, p_mad) >= args.min_score]

    print(
        f"\n=== scan done: {len(samples)} steps in {elapsed:.1f}s ===",
        flush=True,
    )
    print(
        f"baseline: S≈{s_med:.0f}±{s_mad:.0f}  "
        f"fft_peak≈{p_med:.0f}±{p_mad:.0f}  "
        f"hits≥{args.min_score}: {len(hits)}",
        flush=True,
    )
    print(
        f"{'band':<5} {'MHz':>8} {'S':>4} {'peak':>6} {'bin':>4} "
        f"{'dB~':>6} {'score':>6}",
        flush=True,
    )
    for s in ranked[: max(args.top, 0)]:
        sc = _score(s, s_med, s_mad, p_med, p_mad)
        mark = "*" if sc >= args.min_score else " "
        print(
            f"{mark}{s.band:<4} {s.freq_hz/1e6:8.3f} {s.s_meter:4d} "
            f"{s.fft_peak:6d} {s.peak_bin:4d} {s.peak_dbm:6.1f} {sc:6.2f}",
            flush=True,
        )

    if hits:
        best = hits[0]
        print(
            f"\nbest: {best.band} {best.freq_hz/1e6:.3f} MHz  "
            f"S={best.s_meter} peak={best.fft_peak} ~{best.peak_dbm:.1f} dB "
            f"(GSOC proxy)",
            flush=True,
        )
        print(
            "tune tip: "
            f"g90-live-fft --port {port} --mode {args.mode} "
            f"--freq {best.freq_hz} --dbm",
            flush=True,
        )
    else:
        print(
            "\nno strong hits — try --preset cw --step-hz 2500, "
            "antenna, or lower --min-score",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
