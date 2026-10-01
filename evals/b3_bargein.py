"""Behaviour 3 — barge-in handling: yield quickly on a real interruption, do not
talk over the caller, resume correctly after a false interruption.

Metrics (per run, per persona, per environment):
  R_Y   yield rate: share of real user interruptions where the agent stopped within 2.0 s
  L_Y   mean / p50 / p95 yield latency (s)
  I_A   agent-interrupts-user events per user turn (agent segment starting while the user speaks)
  overlap_share  share of ticks where both parties speak
  livekit.false_interruptions / resumed  (from the sidecar, livekit_session provider only)

Yield events come from τ-bench's ``extract_voice_quality_events_from_simulation``
(identical windows to the leaderboard panel). I_A and overlap share are computed
from the tick timeline directly so they are available per simulation.

Usage:
    uv run --project external/tau2-bench python evals/b3_bargein.py <run_dir> [--out runs/<name>/b3.json]
"""

from __future__ import annotations

import argparse
import statistics
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


def _speaking(chunk) -> bool:
    return bool(chunk is not None and getattr(chunk, "contains_speech", False))


def tick_overlap_stats(ticks) -> dict:
    """I_A numerator (agent starts while user speaking), user turns, overlap share."""
    agent_prev = False
    user_prev = False
    agent_interrupts = 0
    user_turns = 0
    overlap_ticks = 0
    speech_ticks = 0
    for t in ticks:
        a = _speaking(t.agent_chunk)
        u = _speaking(t.user_chunk)
        if a and not agent_prev and u:
            agent_interrupts += 1
        if u and not user_prev:
            user_turns += 1
        if a or u:
            speech_ticks += 1
        if a and u:
            overlap_ticks += 1
        agent_prev, user_prev = a, u
    return {
        "agent_interrupts": agent_interrupts,
        "user_turns": user_turns,
        "overlap_ticks": overlap_ticks,
        "speech_ticks": speech_ticks,
    }


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

    from tau2.metrics.voice_interaction_metrics import (
        extract_voice_quality_events_from_simulation,
    )

    groups = defaultdict(
        lambda: {"yield_n": 0, "yield_ok": 0, "yield_lat": [], "agent_interrupts": 0, "user_turns": 0, "overlap_ticks": 0, "speech_ticks": 0, "false_int": 0, "false_int_resumed": 0}
    )
    per_sim = {}
    for sim in results.simulations:
        if not sim.ticks:
            continue
        evs = extract_voice_quality_events_from_simulation(
            sim.ticks, tick_duration_sec=tick_sec, simulation_id=sim.id, task_id=str(sim.task_id)
        )
        yields = [e for e in evs if e.event_category == "yield"]
        ov = tick_overlap_stats(sim.ticks)
        side = load_sidecar(run_dir, sim)
        false_int = [e for e in side if e.get("type") == "agent_false_interruption"]
        rec = {
            "task_id": str(sim.task_id),
            "persona": persona_of(sim) or "?",
            "environment": environment_of(sim) or "?",
            "yield_n": len(yields),
            "yield_ok": sum(1 for e in yields if e.event_type == "yield"),
            "yield_latencies": [e.latency_sec for e in yields if e.latency_sec is not None],
            **ov,
            "false_interruptions": len(false_int),
            "false_interruptions_resumed": sum(1 for e in false_int if e.get("resumed")),
        }
        per_sim[sim.id] = rec
        for g in ("all", f"persona={rec['persona']}", f"env={rec['environment']}"):
            G = groups[g]
            G["yield_n"] += rec["yield_n"]
            G["yield_ok"] += rec["yield_ok"]
            G["yield_lat"].extend(rec["yield_latencies"])
            for k in ("agent_interrupts", "user_turns", "overlap_ticks", "speech_ticks"):
                G[k] += ov[k]
            G["false_int"] += rec["false_interruptions"]
            G["false_int_resumed"] += rec["false_interruptions_resumed"]

    def pct(xs, q):
        if not xs:
            return None
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]

    table = {}
    for g, G in groups.items():
        table[g] = {
            "R_Y": (G["yield_ok"] / G["yield_n"]) if G["yield_n"] else None,
            "yield_n": G["yield_n"],
            "L_Y_mean": statistics.mean(G["yield_lat"]) if G["yield_lat"] else None,
            "L_Y_p50": pct(G["yield_lat"], 0.5),
            "L_Y_p95": pct(G["yield_lat"], 0.95),
            "I_A": (G["agent_interrupts"] / G["user_turns"]) if G["user_turns"] else None,
            "overlap_share": (G["overlap_ticks"] / G["speech_ticks"]) if G["speech_ticks"] else None,
            "false_interruptions": G["false_int"],
            "false_interruptions_resumed": G["false_int_resumed"],
        }

    out = {
        "run": run_dir.name,
        "targets": {"R_Y": 0.95, "L_Y_mean": 0.6, "I_A": 0.2},
        "cascaded_baseline_row": {"R_Y": 0.99, "L_Y_mean": 0.84, "I_A": 0.58},  # retail domain
        "notes": "I_A here counts agent segments starting while the user speaks, normalised by user turns "
        "(same definition as τ-bench's panel; small numeric differences possible because τ-bench "
        "applies its end-of-conversation filter).",
        "by_group": table,
        "per_simulation": per_sim,
    }
    out_path = Path(args.out) if args.out else run_dir / "b3_bargein.json"
    write_json(out_path, out)

    print(f"# Behaviour 3 — barge-in — {run_dir.name}")
    print(f"{'group':28s} {'R_Y':>8s} {'n':>5s} {'L_Y mean':>9s} {'p50':>6s} {'p95':>6s} {'I_A':>6s} {'overlap':>8s} {'falseInt':>9s}")
    for g in sorted(table, key=lambda x: (x != "all", x)):
        r = table[g]
        print(
            f"{g:28s} {fmt(r['R_Y']):>8s} {r['yield_n']:>5d} {fmt(r['L_Y_mean']):>9s} {fmt(r['L_Y_p50']):>6s} "
            f"{fmt(r['L_Y_p95']):>6s} {fmt(r['I_A']):>6s} {fmt(r['overlap_share']):>8s} "
            f"{r['false_interruptions']:>4d}/{r['false_interruptions_resumed']:<4d}"
        )
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
