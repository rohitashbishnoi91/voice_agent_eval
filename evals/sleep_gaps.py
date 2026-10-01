"""Flag simulations whose wall clock stalled (laptop sleep) so latency/turn-taking
metrics can be excluded. Scans artifacts/task_*/sim_*/task.log for timestamp gaps.

    python evals/sleep_gaps.py <run dir> [--min-gap 60]
Writes <run dir>/sleep_gaps.json: {sim_id: [{start, end, minutes}], ...}
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.common import resolve_run_dir  # noqa: E402


def gaps_in(log: Path, min_gap: float) -> list[dict]:
    prev, out = None, []
    for line in open(log, errors="ignore"):
        try:
            t = dt.datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            continue
        if prev and (t - prev).total_seconds() > min_gap:
            out.append({"start": prev.isoformat(timespec="seconds"), "end": t.isoformat(timespec="seconds"),
                        "minutes": round((t - prev).total_seconds() / 60, 1)})
        prev = t
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--min-gap", type=float, default=60.0, help="seconds; a tick never legitimately takes this long")
    args = ap.parse_args()
    run_dir = resolve_run_dir(args.run)
    flagged = {}
    for log in sorted(run_dir.glob("artifacts/task_*/sim_*/task.log")):
        g = gaps_in(log, args.min_gap)
        if g:
            flagged[log.parent.name.replace("sim_", "")] = {"task": log.parent.parent.name.replace("task_", ""), "gaps": g}
    (run_dir / "sleep_gaps.json").write_text(json.dumps(flagged, indent=1))
    n_all = len(list(run_dir.glob("artifacts/task_*/sim_*/task.log")))
    print(f"{len(flagged)}/{n_all} simulations have wall-clock gaps > {args.min_gap:.0f}s (sleep):")
    for sid, v in flagged.items():
        print(f"  task {v['task']:>4s} sim {sid[:8]}  {sum(x['minutes'] for x in v['gaps']):6.1f} min in {len(v['gaps'])} gaps")


if __name__ == "__main__":
    main()
