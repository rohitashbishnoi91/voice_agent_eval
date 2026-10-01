"""Behaviour 2 — selectivity: does the agent talk through backchannels, vocal
tics and non-directed speech instead of stopping or answering them?

Reuses τ-bench's event extraction (same detection windows as the leaderboard
panel) and breaks the three selectivity rates down per persona and per
environment. When the run comes from the ``livekit_session`` provider, the
LiveKit sidecar is cross-checked so a failure can be attributed to
"LiveKit's interruption detector classified the overlap as a real interruption"
(config/model problem) versus "the agent stopped although the detector said
backchannel" (pipeline/prompt problem).

Usage:
    uv run --project external/tau2-bench python evals/b2_selectivity.py <run_dir> [--out runs/<name>/b2.json]
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from common import (
    environment_of,
    fmt,
    load_results,
    load_sidecar,
    persona_of,
    resolve_run_dir,
    write_json,
)

CATEGORIES = {
    "backchannel": "S_BC",
    "vocal_tic": "S_VT",
    "non_directed": "S_ND",
}


def selectivity_events(sim, tick_sec: float):
    from tau2.metrics.voice_interaction_metrics import (
        extract_voice_quality_events_from_simulation,
    )

    events = extract_voice_quality_events_from_simulation(
        sim.ticks, tick_duration_sec=tick_sec, simulation_id=sim.id, task_id=str(sim.task_id)
    )
    return [e for e in events if e.event_category in CATEGORIES]


def sidecar_overlaps(run_dir: Path, sim) -> list[dict]:
    """LiveKit `overlapping_speech` events (adaptive interruption verdicts)."""
    return [e for e in load_sidecar(run_dir, sim) if e.get("type") == "overlapping_speech"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    run_dir = resolve_run_dir(args.run_dir)
    results = load_results(run_dir)
    tick_sec = (
        results.info.audio_native_config.tick_duration_seconds
        if results.info.audio_native_config
        else 0.2
    )

    agg = defaultdict(lambda: {"n": 0, "errors": 0})  # key -> counts
    per_sim = {}
    for sim in results.simulations:
        if not sim.ticks:
            continue
        evs = selectivity_events(sim, tick_sec)
        persona, env = persona_of(sim) or "?", environment_of(sim) or "?"
        sim_rec = {c: {"n": 0, "errors": 0} for c in CATEGORIES}
        for e in evs:
            for key in (("all", e.event_category), (f"persona={persona}", e.event_category), (f"env={env}", e.event_category)):
                agg[key]["n"] += 1
                agg[key]["errors"] += int(e.is_error)
            sim_rec[e.event_category]["n"] += 1
            sim_rec[e.event_category]["errors"] += int(e.is_error)
        # LiveKit-side cross-check: how many overlaps did the adaptive detector
        # judge as backchannel (is_interruption False) vs real interruption?
        ov = sidecar_overlaps(run_dir, sim)
        sim_rec["livekit_overlaps"] = {
            "n": len(ov),
            "judged_interruption": sum(1 for o in ov if o.get("is_interruption")),
            "judged_backchannel": sum(1 for o in ov if not o.get("is_interruption")),
            "mean_detection_delay": (
                sum(o.get("detection_delay", 0.0) for o in ov) / len(ov) if ov else None
            ),
        }
        sim_rec["task_id"] = str(sim.task_id)
        sim_rec["persona"] = persona
        sim_rec["environment"] = env
        per_sim[sim.id] = sim_rec

    def rate(d):
        return None if d["n"] == 0 else 1.0 - d["errors"] / d["n"]

    table = {}
    for (group, cat), d in agg.items():
        table.setdefault(group, {})[CATEGORIES[cat]] = {"selectivity": rate(d), **d}

    out = {
        "run": run_dir.name,
        "metric_definitions": {
            "S_BC": "share of user backchannels the agent correctly talked through (yield within 1.0 s = error)",
            "S_VT": "share of vocal tics correctly ignored (yield within 1.0 s while speaking / respond within 2.0 s while silent = error)",
            "S_ND": "share of non-directed speech correctly ignored (same windows)",
        },
        "targets": {"S_BC": 0.85, "S_VT": 0.5, "S_ND": 0.4},
        "cascaded_baseline_row": {"S_BC": 0.57, "S_VT": 0.50, "S_ND": 0.52},  # retail domain
        "by_group": table,
        "per_simulation": per_sim,
    }
    out_path = Path(args.out) if args.out else run_dir / "b2_selectivity.json"
    write_json(out_path, out)

    print(f"# Behaviour 2 — selectivity — {run_dir.name}")
    print(f"{'group':28s} {'S_BC':>12s} {'S_VT':>12s} {'S_ND':>12s}")
    for group in sorted(table, key=lambda g: (g != "all", g)):
        row = table[group]
        cells = []
        for m in ("S_BC", "S_VT", "S_ND"):
            d = row.get(m)
            cells.append(f"{fmt(d['selectivity'])} (n={d['n']})" if d else "-")
        print(f"{group:28s} " + " ".join(f"{c:>12s}" for c in cells))
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
