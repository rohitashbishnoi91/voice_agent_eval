#!/usr/bin/env bash
# Copy the small, reviewable outputs of a τ-bench run into runs/results/<run> (tracked in git):
# results index, interaction metrics, behaviour evals, sleep flags, inspector timeline/summary.
# The per-simulation files with audio (≈100 MB/run) stay in external/tau2-bench/data/simulations (ignored).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for run in "$@"; do
  src="$ROOT/external/tau2-bench/data/simulations/$run"; dst="$ROOT/runs/results/$run"; mkdir -p "$dst"
  for f in results.json interaction_metrics.json b1_identifiers.json b2_selectivity.json b3_bargein.json sleep_gaps.json; do
    [[ -f "$src/$f" ]] && cp "$src/$f" "$dst/"
  done
  (cd "$ROOT" && uv run --project external/tau2-bench python evals/inspect_run.py "$src" --json "$dst/inspect_summary.json" 2>/dev/null > "$dst/timeline.txt")
  echo "exported $run → runs/results/$run ($(du -sh "$dst" | cut -f1))"
done
