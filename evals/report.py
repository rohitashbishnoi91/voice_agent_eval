"""One comparison table across runs: pass^1, τ-bench interaction panel, and the
behaviour-1/2/3 metrics, with an optional diff against a baseline run.

Usage:
    uv run --project external/tau2-bench python evals/report.py <run_dir> [<run_dir> ...] [--baseline <run_dir>] [--subset]

`--subset` restricts pass^1 to the frozen 30-task iteration subset so full runs
and subset runs can be compared on the same tasks.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import fmt, load_results, load_subset_ids, resolve_run_dir, summarize_rewards, write_json

PANEL = [
    ("L_R", "response_latency_mean"),
    ("L_Y", "yield_latency_mean"),
    ("R_R", "response_rate"),
    ("R_Y", "yield_rate"),
    ("I_A", "agent_interruption_rate"),
    ("S_BC", "selectivity_backchannel"),
    ("S_VT", "selectivity_vocal_tic"),
    ("S_ND", "selectivity_non_directed"),
]

BASELINE_ROW = {  # τ³-Voice "Cascaded baseline", RETAIL domain, regular (submission JSON, 2026-05-19)
    # (the leaderboard "overall" row averages retail/airline/telecom: L_R 4.24, I_A 0.64, S_BC 0.67, ...)
    "pass_1": 28.9, "L_R": 4.02, "L_Y": 0.84, "R_R": 0.77, "R_Y": 0.99,
    "I_A": 0.58, "S_BC": 0.57, "S_VT": 0.50, "S_ND": 0.52,
}


def panel_metrics(run_dir: Path, results):
    cached = run_dir / "interaction_metrics.json"
    if cached.exists():
        try:
            d = json.load(open(cached))
            # `tau2 submit interaction-metrics` writes {"domains": {...}, "overall": {...}} or a flat dict
            if "overall" in d:
                return d["overall"]
            if "domains" in d and d["domains"]:
                return next(iter(d["domains"].values()))
            return d
        except Exception:
            pass
    try:
        from tau2.metrics.voice_interaction_metrics import compute_interaction_metrics_for_experiment

        return compute_interaction_metrics_for_experiment(results)
    except Exception as e:  # text runs, missing ticks, etc.
        return {"error": str(e)}


def behaviour_json(run_dir: Path, name: str):
    p = run_dir / name
    return json.load(open(p)) if p.exists() else None


def collect(run_dir: Path, subset_only: bool) -> dict:
    results = load_results(run_dir)
    summ = summarize_rewards(run_dir, results)
    row = {"run": run_dir.name, "n_sims": summ.n_sims, "pass_1": summ.pass_1, "avg_reward": summ.avg_reward}
    if subset_only:
        ids = set(load_subset_ids())
        vals = [v for k, v in summ.rewards_by_task.items() if k in ids]
        row["pass_1_subset"] = 100.0 * sum(1 for v in vals if v >= 1.0) / len(vals) if vals else None
        row["n_subset_tasks"] = len(vals)
    pm = panel_metrics(run_dir, results)
    for short, key in PANEL:
        row[short] = pm.get(key) if isinstance(pm, dict) else None
    b1 = behaviour_json(run_dir, "b1_identifiers.json")
    if b1:
        row["b1_auth_success"] = b1.get("summary", {}).get("auth_success_rate")
        row["b1_entity_recall"] = b1.get("summary", {}).get("entity_recall_used")
        row["b1_spell_request_rate"] = b1.get("summary", {}).get("spell_request_rate")
    b3 = behaviour_json(run_dir, "b3_bargein.json")
    if b3:
        row["L_Y_p95"] = b3["by_group"].get("all", {}).get("L_Y_p95")
        row["false_int"] = b3["by_group"].get("all", {}).get("false_interruptions")
    # tool errors per simulation (weak agents hit τ-bench's 10-error cap; useful signal)
    try:
        errs = []
        for sim in results.simulations:
            n = 0
            for t in sim.ticks or []:
                n += sum(1 for tm in (t.agent_tool_results or []) if getattr(tm, "error", False))
            errs.append(n)
        row["tool_errors_per_sim"] = sum(errs) / len(errs) if errs else None
        row["term_reasons"] = ",".join(sorted({str(sim.termination_reason) for sim in results.simulations}))
    except Exception:
        row["tool_errors_per_sim"] = None
    # cost
    try:
        costs = [s.agent_cost for s in results.simulations if s.agent_cost is not None]
        row["agent_cost_per_sim_usd"] = sum(costs) / len(costs) if costs else None
    except Exception:
        row["agent_cost_per_sim_usd"] = None
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--subset", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    rows = [collect(resolve_run_dir(r), args.subset) for r in args.runs]
    base = collect(resolve_run_dir(args.baseline), args.subset) if args.baseline else None

    cols = ["pass_1"] + (["pass_1_subset"] if args.subset else []) + [s for s, _ in PANEL] + [
        "L_Y_p95", "false_int", "b1_auth_success", "b1_entity_recall", "b1_spell_request_rate", "tool_errors_per_sim", "agent_cost_per_sim_usd"
    ]
    header = f"{'run':44s} " + " ".join(f"{c:>10s}" for c in cols)
    print(header)
    print("-" * len(header))
    print(f"{'τ³-Voice cascaded baseline (leaderboard)':44s} " + " ".join(f"{fmt(BASELINE_ROW.get(c)):>10s}" for c in cols))
    if base:
        print(f"{'[baseline] ' + base['run'][:32]:44s} " + " ".join(f"{fmt(base.get(c)):>10s}" for c in cols))
    for r in rows:
        print(f"{r['run'][:44]:44s} " + " ".join(f"{fmt(r.get(c)):>10s}" for c in cols))
        if base:
            diffs = []
            for c in cols:
                a, b = r.get(c), base.get(c)
                diffs.append(f"{(a - b):+.2f}" if isinstance(a, (int, float)) and isinstance(b, (int, float)) else "")
            print(f"{'   Δ vs baseline':44s} " + " ".join(f"{d:>10s}" for d in diffs))

    if args.out:
        write_json(Path(args.out), {"baseline": base, "runs": rows})
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
