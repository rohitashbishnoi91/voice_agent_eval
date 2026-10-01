"""Shared helpers for the behaviour evals.

All scripts read finished τ-bench voice runs (directory format:
``results.json`` + ``simulations/sim_*.json`` + ``artifacts/task_<id>/sim_<uuid>/``)
and the optional LiveKit sidecar written by the ``livekit_session`` provider
(``artifacts/task_<id>/sim_<uuid>/livekit_events.jsonl``).

Run with the tau2 environment so ``tau2`` is importable::

    uv run --project external/tau2-bench python evals/<script>.py <run_dir> ...
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
TAU = ROOT / "external" / "tau2-bench"
SIMULATIONS_DIR = TAU / "data" / "simulations"
SUBSET_FILE = ROOT / "evals" / "subsets" / "retail_iter10.json"
RUNS_DIR = ROOT / "runs"


def resolve_run_dir(arg: str) -> Path:
    """Accept an absolute/relative path or a run name under data/simulations."""
    p = Path(arg)
    if p.exists():
        return p.resolve()
    q = SIMULATIONS_DIR / arg
    if q.exists():
        return q.resolve()
    raise FileNotFoundError(f"run not found: {arg} (tried {p} and {q})")


def load_results(run_dir: Path):
    """Load a τ-bench Results object (dir or json format)."""
    from tau2.data_model.simulation import Results

    return Results.load(run_dir)


def load_subset_ids(path: Path = SUBSET_FILE) -> list[str]:
    return [str(x) for x in json.load(open(path))["task_ids"]]


def persona_of(sim) -> Optional[str]:
    env = getattr(sim, "speech_environment", None)
    return getattr(env, "persona_name", None) if env else None


def environment_of(sim) -> Optional[str]:
    env = getattr(sim, "speech_environment", None)
    return getattr(env, "environment", None) if env else None


def sim_artifact_dir(run_dir: Path, sim) -> Optional[Path]:
    d = run_dir / "artifacts" / f"task_{sim.task_id}" / f"sim_{sim.id}"
    return d if d.exists() else None


def load_sidecar(run_dir: Path, sim) -> list[dict[str, Any]]:
    """LiveKit-side events for one simulation (empty if provider != livekit_session)."""
    d = sim_artifact_dir(run_dir, sim)
    if d is None:
        return []
    f = d / "livekit_events.jsonl"
    if not f.exists():
        return []
    out = []
    for line in f.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def agent_tool_calls(sim) -> list[dict[str, Any]]:
    """All agent tool calls in tick order: [{tick, name, arguments, id}]."""
    calls = []
    for t in sim.ticks or []:
        for tc in t.agent_tool_calls or []:
            calls.append({"tick": t.tick_id, "name": tc.name, "arguments": tc.arguments, "id": tc.id})
        if t.agent_chunk and t.agent_chunk.tool_calls:
            for tc in t.agent_chunk.tool_calls:
                if not any(c["id"] == tc.id and c["tick"] == t.tick_id for c in calls):
                    calls.append(
                        {"tick": t.tick_id, "name": tc.name, "arguments": tc.arguments, "id": tc.id}
                    )
    return calls


def agent_transcript(sim) -> list[tuple[int, str]]:
    """(tick, text) for every tick where the agent produced transcript text."""
    out = []
    for t in sim.ticks or []:
        if t.agent_chunk and t.agent_chunk.content:
            out.append((t.tick_id, t.agent_chunk.content))
    return out


def user_transcript(sim) -> list[tuple[int, str]]:
    """(tick, text) of what the *simulated user said* (gold script per tick)."""
    out = []
    for t in sim.ticks or []:
        if t.user_transcript:
            out.append((t.tick_id, t.user_transcript))
        elif t.user_chunk and t.user_chunk.content:
            out.append((t.tick_id, t.user_chunk.content))
    return out


@dataclass
class RunSummary:
    run_dir: Path
    name: str
    n_sims: int = 0
    n_tasks: int = 0
    pass_1: Optional[float] = None
    avg_reward: Optional[float] = None
    rewards_by_task: dict[str, float] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


def summarize_rewards(run_dir: Path, results=None) -> RunSummary:
    results = results or load_results(run_dir)
    rs = RunSummary(run_dir=run_dir, name=run_dir.name)
    rewards: dict[str, list[float]] = {}
    for sim in results.simulations:
        r = sim.reward_info.reward if sim.reward_info else 0.0
        rewards.setdefault(str(sim.task_id), []).append(float(r or 0.0))
    rs.n_sims = len(results.simulations)
    rs.n_tasks = len(rewards)
    if rewards:
        per_task = {k: sum(v) / len(v) for k, v in rewards.items()}
        rs.rewards_by_task = per_task
        rs.avg_reward = sum(per_task.values()) / len(per_task)
        # pass^1 = share of trials with reward 1 (single-trial runs: == avg success)
        successes = [1.0 if x >= 1.0 else 0.0 for v in rewards.values() for x in v]
        rs.pass_1 = 100.0 * sum(successes) / len(successes)
    return rs


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")


def fmt(x: Any, nd: int = 2) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def die(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(1)
