"""Build the frozen 30-task retail iteration subset.

Composition (see plan, Phase 3):
  * the 20 "voice-fragile" retail tasks Sierra used for their own annotation set
    (pass in text, fail in clean voice) -- fixed;
  * 10 more tasks chosen deterministically to (a) prefer tasks with `unknown_info`
    (identity lookups by name+zip instead of email -> behaviour 1), (b) prefer
    tasks with >= 2 write actions (multi-step), and (c) balance the five regular
    personas so each appears >= 5 times across the 30 (persona per task is fixed
    in tasks_voice.json).

Run once, commit the output. Re-running with the same tau2-bench data gives the
same file.

    uv run --project external/tau2-bench python evals/subsets/select_iter30.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOMAIN_DIR = ROOT / "external/tau2-bench/data/tau2/domains/retail"
OUT = Path(__file__).with_name("retail_iter30.json")

# From src/tau2/scripts/per_task_summary.py (2026-03-10 voice-fragile annotation batch).
VOICE_FRAGILE = [6, 7, 8, 14, 19, 22, 23, 24, 25, 28, 31, 33, 35, 36, 51, 56, 59, 79, 87, 106]

# Retail tools that mutate the DB (from the retail toolkit; used to count write actions).
WRITE_TOOLS = {
    "cancel_pending_order",
    "exchange_delivered_order_items",
    "modify_pending_order_address",
    "modify_pending_order_items",
    "modify_pending_order_payment",
    "modify_user_address",
    "return_delivered_order_items",
}


def main() -> None:
    tasks = {t["id"]: t for t in json.load(open(DOMAIN_DIR / "tasks.json"))}
    voice = json.load(open(DOMAIN_DIR / "tasks_voice.json"))["configs"]

    def persona(tid: str) -> str:
        return voice[tid]["configs"]["regular"]["persona_name"]

    def n_writes(t: dict) -> int:
        return sum(a["name"] in WRITE_TOOLS for a in t["evaluation_criteria"].get("actions", []))

    def has_unknown(t: dict) -> bool:
        instr = t["user_scenario"]["instructions"]
        return bool(isinstance(instr, dict) and instr.get("unknown_info"))

    chosen = [str(i) for i in VOICE_FRAGILE]
    persona_counts = Counter(persona(t) for t in chosen)

    # Candidate pool, deterministic order: unknown_info first, then more writes, then id.
    pool = [
        tid for tid in sorted(tasks, key=lambda x: int(x)) if tid not in chosen
    ]
    pool.sort(key=lambda tid: (not has_unknown(tasks[tid]), -n_writes(tasks[tid]), int(tid)))

    while len(chosen) < 30 and pool:
        # Prefer the persona currently least represented.
        min_persona = min(
            ["mildred_kaplan", "arjun_roy", "wei_lin", "mamadou_diallo", "priya_patil"],
            key=lambda p: persona_counts[p],
        )
        pick = next((tid for tid in pool if persona(tid) == min_persona), pool[0])
        pool.remove(pick)
        chosen.append(pick)
        persona_counts[persona(pick)] += 1

    out = {
        "domain": "retail",
        "description": "Frozen 30-task iteration subset: 20 Sierra voice-fragile tasks + 10 "
        "persona-balanced tasks preferring unknown_info identity lookups and multi-write flows.",
        "task_ids": chosen,
        "voice_fragile_ids": [str(i) for i in VOICE_FRAGILE],
        "persona_counts": dict(persona_counts),
        "per_task": {
            tid: {
                "persona": persona(tid),
                "environment": voice[tid]["configs"]["regular"]["environment"],
                "unknown_info": has_unknown(tasks[tid]),
                "write_actions": n_writes(tasks[tid]),
            }
            for tid in chosen
        },
    }
    OUT.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {OUT} ({len(chosen)} tasks); personas={dict(persona_counts)}")


if __name__ == "__main__":
    main()
