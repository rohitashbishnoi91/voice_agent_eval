"""Build the README results table from runs/results/<run>/ exports.

    python evals/results_table.py runs/results/retail_subset_local_v0_tau_cascaded_v0 runs/results/... [--write-readme]
Columns: pass^1, auth, b1 wrongLLM/wrongSTT, S_BC/S_VT/S_ND, R_Y/L_Y/I_A, tool calls, unbacked claims,
placeholder args, reply tokens (sidecar not needed: inspect_summary.json carries the counts).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

LABELS = {"v0_tau_cascaded": "v0 (τ-bench prompt)", "v1_backchannel_concise": "v1 backchannels+brevity",
          "v2_identifier_readback": "v2 identifier read-back", "v3_authenticate_first": "v3 + authenticate first",
          "v4_saying_is_not_doing": "v4 + saying is not doing"}


def load(d: Path) -> dict:
    j = lambda n: json.load(open(d / n)) if (d / n).exists() else {}
    im = j("interaction_metrics.json").get("overall", {})
    b1 = j("b1_identifiers.json").get("summary", {})
    b2 = j("b2_selectivity.json").get("summary", j("b2_selectivity.json").get("by_group", {}).get("all", {}))
    b3 = j("b3_bargein.json").get("summary", j("b3_bargein.json").get("by_group", {}).get("all", {}))
    ins = j("inspect_summary.json") or []
    n = len(ins) or 1
    rewards = [x.get("reward") or 0 for x in ins]
    row = {
        "run": d.name,
        "label": next((v for k, v in LABELS.items() if k in d.name), d.name),
        "n": len(ins),
        "pass1": sum(1 for r in rewards if r >= 1.0),
        "auth": b1.get("auth_success_rate"),
        "wrongLLM": b1.get("entity_used_wrong_heard_ok"),
        "wrongSTT": b1.get("entity_used_wrong_misheard"),
        "S_BC": im.get("selectivity_backchannel"), "S_VT": im.get("selectivity_vocal_tic"), "S_ND": im.get("selectivity_non_directed"),
        "R_Y": im.get("yield_rate"), "L_Y": im.get("yield_latency_mean"), "I_A": im.get("agent_interruption_rate"),
        "R_R": im.get("response_rate"), "L_R": im.get("response_latency_mean"),
        "tools": sum(x.get("tool_calls", 0) for x in ins), "tool_err": sum(x.get("tool_errors", 0) for x in ins),
        "claims": sum(x.get("unbacked_claims", 0) for x in ins), "placeholders": sum(x.get("placeholder_arg_calls", 0) for x in ins),
        "json": sum(x.get("json_spoken", 0) for x in ins),
        "agent_speech": sum(x.get("agent_speech_ticks", 0) for x in ins) / max(sum(x.get("ticks", 0) for x in ins), 1),
    }
    return row


def f(x, nd=2, pct=False):
    if x is None:
        return "–"
    return f"{100*x:.0f} %" if pct else f"{x:.{nd}f}"


def table(rows: list[dict]) -> str:
    hdr = ("| version | pass^1 | auth | b1 wrong (LLM / STT) | S_BC / S_VT / S_ND | R_Y / L_Y / I_A | R_R / L_R | tool calls (errors) | unbacked claims | placeholder args | JSON spoken | agent speech share |\n"
           "|---|---|---|---|---|---|---|---|---|---|---|---|")
    out = [hdr]
    for r in rows:
        out.append(f"| **{r['label']}** | {r['pass1']}/{r['n']} | {f(r['auth'], pct=True)} | {f(r['wrongLLM'])} / {f(r['wrongSTT'])} | "
                   f"{f(r['S_BC'])} / {f(r['S_VT'])} / {f(r['S_ND'])} | {f(r['R_Y'])} / {f(r['L_Y'])} s / {f(r['I_A'])} | {f(r['R_R'], pct=True)} / {f(r['L_R'])} s | "
                   f"{r['tools']} ({r['tool_err']}) | {r['claims']} | {r['placeholders']} | {r['json']} | {f(r['agent_speech'], pct=True)} |")
    out.append("| *leaderboard cascaded baseline (gpt-4.1 + Deepgram, 114 tasks, paid)* | 28.9 % | 56 % | – | 0.57 / 0.50 / 0.52 | 0.99 / 0.84 s / 0.58 | 77 % / 4.02 s | – | – | – | – | – |")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--write-readme", action="store_true")
    a = ap.parse_args()
    rows = [load(Path(r)) for r in a.runs]
    t = table(rows)
    print(t)
    if a.write_readme:
        p = Path(__file__).resolve().parents[1] / "README.md"
        s = p.read_text()
        # replace only the contiguous table block (lines starting with '|') right after the heading,
        # leaving the narrative that follows it untouched
        s = re.sub(r"(## Results \(10-task retail subset, `regular` speech complexity, local stack\)\n\n)((?:\|[^\n]*\n?)+)",
                   lambda m: m.group(1) + t + "\n", s)
        p.write_text(s)
        print("README results table updated")


if __name__ == "__main__":
    main()
