"""Live / replay remote FFT + waterfall — DE-9 head-link spectrum (not IF/IQ SDR).

Plots the **160 × uint16** FFT at body frame byte 48 (stock-head family) with
a scrolling waterfall.

Y units (``--units``):
  raw   — wire uint16 as-is
  log   — log1p(raw)
  dbm   — GSOC display dB proxy ``y = −110 + 1.2 × (int16)bin`` (see
          ``display_db_proxy`` in ``fft_bench.py``). Vendor UI scale, **not**
          live-calibrated absolute dBm (**O2** open per control-spec).

Modes:
  --port …     Live fake-head TX/RX (stock head UNPLUGGED; same wiring as
               live_head.py). On-screen buttons for mode / tune / step /
               volume / ATT / band; optional keyboard shortcuts still work.
  --replay …   Cycle CRC-ok body .hex/.bin fixtures (offline / blog).

  --headless --save-png OUT.png   One-shot figure (no GUI); works with --replay.

Requires: pyqtgraph, PyQt6|PySide6|PyQt5, numpy, Pillow (optional tooling —
not needed for selftest).

See docs/bench-setup.md and docs/verification.md (absolute dBm **O2** open).
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path


from g90_codec import BODY_FRAME_LEN, fft_fingerprint  # noqa: E402
from g90_control.head import HeadControl  # noqa: E402
from g90_control.session import LiveHeadSession, resolve_port  # noqa: E402

_N_BINS = 160
_MODES = ("LSB", "USB", "CW", "AM")
_UNITS = ("raw", "log", "dbm")
_FREQ_MIN = 500_000
_FREQ_MAX = 30_000_000
_VOL_MIN = 0
_VOL_MAX = 28
# Tuning step ladder (Hz) — +/- cycles this list.
_STEP_HZ = (100, 500, 1_000, 2_500, 5_000, 10_000, 25_000, 100_000)
# Amateur HF band edges for PgUp/PgDn / Home/End.
_BANDS = (
    ("160m", 1_800_000, 2_000_000),
    ("80m", 3_500_000, 4_000_000),
    ("60m", 5_330_000, 5_405_000),
    ("40m", 7_000_000, 7_300_000),
    ("30m", 10_100_000, 10_150_000),
    ("20m", 14_000_000, 14_350_000),
    ("17m", 18_068_000, 18_168_000),
    ("15m", 21_000_000, 21_450_000),
    ("12m", 24_890_000, 24_990_000),
    ("10m", 28_000_000, 29_700_000),
)
_MODE_FILTER = {
    "LSB": (2700, 200),
    "USB": (2700, 200),
    "CW": (500, 200),
    "AM": (3000, 200),
}
# Community / wfview: ~48 kHz-wide remote scope → ±24 kHz around VFO.
_SPAN_HZ = 48_000.0
_HZ_PER_BIN = _SPAN_HZ / _N_BINS
_WF_ROWS = 240
# GSOC display dB proxy: −110 + 1.2×(int16)bin (matches display_db_proxy).
_GSOC_DBM_SLOPE = 1.2
_GSOC_DBM_OFFSET = -110.0
# GSOC empty/guard prefill — useful Y-axis floor hint only.
_GSOC_DBM_FLOOR = -145.0
# Fixed scope/waterfall panes (no frame-to-frame autoscale).
_FIXED_Y = {
    "dbm": (_GSOC_DBM_FLOOR, -40.0),
    "log": (0.0, 6.0),  # log1p covers raw up to ~400
    "raw": (0.0, 80.0),
}


def _require_gui_deps() -> None:
    try:
        import numpy  # noqa: F401
        import pyqtgraph  # noqa: F401
    except ImportError as e:
        raise SystemExit(
            "live_fft.py needs: python3 -m pip install pyqtgraph numpy "
            "PyQt6 Pillow\n"
            f"Missing: {e}"
        ) from e
    # Qt binding (any one).
    try:
        import PyQt6  # noqa: F401
    except ImportError:
        try:
            import PySide6  # noqa: F401
        except ImportError:
            try:
                import PyQt5  # noqa: F401
            except ImportError as e:
                raise SystemExit(
                    "need a Qt binding: pip install PyQt6 (or PySide6 / PyQt5)"
                ) from e


def _bins_u16(fft: bytes) -> list[int]:
    n = min(len(fft) // 2, _N_BINS)
    return list(struct.unpack_from(f"<{n}H", fft, 0))


def _bins_i16(fft: bytes) -> list[int]:
    """Wire halfwords as signed — matches GSOC ``ldrsh`` remote draw path."""
    n = min(len(fft) // 2, _N_BINS)
    return list(struct.unpack_from(f"<{n}h", fft, 0))


def _display_y(bins: list[int], *, units: str):
    """Map wire bins to plot Y.

    ``dbm`` applies the GSOC remote affine on **signed** bin values. Pass
    ``_bins_i16`` (or u16 values that fit in int16) when ``units=='dbm'``.
    """
    import numpy as np

    a = np.asarray(bins, dtype=np.float64)
    if units == "log":
        return np.log1p(np.maximum(a, 0.0))
    if units == "dbm":
        return _GSOC_DBM_OFFSET + _GSOC_DBM_SLOPE * a
    return a


def _y_label(units: str) -> str:
    if units == "dbm":
        return "GSOC display dB (−110+1.2×i16) — not live-cal dBm"
    if units == "log":
        return "log1p(raw)"
    return "raw uint16"


def _window_title(units: str) -> str:
    if units == "dbm":
        return "G90 remote FFT (DE-9) — GSOC display dB proxy (not live-cal)"
    return "G90 remote FFT (DE-9) — not dBm / not IQ SDR"


def _y_limits(units: str, ymin: float | None, ymax: float | None) -> tuple[float, float]:
    lo, hi = _FIXED_Y[units]
    if ymin is not None:
        lo = ymin
    if ymax is not None:
        hi = ymax
    if hi <= lo:
        hi = lo + 1.0
    return lo, hi


def _offset_hz() -> list[float]:
    # Bin 80 ≈ VFO center for a 160-bin / 48 kHz span.
    mid = (_N_BINS - 1) / 2.0
    return [(i - mid) * _HZ_PER_BIN for i in range(_N_BINS)]


def _load_frame(path: Path) -> bytes:
    raw = path.read_bytes()
    if path.suffix.lower() == ".hex":
        text = "".join(raw.decode("ascii", errors="ignore").split())
        return bytes.fromhex(text)
    return raw


def _iter_replay_paths(args: list[str]) -> list[Path]:
    paths: list[Path] = []
    for item in args:
        p = Path(item)
        if p.is_dir():
            paths.extend(sorted(p.glob("body_*.hex")))
            paths.extend(sorted(p.glob("body_*.bin")))
        elif p.is_file():
            paths.append(p)
        else:
            raise SystemExit(f"replay path not found: {p}")
    seen: set[Path] = set()
    out: list[Path] = []
    for p in paths:
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        out.append(p)
    if not out:
        raise SystemExit("no body_*.hex / body_*.bin files for --replay")
    return out


def _decode_bodies(ctrl: HeadControl, paths: list[Path]) -> list[tuple[Path, object]]:
    bodies: list[tuple[Path, object]] = []
    for path in paths:
        frame = _load_frame(path)
        if len(frame) != BODY_FRAME_LEN:
            print(
                f"skip {path.name}: len={len(frame)} (want {BODY_FRAME_LEN})",
                file=sys.stderr,
            )
            continue
        body = ctrl.decode_body(frame)
        if not body.crc_ok:
            print(f"skip {path.name}: CRC fail", file=sys.stderr)
            continue
        bodies.append((path, body))
    if not bodies:
        raise SystemExit("no CRC-ok body frames in replay set")
    return bodies


def _chrome(
    *,
    mode: str,
    freq_hz: int,
    s_meter: int,
    po: int,
    fp: tuple[int, int, int],
    units: str = "raw",
    volume: int | None = None,
    step_hz: int | None = None,
    att_mode: int | None = None,
    extra: str = "",
) -> str:
    lo = (freq_hz - _SPAN_HZ / 2) / 1e3
    hi = (freq_hz + _SPAN_HZ / 2) / 1e3
    peak_raw, peak_bin = fp[1], fp[2]
    if units == "dbm":
        # fp peak is from unsigned fingerprint; re-express via signed affine.
        peak_i16 = peak_raw if peak_raw < 0x8000 else peak_raw - 0x10000
        peak_y = _GSOC_DBM_OFFSET + _GSOC_DBM_SLOPE * peak_i16
        peak_s = f"peak={peak_y:.1f} dB@{peak_bin}"
    else:
        peak_s = f"peak={peak_raw}@{peak_bin}"
    bits = [
        f"{mode}",
        f"{freq_hz / 1e6:.3f} MHz",
        f"span ~{lo:.0f}–{hi:.0f} kHz",
        f"S={s_meter} Po={po}",
        peak_s,
    ]
    if volume is not None:
        bits.append(f"vol={volume}")
    if step_hz is not None:
        if step_hz >= 1000:
            bits.append(f"step={step_hz/1000:g}k")
        else:
            bits.append(f"step={step_hz}Hz")
    if att_mode is not None:
        att_name = ("none", "pre", "att")[att_mode] if att_mode in (0, 1, 2) else str(att_mode)
        bits.append(f"ATT={att_name}")
    if units == "dbm":
        bits.append("[display dB proxy]")
    base = "  ".join(bits)
    return f"{base}  |  {extra}" if extra else base


def _clamp_freq(hz: int) -> int:
    return max(_FREQ_MIN, min(_FREQ_MAX, hz))


def _band_index(freq_hz: int) -> int:
    for i, (_n, lo, hi) in enumerate(_BANDS):
        if lo <= freq_hz <= hi:
            return i
    # Nearest band by center.
    best_i, best_d = 0, 10**12
    for i, (_n, lo, hi) in enumerate(_BANDS):
        mid = (lo + hi) // 2
        d = abs(freq_hz - mid)
        if d < best_d:
            best_i, best_d = i, d
    return best_i


def _apply_mode_filters(state: dict) -> None:
    hi, lo = _MODE_FILTER.get(state["mode"], (2700, 200))
    state["filter_high"] = hi
    state["filter_low"] = lo


def _frame_bins(fft: bytes, units: str) -> list[int]:
    return _bins_i16(fft) if units == "dbm" else _bins_u16(fft)


def _save_png_numpy(
    bins: list[int],
    title: str,
    out: Path,
    *,
    units: str,
    ymin: float | None = None,
    ymax: float | None = None,
    waterfall: object | None = None,
) -> None:
    """Headless PNG via Pillow — no Qt display required."""
    import numpy as np
    from PIL import Image, ImageDraw

    y = _display_y(bins, units=units)
    y_lo, y_hi = _y_limits(units, ymin, ymax)
    w, h_spec, h_wf = 800, 160, 240
    img = Image.new("RGB", (w, h_spec + h_wf + 28), (18, 22, 28))
    draw = ImageDraw.Draw(img)
    draw.text((8, 6), title[:110], fill=(200, 210, 220))

    def _row_to_rgb(row: np.ndarray) -> np.ndarray:
        span = (y_hi - y_lo) or 1.0
        t = np.clip((row - y_lo) / span, 0.0, 1.0)
        # Simple teal→yellow ramp (not claiming absolute dBm).
        r = (40 + 200 * t).astype(np.uint8)
        g = (80 + 150 * t).astype(np.uint8)
        b = (100 + 40 * (1 - t)).astype(np.uint8)
        return np.stack([r, g, b], axis=-1)

    # Spectrum as filled polyline (fixed Y).
    yspan = (y_hi - y_lo) or 1.0
    pts = []
    for i, v in enumerate(y):
        x = int(i * (w - 1) / max(len(y) - 1, 1))
        py = 28 + h_spec - 1 - int((float(v) - y_lo) / yspan * (h_spec - 1))
        py = max(28, min(28 + h_spec - 1, py))
        pts.append((x, py))
    if len(pts) >= 2:
        draw.line(pts, fill=(80, 200, 220), width=2)

    # Waterfall block.
    if waterfall is not None:
        wf = np.asarray(waterfall, dtype=np.float64)
    else:
        wf = np.tile(y, (_WF_ROWS, 1))
    # Resize rows to image width via nearest for speed.
    wf_img = np.zeros((h_wf, w, 3), dtype=np.uint8)
    for i in range(h_wf):
        src = wf[int(i * wf.shape[0] / h_wf)]
        # Stretch bins → width.
        idx = (np.linspace(0, len(src) - 1, w)).astype(int)
        wf_img[i] = _row_to_rgb(src[idx])
    img.paste(Image.fromarray(wf_img, "RGB"), (0, 28 + h_spec))

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print(f"wrote {out}", file=sys.stderr)


class _Waterfall:
    def __init__(self, rows: int = _WF_ROWS) -> None:
        import numpy as np

        self.rows = rows
        self.data = np.zeros((rows, _N_BINS), dtype=np.float64)

    def push(self, y) -> None:
        self.data[:-1] = self.data[1:]
        self.data[-1] = y


def _nudge_freq(state: dict, delta_hz: int) -> None:
    state["freq_hz"] = _clamp_freq(int(state["freq_hz"]) + int(delta_hz))


def _set_mode(state: dict, mode: str) -> None:
    state["mode"] = mode
    _apply_mode_filters(state)


def _cycle_mode(state: dict, *, reverse: bool = False) -> None:
    i = _MODES.index(state["mode"]) if state["mode"] in _MODES else 0
    state["mode"] = _MODES[(i - 1 if reverse else i + 1) % len(_MODES)]
    _apply_mode_filters(state)


def _nudge_volume(state: dict, delta: int) -> None:
    state["volume"] = max(_VOL_MIN, min(_VOL_MAX, int(state["volume"]) + delta))


def _nudge_step(state: dict, delta_idx: int) -> None:
    try:
        i = _STEP_HZ.index(state["step_hz"])
    except ValueError:
        i = 2
    state["step_hz"] = _STEP_HZ[max(0, min(len(_STEP_HZ) - 1, i + delta_idx))]


def _band_prev(state: dict) -> None:
    i = max(0, _band_index(state["freq_hz"]) - 1)
    state["freq_hz"] = _BANDS[i][1]


def _band_next(state: dict) -> None:
    i = _band_index(state["freq_hz"])
    _n, lo, _hi = _BANDS[i]
    if state["freq_hz"] >= lo and i + 1 < len(_BANDS):
        i += 1
    state["freq_hz"] = _BANDS[i][1]


def _band_home(state: dict) -> None:
    state["freq_hz"] = _BANDS[_band_index(state["freq_hz"])][1]


def _band_end(state: dict) -> None:
    state["freq_hz"] = _BANDS[_band_index(state["freq_hz"])][2]


def _cycle_att(state: dict) -> None:
    state["att_mode"] = (int(state.get("att_mode", 0)) + 1) % 3


def _fmt_step(step_hz: int) -> str:
    if step_hz >= 1000:
        return f"{step_hz / 1000:g} kHz"
    return f"{step_hz} Hz"


def _build_control_panel(state: dict, QtWidgets, *, on_quit):
    """On-screen buttons for live fake-head controls. Returns (widget, sync_fn)."""
    panel = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(panel)
    root.setContentsMargins(6, 4, 6, 6)
    root.setSpacing(4)

    def _btn(text: str, slot, *, checkable: bool = False, width: int | None = None):
        b = QtWidgets.QPushButton(text)
        b.setCheckable(checkable)
        if width is not None:
            b.setMinimumWidth(width)
        b.setMinimumHeight(34)
        b.clicked.connect(slot)
        return b

    # --- Mode row ---
    row_mode = QtWidgets.QHBoxLayout()
    row_mode.addWidget(QtWidgets.QLabel("Mode"))
    mode_group = QtWidgets.QButtonGroup(panel)
    mode_group.setExclusive(True)
    mode_btns: dict[str, object] = {}
    for m in _MODES:

        def _mk_mode(mode=m):
            return lambda _checked=False: _set_mode(state, mode)

        b = _btn(m, _mk_mode(), checkable=True, width=56)
        mode_group.addButton(b)
        mode_btns[m] = b
        row_mode.addWidget(b)
    row_mode.addStretch(1)
    att_btn = _btn("ATT: none", lambda: _cycle_att(state), width=100)
    row_mode.addWidget(att_btn)
    root.addLayout(row_mode)

    # --- Tune row ---
    row_tune = QtWidgets.QHBoxLayout()
    row_tune.addWidget(QtWidgets.QLabel("Tune"))
    row_tune.addWidget(_btn("◀ Band", lambda: _band_prev(state), width=72))
    row_tune.addWidget(_btn("−100k", lambda: _nudge_freq(state, -100_000), width=56))
    row_tune.addWidget(
        _btn("−10×", lambda: _nudge_freq(state, -10 * int(state["step_hz"])), width=48)
    )
    row_tune.addWidget(
        _btn("−", lambda: _nudge_freq(state, -int(state["step_hz"])), width=40)
    )
    row_tune.addWidget(
        _btn("+", lambda: _nudge_freq(state, int(state["step_hz"])), width=40)
    )
    row_tune.addWidget(
        _btn("+10×", lambda: _nudge_freq(state, 10 * int(state["step_hz"])), width=48)
    )
    row_tune.addWidget(_btn("+100k", lambda: _nudge_freq(state, 100_000), width=56))
    row_tune.addWidget(_btn("Band ▶", lambda: _band_next(state), width=72))
    row_tune.addWidget(_btn("Lo", lambda: _band_home(state), width=40))
    row_tune.addWidget(_btn("Hi", lambda: _band_end(state), width=40))
    row_tune.addStretch(1)
    root.addLayout(row_tune)

    # --- Step / volume row ---
    row_misc = QtWidgets.QHBoxLayout()
    row_misc.addWidget(QtWidgets.QLabel("Step"))
    row_misc.addWidget(_btn("−", lambda: _nudge_step(state, -1), width=36))
    step_lbl = QtWidgets.QLabel(_fmt_step(int(state["step_hz"])))
    step_lbl.setMinimumWidth(72)
    row_misc.addWidget(step_lbl)
    row_misc.addWidget(_btn("+", lambda: _nudge_step(state, 1), width=36))
    row_misc.addSpacing(16)
    row_misc.addWidget(QtWidgets.QLabel("Vol"))
    row_misc.addWidget(_btn("−", lambda: _nudge_volume(state, -1), width=36))
    vol_lbl = QtWidgets.QLabel(str(int(state["volume"])))
    vol_lbl.setMinimumWidth(28)
    row_misc.addWidget(vol_lbl)
    row_misc.addWidget(_btn("+", lambda: _nudge_volume(state, 1), width=36))
    row_misc.addStretch(1)
    row_misc.addWidget(_btn("Quit", on_quit, width=72))
    root.addLayout(row_misc)

    att_names = ("none", "pre", "att")

    def sync() -> None:
        m = state.get("mode")
        for name, b in mode_btns.items():
            b.setChecked(name == m)
        step_lbl.setText(_fmt_step(int(state["step_hz"])))
        vol_lbl.setText(str(int(state["volume"])))
        ai = int(state.get("att_mode", 0)) % 3
        att_btn.setText(f"ATT: {att_names[ai]}")

    sync()
    return panel, sync


def _run_gui(
    *,
    source: str,
    get_frame,
    interval_ms: float,
    units: str,
    state: dict,
    on_key=None,
    save_png: Path | None = None,
    ymin: float | None = None,
    ymax: float | None = None,
    autoscale: bool = False,
) -> int:
    """Interactive spectrum + waterfall via pyqtgraph (+ on-screen controls)."""
    import numpy as np
    import pyqtgraph as pg
    from pyqtgraph.Qt import QtCore, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    pg.setConfigOptions(antialias=True, background="#12161c", foreground="#c8d2dc")

    y_lo, y_hi = _y_limits(units, ymin, ymax)
    show_controls = "volume" in state and "step_hz" in state

    shell = QtWidgets.QWidget()
    shell.setWindowTitle(_window_title(units))
    shell.resize(980, 760 if show_controls else 640)
    layout = QtWidgets.QVBoxLayout(shell)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    status = QtWidgets.QLabel("")
    status.setWordWrap(True)
    status.setStyleSheet(
        "QLabel { background:#12161c; color:#c8d2dc; padding:6px 10px; "
        "font-family: Menlo, Consolas, monospace; font-size: 12px; }"
    )
    layout.addWidget(status)

    plots = pg.GraphicsLayoutWidget()
    layout.addWidget(plots, stretch=1)

    plot = plots.addPlot()
    plot.showGrid(x=True, y=True, alpha=0.25)
    plot.setLabel("left", _y_label(units))
    plot.setLabel("bottom", "offset from VFO (kHz) — ~48 kHz span")
    plot.setXRange(-_SPAN_HZ / 2000.0, _SPAN_HZ / 2000.0)
    if not autoscale:
        plot.setYRange(y_lo, y_hi, padding=0)
        plot.disableAutoRange(axis="y")
    curve = plot.plot(pen=pg.mkPen("#50c8dc", width=2))
    vline = pg.InfiniteLine(
        pos=0.0,
        angle=90,
        pen=pg.mkPen("#888", style=QtCore.Qt.PenStyle.DashLine),
    )
    plot.addItem(vline)

    plots.nextRow()
    wf_plot = plots.addPlot()
    wf_plot.setLabel("left", "time →")
    wf_plot.setLabel("bottom", "bin")
    wf_plot.setXRange(0, _N_BINS)
    wf_plot.setYRange(0, _WF_ROWS)
    wf_plot.invertY(True)
    img = pg.ImageItem()
    wf_plot.addItem(img)
    lut = np.zeros((256, 3), dtype=np.ubyte)
    for i in range(256):
        t = i / 255.0
        lut[i] = (int(40 + 200 * t), int(80 + 150 * t), int(100 + 40 * (1 - t)))
    img.setLookupTable(lut)

    sync_controls = None
    if show_controls:

        def _quit() -> None:
            state["quit"] = True
            shell.close()

        controls, sync_controls = _build_control_panel(
            state, QtWidgets, on_quit=_quit
        )
        controls.setStyleSheet(
            "QWidget { background:#1a2030; color:#c8d2dc; }"
            "QPushButton { background:#2a3348; color:#e8eef6; border:1px solid #3d4a63; "
            "border-radius:4px; padding:4px 8px; }"
            "QPushButton:checked { background:#3d7ea6; border-color:#50c8dc; }"
            "QPushButton:hover { background:#354058; }"
            "QLabel { color:#a8b4c4; }"
        )
        layout.addWidget(controls)

    wf = _Waterfall()
    x_khz = np.asarray(_offset_hz()) / 1000.0
    po_abort = {"v": False}

    def _apply(bins: list[int], chrome: str) -> None:
        y = _display_y(bins, units=units)
        wf.push(y)
        curve.setData(x_khz, y)
        if autoscale:
            ymin_a = float(y.min()) if len(y) else y_lo
            ymax_a = float(y.max()) if len(y) else y_hi
            if units == "dbm":
                ymin_a = min(ymin_a, _GSOC_DBM_FLOOR)
                pad = max(5.0, 0.1 * (ymax_a - ymin_a))
                plot.setYRange(ymin_a - pad * 0.2, ymax_a + pad)
            else:
                plot.setYRange(0, ymax_a * 1.15 if ymax_a > 0 else 1.0)
            img.setImage(
                wf.data.T,
                levels=(float(wf.data.min()), float(wf.data.max()) or 1.0),
            )
        else:
            plot.setYRange(y_lo, y_hi, padding=0)
            img.setImage(wf.data.T, levels=(y_lo, y_hi))
        status.setText(chrome)
        if sync_controls is not None:
            sync_controls()

    def _tick() -> None:
        if state.get("quit") or po_abort["v"]:
            shell.close()
            return
        try:
            result = get_frame()
        except Exception as exc:  # noqa: BLE001
            status.setText(f"error: {exc}")
            return
        if result is None:
            status.setText("waiting for CRC-ok body…")
            return
        bins, chrome, abort = result
        if abort:
            po_abort["v"] = True
            status.setText(chrome)
            print(chrome, file=sys.stderr)
            shell.close()
            return
        _apply(bins, chrome)

    # Seed
    first = get_frame()
    if first is None:
        raise SystemExit("no first frame for GUI")
    bins0, chrome0, abort0 = first
    if abort0:
        raise SystemExit(chrome0)
    _apply(bins0, chrome0)

    if save_png is not None:
        _save_png_numpy(
            bins0,
            chrome0,
            save_png,
            units=units,
            ymin=ymin,
            ymax=ymax,
            waterfall=wf.data,
        )

    def _key(evt) -> None:
        key = evt.key()
        mods = evt.modifiers()
        if on_key is not None:
            on_key(key, mods, QtCore)
        if key in (QtCore.Qt.Key.Key_Q, QtCore.Qt.Key.Key_Escape):
            state["quit"] = True
            shell.close()

    shell.keyPressEvent = _key  # type: ignore[method-assign]
    shell.show()
    timer = QtCore.QTimer()
    timer.timeout.connect(_tick)
    timer.start(int(max(interval_ms, 15.0)))
    scale = "autoscale" if autoscale else f"Y=[{y_lo:g},{y_hi:g}]"
    print(
        f"{source}  units={units}  {scale}  (RX-only; abort if Po≠0)",
        file=sys.stderr,
    )
    app.exec()
    return 2 if po_abort["v"] else 0


def _run_replay_gui(
    bodies: list[tuple[Path, object]],
    *,
    units: str,
    interval_ms: float,
    mode: str,
    freq_hz: int,
    save_png: Path | None,
    ymin: float | None = None,
    ymax: float | None = None,
    autoscale: bool = False,
) -> int:
    idx = {"i": 0}
    state = {"quit": False}

    def get_frame():
        path, body = bodies[idx["i"] % len(bodies)]
        idx["i"] += 1
        bins = _frame_bins(body.fft, units)
        fp = fft_fingerprint(body.fft)
        st = body.status
        chrome = _chrome(
            mode=mode,
            freq_hz=freq_hz,
            s_meter=st.s_meter,
            po=st.po,
            fp=fp,
            units=units,
            extra=f"replay {path.name}",
        )
        return bins, chrome, False

    return _run_gui(
        source=f"replay {len(bodies)} frames",
        get_frame=get_frame,
        interval_ms=interval_ms,
        units=units,
        state=state,
        save_png=save_png,
        ymin=ymin,
        ymax=ymax,
        autoscale=autoscale,
    )


def _run_live(
    port: str,
    *,
    profile: str,
    baud: int,
    timeout_s: float,
    interval_ms: float,
    mode0: str,
    freq0: int,
    filter_high: int,
    filter_low: int,
    units: str,
    save_png: Path | None,
    headless: bool,
    ymin: float | None = None,
    ymax: float | None = None,
    autoscale: bool = False,
) -> int:
    # Default step: 1 kHz, or 100 Hz if started in CW.
    step0 = 100 if mode0 == "CW" else 1_000
    if step0 not in _STEP_HZ:
        step0 = 1_000
    hi0, lo0 = _MODE_FILTER.get(mode0, (filter_high, filter_low))
    # CLI filter wins if caller overrode defaults for this mode.
    if filter_high != hi0 or filter_low != lo0:
        # Keep explicit CLI filters until mode changes.
        use_hi, use_lo = filter_high, filter_low
    else:
        use_hi, use_lo = hi0, lo0
    state = {
        "mode": mode0,
        "freq_hz": freq0,
        "quit": False,
        "volume": 8,
        "step_hz": step0,
        "att_mode": 0,
        "filter_high": use_hi,
        "filter_low": use_lo,
    }

    with LiveHeadSession(
        port, baud=baud, profile=profile, timeout_s=timeout_s
    ) as sess:

        def _exchange():
            return sess.exchange(
                mode=state["mode"],
                freq_hz=state["freq_hz"],
                filter_high_hz=state["filter_high"],
                filter_low_hz=state["filter_low"],
                volume=state["volume"],
                att_mode=state["att_mode"],
                rf_power=1,
            )

        er = _exchange()
        if er.body is None or not er.body.crc_ok:
            raise SystemExit("no CRC-ok body reply on live port")
        if er.body.status.po != 0:
            raise SystemExit(
                f"body Po meter non-zero ({er.body.status.po}) — aborting (no-TX policy)"
            )

        if headless:
            assert save_png is not None
            bins = _frame_bins(er.body.fft, units)
            fp = fft_fingerprint(er.body.fft)
            chrome = _chrome(
                mode=state["mode"],
                freq_hz=state["freq_hz"],
                s_meter=er.body.status.s_meter,
                po=er.body.status.po,
                fp=fp,
                units=units,
                volume=state["volume"],
                step_hz=state["step_hz"],
                att_mode=state["att_mode"],
                extra="live",
            )
            _save_png_numpy(
                bins, chrome, save_png, units=units, ymin=ymin, ymax=ymax
            )
            return 0

        primed = {"body": er.body, "used": False}

        def get_frame():
            if not primed["used"]:
                primed["used"] = True
                body = primed["body"]
            else:
                er2 = _exchange()
                if er2.body is None or not er2.body.crc_ok:
                    return None
                body = er2.body
            if body.status.po != 0:
                return (
                    _frame_bins(body.fft, units),
                    f"ABORT: body Po={body.status.po} (no-TX policy)",
                    True,
                )
            bins = _frame_bins(body.fft, units)
            fp = fft_fingerprint(body.fft)
            chrome = _chrome(
                mode=state["mode"],
                freq_hz=state["freq_hz"],
                s_meter=body.status.s_meter,
                po=body.status.po,
                fp=fp,
                units=units,
                volume=state["volume"],
                step_hz=state["step_hz"],
                att_mode=state["att_mode"],
            )
            return bins, chrome, False

        def on_key(key, mods, QtCore) -> None:
            # Optional shortcuts; primary UX is the on-screen button bar.
            shift = bool(mods & QtCore.Qt.KeyboardModifier.ShiftModifier)
            ctrl = bool(
                mods
                & (
                    QtCore.Qt.KeyboardModifier.ControlModifier
                    | QtCore.Qt.KeyboardModifier.MetaModifier
                )
            )
            mode_keys = {
                QtCore.Qt.Key.Key_1: "LSB",
                QtCore.Qt.Key.Key_2: "USB",
                QtCore.Qt.Key.Key_3: "CW",
                QtCore.Qt.Key.Key_4: "AM",
            }
            if key in mode_keys:
                _set_mode(state, mode_keys[key])
                return
            if key == QtCore.Qt.Key.Key_M:
                _cycle_mode(state, reverse=shift)
                return
            if key in (QtCore.Qt.Key.Key_Comma, QtCore.Qt.Key.Key_Minus) or (
                key == QtCore.Qt.Key.Key_V and not shift
            ):
                _nudge_volume(state, -1)
                return
            if key in (QtCore.Qt.Key.Key_Period, QtCore.Qt.Key.Key_Equal) or (
                key == QtCore.Qt.Key.Key_V and shift
            ):
                _nudge_volume(state, 1)
                return
            if key == QtCore.Qt.Key.Key_A:
                _cycle_att(state)
                return
            if key in (
                QtCore.Qt.Key.Key_Plus,
                QtCore.Qt.Key.Key_BracketRight,
                QtCore.Qt.Key.Key_Up,
            ):
                _nudge_step(state, 1)
                return
            if key in (QtCore.Qt.Key.Key_BracketLeft, QtCore.Qt.Key.Key_Down):
                _nudge_step(state, -1)
                return
            if key == QtCore.Qt.Key.Key_PageUp:
                _band_prev(state)
                return
            if key == QtCore.Qt.Key.Key_PageDown:
                _band_next(state)
                return
            if key == QtCore.Qt.Key.Key_Home:
                _band_home(state)
                return
            if key == QtCore.Qt.Key.Key_End:
                _band_end(state)
                return
            if key in (QtCore.Qt.Key.Key_Left, QtCore.Qt.Key.Key_Right):
                step = int(state["step_hz"])
                if ctrl:
                    step = 100_000
                elif shift:
                    step = min(1_000_000, step * 10)
                _nudge_freq(
                    state, step if key == QtCore.Qt.Key.Key_Right else -step
                )

        return _run_gui(
            source=f"live-fft {port} profile={profile} mode={state['mode']} freq={state['freq_hz']}",
            get_frame=get_frame,
            interval_ms=interval_ms,
            units=units,
            state=state,
            on_key=on_key,
            save_png=save_png,
            ymin=ymin,
            ymax=ymax,
            autoscale=autoscale,
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default=None, help="USB serial (live mode)")
    ap.add_argument(
        "--replay",
        nargs="+",
        metavar="PATH",
        help="body_*.hex/.bin file(s) or directory (offline)",
    )
    ap.add_argument("--profile", default="1.81")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--interval-ms", type=float, default=30.0)
    ap.add_argument("--mode", default="USB")
    ap.add_argument("--freq", type=int, default=14_225_000)
    ap.add_argument("--filter-high", type=int, default=2700)
    ap.add_argument("--filter-low", type=int, default=200)
    ap.add_argument(
        "--units",
        choices=_UNITS,
        default=None,
        help="Y axis: raw | log | dbm (GSOC −110+1.2×i16 display proxy; not live-cal)",
    )
    ap.add_argument(
        "--log",
        action="store_true",
        help="alias for --units log",
    )
    ap.add_argument(
        "--dbm",
        action="store_true",
        help="alias for --units dbm (GSOC display dB proxy; O2 not closed)",
    )
    ap.add_argument(
        "--ymin",
        type=float,
        default=None,
        help="fixed Y min (default: dbm=-145, log=0, raw=0)",
    )
    ap.add_argument(
        "--ymax",
        type=float,
        default=None,
        help="fixed Y max (default: dbm=-40, log=6, raw=80)",
    )
    ap.add_argument(
        "--autoscale",
        action="store_true",
        help="old behavior: rescale Y each frame from data min/max",
    )
    ap.add_argument(
        "--headless",
        action="store_true",
        help="no GUI; requires --save-png",
    )
    ap.add_argument(
        "--save-png",
        type=Path,
        metavar="OUT.png",
        help="write a spectrum+waterfall PNG (Pillow)",
    )
    ap.add_argument("--list-ports", action="store_true")
    args = ap.parse_args(argv)

    if args.list_ports:
        from g90_control.session import candidate_ports

        ports = candidate_ports()
        for p in ports:
            print(p)
        return 0 if ports else 1

    if args.headless and args.save_png is None:
        raise SystemExit("--headless requires --save-png")
    if args.replay is None and args.port is None:
        raise SystemExit("pass --port (live) or --replay PATH…")

    units = args.units
    if args.dbm:
        if units not in (None, "dbm"):
            raise SystemExit("conflicting --units / --dbm")
        units = "dbm"
    if args.log:
        if units not in (None, "log"):
            raise SystemExit("conflicting --units / --log / --dbm")
        units = "log"
    if units is None:
        units = "raw"

    # Headless only needs numpy+Pillow; GUI needs pyqtgraph+Qt.
    if args.headless:
        try:
            import numpy  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError as e:
            raise SystemExit(
                "headless needs: pip install numpy Pillow\n" f"Missing: {e}"
            ) from e
    else:
        _require_gui_deps()

    ctrl = HeadControl(args.profile)

    if args.replay is not None:
        paths = _iter_replay_paths(args.replay)
        bodies = _decode_bodies(ctrl, paths)
        print(
            f"replay {len(bodies)} CRC-ok frame(s) from {len(paths)} path(s)",
            file=sys.stderr,
        )
        if args.headless:
            assert args.save_png is not None
            path, body = bodies[0]
            bins = _frame_bins(body.fft, units)
            fp = fft_fingerprint(body.fft)
            chrome = _chrome(
                mode=args.mode,
                freq_hz=args.freq,
                s_meter=body.status.s_meter,
                po=body.status.po,
                fp=fp,
                units=units,
                extra=f"replay {path.name}",
            )
            _save_png_numpy(
                bins,
                chrome,
                args.save_png,
                units=units,
                ymin=args.ymin,
                ymax=args.ymax,
            )
            return 0
        return _run_replay_gui(
            bodies,
            units=units,
            interval_ms=args.interval_ms,
            mode=args.mode,
            freq_hz=args.freq,
            save_png=args.save_png,
            ymin=args.ymin,
            ymax=args.ymax,
            autoscale=bool(args.autoscale),
        )

    port = resolve_port(args.port)
    return _run_live(
        port,
        profile=args.profile,
        baud=args.baud,
        timeout_s=args.timeout,
        interval_ms=args.interval_ms,
        mode0=args.mode,
        freq0=args.freq,
        filter_high=args.filter_high,
        filter_low=args.filter_low,
        units=units,
        save_png=args.save_png,
        headless=bool(args.headless),
        ymin=args.ymin,
        ymax=args.ymax,
        autoscale=bool(args.autoscale),
    )


if __name__ == "__main__":
    raise SystemExit(main())
