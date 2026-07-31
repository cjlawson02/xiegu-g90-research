"""Fake-head body-effect differential — prove encode without a Y-tap.

Stock head UNPLUGGED; FT232 is the head (same wiring as ``live_head.py``).
Steps mode / filter / VFO / volume / ATT / AGC and reports what changed in
CRC-valid 372-byte body replies (meters/status header + FFT fingerprint).

**PASS gate (default / --extended):** header delta AND FFT delta AND mode-step
move AND freq-step FFT move AND ``Po==0``. Ambient S-meter alone is not enough.

**PASS gate (--controls):** every step CRC-ok, Po=0, never TX-armed, and each
control step's head TX wire differs from the previous step in the expected
bytes (encode applied). Body header/FFT deltas are reported per knob — many
knobs are ears-only.

**RX-only:** never arms PTT / ATU-tune / VOX (``LiveHeadSession`` refuses).

    g90-effect-diff --port /dev/cu.usbserial-XXX
    g90-effect-diff --port … --extended
    g90-effect-diff --port … --controls
    g90-effect-diff --port … --extended --controls \\
      --save captures/effect-diff-181

``--save DIR`` writes every CRC-ok body (``.bin``/``.hex``) plus ``manifest.jsonl``
(step label, header 13–47 nonzeros) — needed to fixture-back the lab
``0x45 0x20`` @32–39 march in header bytes 13–47 (**O1**).

See ``docs/bench-setup.md``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, TextIO


from g90_codec import (  # noqa: E402
    fft_fingerprint,
    head_frame_requests_tx,
    head_frame_tx_arms,
    header_diffs,
)
from g90_control.session import LiveHeadSession, resolve_port  # noqa: E402


def po_abort_if_needed(po: int, allow_po: bool) -> None:
    """S5: abort effect-diff ladder when body Po≠0 unless step allows RF."""
    if po != 0 and not allow_po:
        raise RuntimeError(
            f"body Po meter non-zero ({po}) — aborting (no-TX policy)"
        )


def _slug(label: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", label.strip().lower()).strip("_")
    return (s[:48] or "step")


def _hdr13_47_nz(frame: bytes) -> list[tuple[int, int]]:
    return [(i, frame[i]) for i in range(13, min(48, len(frame))) if frame[i] != 0]


def _save_body(
    save_dir: Path,
    *,
    step_i: int,
    frame_i: int,
    label: str,
    frame: bytes,
) -> Path:
    save_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = save_dir / f"body_s{step_i:02d}_{_slug(label)}_{frame_i:03d}_{ts}"
    base.with_suffix(".bin").write_bytes(frame)
    hex_path = base.with_suffix(".hex")
    hex_path.write_text(frame.hex() + "\n", encoding="utf-8")
    return hex_path


def _tx_ctrl_summary(tx: bytes) -> str:
    """Compact head control bytes for ladder logs."""
    return (
        f"c1={tx[36]:02x} c2={tx[37]:02x} c3={tx[38]:02x} rclk={tx[39]} "
        f"gain={tx[42]:02x}{tx[43]:02x} sql={tx[45]} nb={tx[46]:02x} "
        f"vol={tx[48]} mic={tx[49]} qsk_t={tx[51]} mem={tx[53]} c6={tx[54]:02x} "
        f"wpm={tx[60]} c7={tx[61]:02x} c8={tx[82]:02x}{tx[83]:02x} "
        f"rit={tx[84]:02x}{tx[85]:02x}"
    )


@dataclass(frozen=True)
class Step:
    label: str
    mode: str
    freq_hz: int
    filter_high_hz: int
    filter_low_hz: int
    expect: str
    volume: int = 1
    att_mode: int = 0  # 0=none, 1=pre, 2=att (community)
    agc: int = 0  # 0=off, 1=slow, 2=fast, 3=auto (community)
    controls: Mapping[str, Any] = field(default_factory=dict)
    extras: Mapping[str, Any] = field(default_factory=dict)
    frames: int | None = None  # override settle; shutdown uses few frames
    allow_po: bool = False  # ATU tune may briefly key RF
    allow_link_loss: bool = False  # shutdown may drop body replies


# One-knob-at-a-time ladder from a USB/20m baseline.
DEFAULT_STEPS: tuple[Step, ...] = (
    Step("baseline USB", "USB", 14_225_000, 2700, 200, "settle; note audio/noise floor"),
    Step("mode → LSB", "LSB", 14_225_000, 2700, 200, "sideband flip if you can hear a carrier/voice"),
    Step("mode → CW", "CW", 14_225_000, 500, 200, "narrower IF; CW path (no key / no PTT)"),
    Step("mode → USB", "USB", 14_225_000, 2700, 200, "return to baseline audio"),
    Step("filter → 500", "USB", 14_225_000, 500, 200, "ears: narrower; body may NOT echo filter raw"),
    Step("filter → 3200", "USB", 14_225_000, 3200, 200, "ears: wider; prior filter diffs were CRC-only"),
    Step("freq → 7.030", "USB", 7_030_000, 2700, 200, "second RX / band noise change on 40 m"),
    Step("freq → 14.225", "USB", 14_225_000, 2700, 200, "back to 20 m; FFT fingerprint should move"),
)

# Extra RX-only knobs + modes (still no PTT). Appended by ``--extended``.
EXTENDED_STEPS: tuple[Step, ...] = (
    Step("vol → 8", "USB", 14_225_000, 2700, 200, "ears: louder AF", volume=8),
    Step("vol → 1", "USB", 14_225_000, 2700, 200, "ears: quieter AF", volume=1),
    Step("att → pre", "USB", 14_225_000, 2700, 200, "S-meter / noise may drop", att_mode=1),
    Step("att → att", "USB", 14_225_000, 2700, 200, "more attenuation", att_mode=2),
    Step("att → none", "USB", 14_225_000, 2700, 200, "back to no ATT", att_mode=0),
    Step("agc → slow", "USB", 14_225_000, 2700, 200, "AGC slow", agc=1),
    Step("agc → fast", "USB", 14_225_000, 2700, 200, "AGC fast", agc=2),
    Step("agc → off", "USB", 14_225_000, 2700, 200, "AGC off", agc=0),
    Step("mode → AM", "AM", 14_225_000, 2700, 200, "AM path; FFT may move"),
    Step("mode → NFM", "NFM", 14_225_000, 2700, 200, "NFM path"),
    Step("mode → USB end", "USB", 14_225_000, 2700, 200, "return USB"),
    Step(
        "soak 40m",
        "USB",
        7_030_000,
        2700,
        200,
        "longer look at header bytes 13–47 / FFT; still RX-only",
    ),
)

# Community-schema control bits (RX-safe). Appended by ``--controls``.
# Baseline first so diffs are vs a known USB/20m idle.
CONTROLS_STEPS: tuple[Step, ...] = (
    Step(
        "ctrl baseline",
        "USB",
        14_225_000,
        2700,
        200,
        "idle controls; wire defaults",
        volume=5,
    ),
    Step(
        "nb on",
        "USB",
        14_225_000,
        2700,
        200,
        "NB enable; noise floor / FFT may move",
        volume=5,
        controls={"nb_en": True, "nb_level": 5, "nb_width": 3},
    ),
    Step(
        "nb wider",
        "USB",
        14_225_000,
        2700,
        200,
        "NB width↑",
        volume=5,
        controls={"nb_en": True, "nb_level": 5, "nb_width": 7},
    ),
    Step(
        "nb off",
        "USB",
        14_225_000,
        2700,
        200,
        "NB clear",
        volume=5,
        controls={"nb_en": False},
    ),
    Step(
        "sql → 3",
        "USB",
        14_225_000,
        2700,
        200,
        "squelch (NFM/ears; USB may be silent)",
        volume=5,
        controls={"sql_level": 3},
    ),
    Step(
        "sql → 0",
        "USB",
        14_225_000,
        2700,
        200,
        "squelch off",
        volume=5,
        controls={"sql_level": 0},
    ),
    Step(
        "rclk +100",
        "USB",
        14_225_000,
        2700,
        200,
        "RCLK +100 Hz — pitch / FFT bin may shift",
        volume=5,
        controls={"rclk_hz": 100},
    ),
    Step(
        "rclk −100",
        "USB",
        14_225_000,
        2700,
        200,
        "RCLK −100 Hz",
        volume=5,
        controls={"rclk_hz": -100},
    ),
    Step(
        "rclk 0",
        "USB",
        14_225_000,
        2700,
        200,
        "RCLK center",
        volume=5,
        controls={"rclk_hz": 0},
    ),
    Step(
        "rit +50",
        "USB",
        14_225_000,
        2700,
        200,
        "RIT +50 Hz",
        volume=5,
        controls={"rit_hz": 50},
    ),
    Step(
        "rit −50",
        "USB",
        14_225_000,
        2700,
        200,
        "RIT −50 Hz",
        volume=5,
        controls={"rit_hz": -50},
    ),
    Step(
        "rit 0",
        "USB",
        14_225_000,
        2700,
        200,
        "RIT clear",
        volume=5,
        controls={"rit_hz": 0},
    ),
    Step(
        "split on",
        "USB",
        14_225_000,
        2700,
        200,
        "split_en (TX VFO-B when keyed — still RX here)",
        volume=5,
        controls={"split_en": True},
    ),
    Step(
        "vfo-b en",
        "USB",
        14_225_000,
        2700,
        200,
        "vfo_b_en select",
        volume=5,
        controls={"split_en": True, "vfo_b_en": True},
    ),
    Step(
        "split off",
        "USB",
        14_225_000,
        2700,
        200,
        "clear split / VFO-B",
        volume=5,
        controls={"split_en": False, "vfo_b_en": False},
    ),
    Step(
        "phones on",
        "USB",
        14_225_000,
        2700,
        200,
        "headphones route — ears",
        volume=5,
        controls={"headphones": True},
    ),
    Step(
        "line-in on",
        "USB",
        14_225_000,
        2700,
        200,
        "ACC line-in select — ears / dig path",
        volume=5,
        controls={"audio_in_line_en": True},
    ),
    Step(
        "audio route off",
        "USB",
        14_225_000,
        2700,
        200,
        "clear headphones + line-in",
        volume=5,
        controls={"headphones": False, "audio_in_line_en": False},
    ),
    Step(
        "af_gain 31",
        "USB",
        14_225_000,
        2700,
        200,
        "AF gain high — ears",
        volume=5,
        controls={"af_gain": 31},
    ),
    Step(
        "af_gain 15",
        "USB",
        14_225_000,
        2700,
        200,
        "AF gain default",
        volume=5,
        controls={"af_gain": 15},
    ),
    Step(
        "beep off",
        "USB",
        14_225_000,
        2700,
        200,
        "beep_en clear — ears on next button (none here)",
        volume=5,
        controls={"beep_en": False},
    ),
    Step(
        "beep on",
        "USB",
        14_225_000,
        2700,
        200,
        "beep_en set",
        volume=5,
        controls={"beep_en": True},
    ),
    Step(
        "mic 18",
        "USB",
        14_225_000,
        2700,
        200,
        "mic_gain (TX path; wire-only on RX)",
        volume=5,
        controls={"mic_gain": 18},
    ),
    Step(
        "mic 10",
        "USB",
        14_225_000,
        2700,
        200,
        "mic_gain default",
        volume=5,
        controls={"mic_gain": 10},
    ),
    Step(
        "cw path",
        "CW",
        14_225_000,
        500,
        200,
        "CW + disp + WPM/QSK (no paddle)",
        volume=5,
        controls={
            "cw_disp_en": True,
            "cw_wpm": 25,
            "cw_qsk": True,
            "cw_qsk_time_raw": 5,
        },
    ),
    Step(
        "cw wpm 18",
        "CW",
        14_225_000,
        500,
        200,
        "CW WPM↓",
        volume=5,
        controls={"cw_disp_en": True, "cw_wpm": 18, "cw_qsk": True},
    ),
    Step(
        "cw qsk off",
        "CW",
        14_225_000,
        500,
        200,
        "cw_qsk clear",
        volume=5,
        controls={"cw_disp_en": True, "cw_wpm": 18, "cw_qsk": False},
    ),
    Step(
        "ctrl end USB",
        "USB",
        14_225_000,
        2700,
        200,
        "return USB idle",
        volume=5,
    ),
)

# Opt-in: panel lock / tuner_en (safe) + ATU tune (may RF) + shutdown LAST.
# VOX intentionally omitted. Requires ``--armed-controls`` (+ permit tune).
ARMED_CONTROLS_STEPS: tuple[Step, ...] = (
    Step(
        "armed baseline",
        "USB",
        14_225_000,
        2700,
        200,
        "idle before armed probes",
        volume=5,
    ),
    Step(
        "panel lock on",
        "USB",
        14_225_000,
        2700,
        200,
        "panel_lock — no stock UI to observe; wire only",
        volume=5,
        controls={"panel_lock": True},
    ),
    Step(
        "panel lock off",
        "USB",
        14_225_000,
        2700,
        200,
        "clear panel_lock",
        volume=5,
        controls={"panel_lock": False},
    ),
    Step(
        "tuner_en on",
        "USB",
        14_225_000,
        2700,
        200,
        "ATU enable flag (not tune cycle)",
        volume=5,
        controls={"tuner_en": True},
    ),
    Step(
        "tuner_en off",
        "USB",
        14_225_000,
        2700,
        200,
        "clear tuner_en",
        volume=5,
        controls={"tuner_en": False},
    ),
    Step(
        "atu tune on",
        "USB",
        14_225_000,
        2700,
        200,
        "ATU tuning bit — may key RF briefly; listen for relay/tuner",
        volume=5,
        controls={"tuner_en": True},
        extras={"arm_atu_tune": True},
        frames=4,
        allow_po=True,
    ),
    Step(
        "atu tune off",
        "USB",
        14_225_000,
        2700,
        200,
        "clear ATU tune + tuner_en (Po may lag)",
        volume=5,
        controls={"tuner_en": False},
        frames=8,
        allow_po=True,
    ),
    Step(
        "post-tune cool",
        "USB",
        14_225_000,
        2700,
        200,
        "wait for Po→0 after ATU before shutdown",
        volume=5,
        frames=20,
        allow_po=True,
    ),
    Step(
        "shutdown pulse",
        "USB",
        14_225_000,
        2700,
        200,
        "shutdown_req — radio may power off; LAST probe",
        volume=5,
        controls={"shutdown_req": True},
        frames=3,
        allow_po=True,
        allow_link_loss=True,
    ),
    Step(
        "shutdown clear",
        "USB",
        14_225_000,
        2700,
        200,
        "clear shutdown_req / probe if still alive",
        volume=5,
        controls={"shutdown_req": False},
        frames=5,
        allow_po=True,
        allow_link_loss=True,
    ),
)

# Remaining soft schema fields (no shutdown). VOX last (may key on audio).
REST_CONTROLS_STEPS: tuple[Step, ...] = (
    Step(
        "rest baseline",
        "USB",
        14_225_000,
        2700,
        200,
        "idle before rest probes",
        volume=5,
    ),
    Step(
        "mic_comp on",
        "USB",
        14_225_000,
        2700,
        200,
        "mic_compression — TX path; wire-only on RX",
        volume=5,
        controls={"mic_compression": True},
    ),
    Step(
        "mic_comp off",
        "USB",
        14_225_000,
        2700,
        200,
        "clear mic_compression",
        volume=5,
        controls={"mic_compression": False},
    ),
    Step(
        "mem ch 3",
        "USB",
        14_225_000,
        2700,
        200,
        "mem_en + mem_ch=3",
        volume=5,
        controls={"mem_en": True, "mem_ch": 3},
    ),
    Step(
        "mem ch 12",
        "USB",
        14_225_000,
        2700,
        200,
        "mem_ch=12",
        volume=5,
        controls={"mem_en": True, "mem_ch": 12},
    ),
    Step(
        "mem off",
        "USB",
        14_225_000,
        2700,
        200,
        "clear mem_en; mem_ch default 6",
        volume=5,
        controls={"mem_en": False, "mem_ch": 6},
    ),
    Step(
        "band_stack on",
        "USB",
        14_225_000,
        2700,
        200,
        "band_stack_full",
        volume=5,
        controls={"band_stack_full": True},
    ),
    Step(
        "band_stack off",
        "USB",
        14_225_000,
        2700,
        200,
        "clear band_stack_full",
        volume=5,
        controls={"band_stack_full": False},
    ),
    Step(
        "cw ctrl7",
        "CW",
        14_225_000,
        500,
        200,
        "CW ratio/mode-B/M-L-R",
        volume=5,
        controls={
            "cw_disp_en": True,
            "cw_wpm": 22,
            "cw_ratio_raw": 5,
            "cw_mode_b": True,
            "cw_mlr": 2,
        },
    ),
    Step(
        "cw ctrl7 b",
        "CW",
        14_225_000,
        500,
        200,
        "CW ctrl7 alternate",
        volume=5,
        controls={
            "cw_disp_en": True,
            "cw_wpm": 18,
            "cw_ratio_raw": 0,
            "cw_mode_b": False,
            "cw_mlr": 1,
        },
    ),
    Step(
        "ctrl8 aux",
        "USB",
        14_225_000,
        2700,
        200,
        "SWR thresh + aux in/out vol",
        volume=5,
        controls={
            "swr_threshold_raw": 4,
            "aux_out_vol": 12,
            "aux_in_vol": 3,
        },
    ),
    Step(
        "ctrl8 default",
        "USB",
        14_225_000,
        2700,
        200,
        "restore ctrl8 defaults",
        volume=5,
        controls={
            "swr_threshold_raw": 1,
            "aux_out_vol": 9,
            "aux_in_vol": 8,
        },
    ),
    Step(
        "vox on",
        "USB",
        14_225_000,
        2700,
        200,
        "VOX arm — may key if audio present; short hold",
        volume=5,
        extras={"arm_vox": True},
        frames=6,
        allow_po=True,
    ),
    Step(
        "vox off",
        "USB",
        14_225_000,
        2700,
        200,
        "clear VOX",
        volume=5,
        frames=8,
        allow_po=True,
    ),
    Step(
        "rest end",
        "USB",
        14_225_000,
        2700,
        200,
        "return idle",
        volume=5,
    ),
)


@dataclass
class Sample:
    step: Step
    tx: bytes
    rx: bytes
    status_summary: str
    fft_fp: tuple[int, int, int]
    miss: int
    po: int
    ptt_armed_tx: bool


def _settle(
    sess: LiveHeadSession,
    step: Step,
    *,
    frames: int,
    interval_s: float,
    save_dir: Path | None = None,
    step_i: int = 0,
    manifest: TextIO | None = None,
    saw_45_20: list[bool] | None = None,
) -> Sample:
    last_rx: bytes | None = None
    last_body = None
    last_tx: bytes | None = None
    miss = 0
    max_po = 0
    for frame_i in range(frames):
        kw: dict[str, Any] = dict(
            mode=step.mode,
            freq_hz=step.freq_hz,
            filter_high_hz=step.filter_high_hz,
            filter_low_hz=step.filter_low_hz,
            volume=step.volume,
            att_mode=step.att_mode,
            agc=step.agc,
        )
        if step.controls:
            kw["controls"] = dict(step.controls)
        if step.extras:
            kw["extras"] = dict(step.extras)
        er = sess.exchange(**kw)
        arms = head_frame_tx_arms(er.tx)
        forbidden = arms - sess.permit_arms
        if forbidden:
            raise RuntimeError(
                f"lab guard failed: armed {sorted(forbidden)} not in permit_arms"
            )
        if er.rx is None or er.body is None or not er.body.crc_ok:
            miss += 1
            last_tx = er.tx
        else:
            last_rx = er.rx
            last_body = er.body
            last_tx = er.tx
            max_po = max(max_po, er.body.status.po)
            po_abort_if_needed(er.body.status.po, step.allow_po)
            if b"\x45\x20" in er.rx[13:48] and saw_45_20 is not None:
                saw_45_20[0] = True
            if save_dir is not None:
                path = _save_body(
                    save_dir,
                    step_i=step_i,
                    frame_i=frame_i + 1,
                    label=step.label,
                    frame=er.rx,
                )
                nz = _hdr13_47_nz(er.rx)
                if manifest is not None:
                    manifest.write(
                        json.dumps(
                            {
                                "step_i": step_i,
                                "frame_i": frame_i + 1,
                                "label": step.label,
                                "mode": step.mode,
                                "freq_hz": step.freq_hz,
                                "att_mode": step.att_mode,
                                "agc": step.agc,
                                "volume": step.volume,
                                "controls": dict(step.controls),
                                "extras": dict(step.extras),
                                "tx_ctrl": _tx_ctrl_summary(er.tx),
                                "tx_arms": sorted(arms),
                                "path": path.name,
                                "hdr13_47_nz": [[o, v] for o, v in nz],
                                "cw_text": er.body.status.cw_text,
                                "byte6": er.rx[6],
                                "S": er.body.status.s_meter,
                                "Po": er.body.status.po,
                            },
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                    manifest.flush()
        time.sleep(interval_s)
    if last_tx is None:
        raise RuntimeError(f"no TX recorded during step {step.label!r}")
    if last_rx is None or last_body is None:
        if step.allow_link_loss:
            # Synthetic empty sample for shutdown / power-off.
            empty = bytes(372)
            return Sample(
                step=step,
                tx=last_tx,
                rx=empty,
                status_summary=f"(no body CRC-ok; miss={miss}/{frames} max_Po={max_po})",
                fft_fp=(0, 0, 0),
                miss=miss,
                po=max_po,
                ptt_armed_tx=head_frame_requests_tx(last_tx),
            )
        raise RuntimeError(
            f"no CRC-ok body during step {step.label!r} (miss={miss}/{frames})"
        )
    return Sample(
        step=step,
        tx=last_tx,
        rx=last_rx,
        status_summary=last_body.status.summary(),
        fft_fp=fft_fingerprint(last_body.fft),
        miss=miss,
        po=max_po if step.allow_po else last_body.status.po,
        ptt_armed_tx=head_frame_requests_tx(last_tx),
    )


def _print_diff(prev: Sample, cur: Sample) -> dict[str, Any]:
    hdr = header_diffs(prev.rx[:48], cur.rx[:48])
    fft_changed = prev.fft_fp != cur.fft_fp
    tx_changed = prev.tx[:92] != cur.tx[:92]
    tx_diffs = [i for i in range(92) if prev.tx[i] != cur.tx[i]]
    print(f"  vs previous: header_bytes_changed={len(hdr)} fft_fp_changed={fft_changed}")
    if hdr:
        shown = ", ".join(f"[{o}]=0x{a:02x}→0x{b:02x}" for o, a, b in hdr[:12])
        more = "" if len(hdr) <= 12 else f" …+{len(hdr) - 12}"
        print(f"    header: {shown}{more}")
    print(f"    fft_fp: sum/peak@bin {prev.fft_fp} → {cur.fft_fp}")
    if tx_changed:
        shown = ", ".join(
            f"[{i}]=0x{prev.tx[i]:02x}→0x{cur.tx[i]:02x}" for i in tx_diffs[:10]
        )
        more = "" if len(tx_diffs) <= 10 else f" …+{len(tx_diffs) - 10}"
        print(f"    head_tx: {shown}{more}")
    else:
        print("    head_tx: (unchanged)")
    return {
        "header_changed": len(hdr) > 0,
        "fft_changed": fft_changed,
        "header_diffs": hdr,
        "tx_changed": tx_changed,
        "tx_diffs": tx_diffs,
    }


def run_differential(
    sess: LiveHeadSession,
    steps: tuple[Step, ...] = DEFAULT_STEPS,
    *,
    settle_frames: int = 8,
    interval_ms: float = 30.0,
    soak_frames: int = 40,
    save_dir: Path | None = None,
    controls_mode: bool = False,
) -> int:
    interval_s = interval_ms / 1000.0
    any_header = False
    any_fft = False
    mode_moved = False
    freq_moved = False
    vol_moved = False
    att_moved = False
    agc_moved = False
    max_po = 0
    saw_45_20_flag = [False]
    ctrl_tx_ok = 0
    ctrl_tx_fail = 0
    ctrl_body_moved: list[str] = []
    ctrl_ears_only: list[str] = []

    print("effect-diff: settle each step, compare body header + FFT fingerprint")
    print("policy: RX-only — PTT/tune/VOX blocked; abort if body Po≠0")
    print("human: use ears / second RX for the 'expect' notes — wire alone is partial")
    if controls_mode:
        print(
            "controls: PASS = CRC + Po=0 + each control step changes head TX wire; "
            "body deltas logged (many knobs are ears-only)"
        )
    if save_dir is not None:
        save_dir.mkdir(parents=True, exist_ok=True)
        print(f"save: every CRC-ok body → {save_dir}/ (+ manifest.jsonl)\n")
    else:
        print()

    manifest_f: TextIO | None = None
    if save_dir is not None:
        manifest_f = (save_dir / "manifest.jsonl").open("a", encoding="utf-8")

    prev: Sample | None = None
    try:
        for step_i, step in enumerate(steps, start=1):
            if step.frames is not None:
                frames = step.frames
            elif step.label.startswith("soak"):
                frames = soak_frames
            else:
                frames = settle_frames
            print(f"== {step.label} ==")
            print(
                f"  head mode={step.mode} freq={step.freq_hz} "
                f"filter={step.filter_low_hz}-{step.filter_high_hz} "
                f"vol={step.volume} att={step.att_mode} agc={step.agc}"
            )
            if step.controls:
                print(f"  controls: {dict(step.controls)}")
            if step.extras:
                print(f"  extras: {dict(step.extras)}")
            if step.allow_po or step.allow_link_loss:
                print(
                    f"  flags: allow_po={step.allow_po} "
                    f"allow_link_loss={step.allow_link_loss}"
                )
            print(f"  expect: {step.expect}")
            sample = _settle(
                sess,
                step,
                frames=frames,
                interval_s=interval_s,
                save_dir=save_dir,
                step_i=step_i,
                manifest=manifest_f,
                saw_45_20=saw_45_20_flag,
            )
            max_po = max(max_po, sample.po)
            nz = _hdr13_47_nz(sample.rx)
            print(
                f"  RX {sample.status_summary} miss={sample.miss}/{frames} "
                f"Po={sample.po} ptt_armed={sample.ptt_armed_tx}"
            )
            print(f"  TX {_tx_ctrl_summary(sample.tx)}")
            print(
                f"  fft_fp sum={sample.fft_fp[0]} peak={sample.fft_fp[1]}@{sample.fft_fp[2]}"
            )
            if nz:
                shown = " ".join(f"[{o}]=0x{v:02x}" for o, v in nz[:16])
                more = "" if len(nz) <= 16 else f" …+{len(nz) - 16}"
                print(f"  hdr13-47_nz ({len(nz)}): {shown}{more}")
            if prev is not None:
                info = _print_diff(prev, sample)
                any_header = any_header or info["header_changed"]
                any_fft = any_fft or info["fft_changed"]
                moved = info["header_changed"] or info["fft_changed"]
                if "mode" in step.label and moved:
                    mode_moved = True
                if "freq" in step.label and info["fft_changed"]:
                    freq_moved = True
                if step.label.startswith("vol") and moved:
                    vol_moved = True
                if step.label.startswith("att") and moved:
                    att_moved = True
                if step.label.startswith("agc") and moved:
                    agc_moved = True
                # Control/extras steps: require head TX wire change vs previous.
                if step.controls or step.extras:
                    if info["tx_changed"]:
                        ctrl_tx_ok += 1
                        if moved:
                            ctrl_body_moved.append(step.label)
                        else:
                            ctrl_ears_only.append(step.label)
                    else:
                        ctrl_tx_fail += 1
                        print("    WARN: expected head TX change for control step")
            print()
            prev = sample
    finally:
        if manifest_f is not None:
            manifest_f.close()

    print("=== verdict ===")
    print(f"  any_header_delta={any_header} any_fft_delta={any_fft}")
    print(
        f"  mode_moved={mode_moved} freq_fft_moved={freq_moved} "
        f"vol_moved={vol_moved} att_moved={att_moved} agc_moved={agc_moved}"
    )
    print(f"  max_body_Po={max_po} (must stay 0)")
    if ctrl_tx_ok or ctrl_tx_fail:
        print(
            f"  controls_tx_changed={ctrl_tx_ok} fail={ctrl_tx_fail} "
            f"body_moved={len(ctrl_body_moved)} ears_or_silent={len(ctrl_ears_only)}"
        )
        if ctrl_body_moved:
            print(f"    body_moved: {', '.join(ctrl_body_moved)}")
        if ctrl_ears_only:
            print(f"    wire_only: {', '.join(ctrl_ears_only)}")
    if save_dir is not None:
        print(f"  saved_dir={save_dir} saw_0x45_0x20_pair={saw_45_20_flag[0]}")

    if max_po != 0:
        print("  FAIL: body Po non-zero — possible TX", file=sys.stderr)
        return 2

    if controls_mode:
        ok = ctrl_tx_fail == 0 and ctrl_tx_ok > 0
        if ok:
            print(
                "  PASS (controls): head TX wire moved each control step; "
                "RX-only held (Po=0)"
            )
            return 0
        print(
            f"  FAIL (controls): need head TX change per control step "
            f"(ok={ctrl_tx_ok} fail={ctrl_tx_fail})",
            file=sys.stderr,
        )
        return 1

    # Require causal mode + freq body reaction — ambient S/FFT alone must not PASS.
    ok = (
        any_header
        and any_fft
        and mode_moved
        and freq_moved
        and max_po == 0
    )
    if ok:
        extra = ""
        if ctrl_tx_fail:
            extra = f" (controls wire fail={ctrl_tx_fail} — see WARN above)"
        elif ctrl_tx_ok:
            extra = f" (controls wire ok={ctrl_tx_ok})"
        print(
            "  PASS (wire): mode+freq steps moved body header/FFT — "
            f"RX-only held (Po=0){extra}"
        )
        return 0 if ctrl_tx_fail == 0 else 1
    print(
        "  FAIL (wire): need header+FFT deltas on mode AND freq steps "
        f"(mode_moved={mode_moved} freq_fft_moved={freq_moved}) — "
        "ambient S/FFT alone is not enough",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default=None)
    ap.add_argument("--profile", default="1.81")
    ap.add_argument("--settle", type=int, default=8, help="frames per step")
    ap.add_argument("--soak", type=int, default=40, help="frames for soak steps")
    ap.add_argument("--interval-ms", type=float, default=30.0)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument(
        "--extended",
        action="store_true",
        help="also run volume/ATT/AGC/AM/NFM + soak (still RX-only)",
    )
    ap.add_argument(
        "--controls",
        action="store_true",
        help="RX-safe control-bit ladder (NB/SQL/RIT/RCLK/split/AF/CW/…)",
    )
    ap.add_argument(
        "--controls-only",
        action="store_true",
        help="run only the controls ladder (skip mode/freq/extended)",
    )
    ap.add_argument(
        "--armed-controls",
        action="store_true",
        help="panel_lock + tuner_en + ATU tune (may RF) + shutdown LAST; no VOX",
    )
    ap.add_argument(
        "--rest-controls",
        action="store_true",
        help="mem/mic-comp/ctrl7/ctrl8/band_stack + short VOX (no shutdown)",
    )
    ap.add_argument(
        "--save",
        metavar="DIR",
        help="save every CRC-ok body (.bin/.hex) + manifest.jsonl (header 13–47 fixture re-capture)",
    )
    ap.add_argument("--list-ports", action="store_true")
    args = ap.parse_args(argv)

    if args.list_ports:
        from g90_control.session import candidate_ports

        ports = candidate_ports()
        for p in ports:
            print(p)
        return 0 if ports else 1

    only_rest = (
        args.rest_controls
        and not args.controls_only
        and not args.controls
        and not args.extended
        and not args.armed_controls
    )
    only_armed = (
        args.armed_controls
        and not args.controls_only
        and not args.controls
        and not args.extended
        and not args.rest_controls
    )

    if only_rest:
        steps = REST_CONTROLS_STEPS
        controls_mode = True
    elif only_armed:
        steps = ARMED_CONTROLS_STEPS
        controls_mode = True
    elif args.controls_only:
        steps = CONTROLS_STEPS
        if args.rest_controls:
            steps = steps + REST_CONTROLS_STEPS
        if args.armed_controls:
            steps = steps + ARMED_CONTROLS_STEPS
        controls_mode = True
    else:
        steps = DEFAULT_STEPS
        if args.extended:
            steps = steps + EXTENDED_STEPS
        if args.controls:
            steps = steps + CONTROLS_STEPS
        if args.rest_controls:
            steps = steps + REST_CONTROLS_STEPS
        if args.armed_controls:
            steps = steps + ARMED_CONTROLS_STEPS
        controls_mode = bool(
            args.controls or args.controls_only or args.armed_controls or args.rest_controls
        )

    port = resolve_port(args.port)
    save_dir = Path(args.save) if args.save else None
    permit: frozenset[str] = frozenset()
    if args.armed_controls:
        permit = permit | frozenset({"tune"})
    if args.rest_controls:
        permit = permit | frozenset({"vox"})
    print(
        f"effect-diff {port} @ {args.baud} profile={args.profile} "
        f"settle={args.settle} steps={len(steps)}"
        + (" extended" if args.extended else "")
        + (" controls" if (args.controls or args.controls_only) else "")
        + (" rest" if args.rest_controls else "")
        + (" armed" if args.armed_controls else "")
        + (f" permit_arms={sorted(permit)}" if permit else "")
        + (f" save={save_dir}" if save_dir else ""),
        file=sys.stderr,
    )
    warn = "RX-only (PTT/tune/VOX blocked)"
    if args.armed_controls and args.rest_controls:
        warn = "ARMED: ATU/shutdown + VOX may key RF"
    elif args.armed_controls:
        warn = "ARMED: ATU tune may key RF; shutdown LAST may power off"
    elif args.rest_controls:
        warn = "REST: short VOX may key RF if audio trips; no shutdown"
    print(
        "confirm: stock head UNPLUGGED; TXD→pin2 RXD→pin3 GND→pin5; 3.3V logic; "
        + warn,
        file=sys.stderr,
    )

    with LiveHeadSession(
        port,
        baud=args.baud,
        profile=args.profile,
        timeout_s=args.timeout,
        permit_arms=permit,
    ) as sess:
        try:
            return run_differential(
                sess,
                steps,
                settle_frames=args.settle,
                soak_frames=args.soak,
                interval_ms=args.interval_ms,
                save_dir=save_dir,
                controls_mode=bool(
                    args.controls_only
                    or only_armed
                    or only_rest
                ),
            )
        except KeyboardInterrupt:
            print("\ninterrupted", file=sys.stderr)
            return 130
        except RuntimeError as e:
            print(f"FAIL {e}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
