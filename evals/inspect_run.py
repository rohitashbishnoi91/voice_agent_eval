"""Print a readable per-simulation timeline of a τ-bench voice run.

For every simulation: outcome line (reward, termination, duration, speech-tick
shares, tool errors), then the merged timeline of what the simulated user said,
what the agent said, every tool call with its arguments and whether the tool
returned an error. This is the "read the conversation" step of failure mining.

    uv run --project external/tau2-bench python evals/inspect_run.py <run dir or name> [--task 6] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.common import (  # noqa: E402
    agent_tool_calls,
    agent_transcript,
    load_results,
    persona_of,
    resolve_run_dir,
    user_transcript,
    write_json,
)


# tool arguments that are placeholders rather than values the caller gave
PLACEHOLDER_RE = re.compile(
    r"^(none|null|n/a|unknown|not (found|provided|available)|provided|customer|user|the |a |an |#W0+$|#W1234567|12345|gift_card_0+|"
    r"johndoe|john|doe|.*(customer|provided|user does|the email|please|not have).*)$",
    re.I,
)


def _placeholder_args(calls):
    n = 0
    for c in calls:
        for v in (c.get("arguments") or {}).values():
            for x in (v if isinstance(v, list) else [v]):
                if isinstance(x, str) and PLACEHOLDER_RE.match(x.strip()):
                    n += 1
                    break
    return n


# agent claims to be acting / to have acted; counted when no tool call follows within CLAIM_WINDOW ticks
CLAIM_RE = re.compile(
    r"(let me (check|look|find|pull|verify|locate|see)|one moment|just a moment|i(?:'ll| will) (check|look|find|locate|process|update|cancel)|"
    r"(is|has been|will be) (being )?(cancel+ed|updated|processed|refunded|exchanged|returned)|i(?:'ve| have) (updated|cancel+ed|found|located))",
    re.I,
)
CLAIM_WINDOW = 50  # ticks = 10 s


def _unbacked_claims(sim, calls):
    call_ticks = sorted(c["tick"] for c in calls)
    n = 0
    for t, text in agent_transcript(sim):
        if CLAIM_RE.search(text) and not any(t <= ct <= t + CLAIM_WINDOW for ct in call_ticks):
            n += 1
    return n


JSON_SPOKEN_RE = re.compile(r'\{\s*"name"\s*:|"parameters"\s*:|function call|\bfunction\b.*\(', re.I)


def _speech_ticks(sim):
    a = u = 0
    for t in sim.ticks or []:
        if t.agent_chunk is not None and getattr(t.agent_chunk, "contains_speech", False):
            a += 1
        if t.user_chunk is not None and getattr(t.user_chunk, "contains_speech", False):
            u += 1
    return a, u


def _tool_errors(sim):
    """{tool_call_id: (is_error, content)} from every tick's agent tool results."""
    out = {}
    for t in sim.ticks or []:
        for r in t.agent_tool_results or []:
            out[r.id] = (bool(getattr(r, "error", False)), (r.content or "")[:160])
    return out


def _merge_runs(items):
    """Collapse consecutive per-tick fragments from the same speaker into one line."""
    merged = []
    for tick, who, text in items:
        if merged and merged[-1][1] == who and who in ("USER", "AGENT") and tick - merged[-1][2] <= 3:
            merged[-1][3] += (text if who == "USER" else " " + text)  # user transcript is split per tick mid-word
            merged[-1][2] = tick
        else:
            merged.append([tick, who, tick, text])
    return merged


def inspect_sim(sim, out):
    n = len(sim.ticks or [])
    a_ticks, u_ticks = _speech_ticks(sim)
    calls = agent_tool_calls(sim)
    errs = _tool_errors(sim)
    n_err = sum(1 for c in calls if errs.get(c["id"], (False, ""))[0])
    reward = sim.reward_info.reward if sim.reward_info else None
    # tool calls the LLM *spoke* instead of emitting (raw JSON / function names read out by TTS)
    n_json_spoken = sum(1 for _, s in agent_transcript(sim) if JSON_SPOKEN_RE.search(s))
    n_placeholder = _placeholder_args(calls)
    n_claims = _unbacked_claims(sim, calls)
    out(f"\n=== task {sim.task_id}  reward={reward}  termination={sim.termination_reason.value if hasattr(sim.termination_reason,'value') else sim.termination_reason}"
        f"  duration={sim.duration:.0f}s  ticks={n}  agent_speech={a_ticks} ({a_ticks/max(n,1):.0%})  user_speech={u_ticks} ({u_ticks/max(n,1):.0%})"
        f"  tool_calls={len(calls)} errors={n_err}  json_spoken={n_json_spoken}  placeholder_arg_calls={n_placeholder}  unbacked_claims={n_claims}  persona={persona_of(sim)}")
    if sim.reward_info and getattr(sim.reward_info, "db_check", None) is not None:
        out(f"    db_check={getattr(sim.reward_info.db_check, 'db_match', sim.reward_info.db_check)}")
    items = [(t, "USER", s) for t, s in user_transcript(sim)]
    items += [(t, "AGENT", s) for t, s in agent_transcript(sim)]
    for c in calls:
        is_err, content = errs.get(c["id"], (None, ""))
        flag = "ERROR " if is_err else ("ok " if is_err is not None else "no-result ")
        items.append((c["tick"], "TOOL", f"{c['name']}({json.dumps(c['arguments'], ensure_ascii=False)}) -> {flag}{content}"))
    items.sort(key=lambda x: (x[0], {"USER": 0, "AGENT": 1, "TOOL": 2}[x[1]]))
    for t0, who, t1, text in _merge_runs(items):
        span = f"{t0*0.2:6.1f}s" if t0 == t1 else f"{t0*0.2:6.1f}-{t1*0.2:.1f}s"
        out(f"  [{span}] {who:5s} {re.sub(r'[ \t]+', ' ', text).strip()}")
    return {
        "task_id": sim.task_id, "reward": reward, "termination": str(sim.termination_reason),
        "duration": sim.duration, "ticks": n, "agent_speech_ticks": a_ticks, "user_speech_ticks": u_ticks,
        "tool_calls": len(calls), "tool_errors": n_err, "json_spoken": n_json_spoken, "placeholder_arg_calls": n_placeholder, "unbacked_claims": n_claims,
        "calls": [dict(c, error=errs.get(c["id"], (None, ""))[0]) for c in calls],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--task", nargs="*", help="only these task ids")
    ap.add_argument("--json", help="also write a machine-readable summary here")
    args = ap.parse_args()
    run_dir = resolve_run_dir(args.run)
    results = load_results(run_dir)
    summary = []
    for sim in results.simulations:
        if args.task and str(sim.task_id) not in args.task:
            continue
        summary.append(inspect_sim(sim, print))
    if args.json:
        write_json(Path(args.json), summary)


if __name__ == "__main__":
    main()
