"""Behaviour 1 — identifier capture under accent and noise.

For every simulation:
  * ground-truth entities from the task (name, zip, email, and any order ids the
    reference trajectory touches);
  * what the agent USED: arguments of identity/lookup tool calls
    (find_user_id_by_name_zip, find_user_id_by_email, get_order_details, ...);
  * what the agent HEARD (livekit_session provider only): final STT transcripts
    from the LiveKit sidecar;
  * whether the agent asked the caller to spell / read back (regex over the agent
    transcript);
  * τ-bench's authentication classification when `tau2 review` has been run
    (sim.auth_classification), else a heuristic (a successful user lookup call).

Outputs per-simulation records and a summary broken down by persona and
environment, plus entity recall split into "never recognised" vs "recognised
then lost" (heard correctly at least once but the tool call used a wrong value).

Usage:
    uv run --project external/tau2-bench python evals/b1_identifiers.py <run_dir> [--out ...]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from common import (
    agent_tool_calls,
    agent_transcript,
    environment_of,
    fmt,
    load_results,
    load_sidecar,
    persona_of,
    resolve_run_dir,
    user_transcript,
    write_json,
)

LOOKUP_TOOLS = {"find_user_id_by_name_zip", "find_user_id_by_email"}
ORDER_TOOLS = {"get_order_details"}
WRITE_TOOLS = {
    "cancel_pending_order",
    "exchange_delivered_order_items",
    "modify_pending_order_address",
    "modify_pending_order_items",
    "modify_pending_order_payment",
    "modify_user_address",
    "return_delivered_order_items",
}

SPELL_RE = re.compile(
    r"(spell|letter by letter|one letter at a time|digit by digit|one digit at a time|"
    r"can you confirm|let me (read|repeat) (that|it) back|just to confirm|did you say)",
    re.I,
)
LETTERS_RE = re.compile(r"\b(?:[A-Za-z][\s,.\-]+){3,}[A-Za-z]\b")  # e.g. "J, O, H, N"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


_DB_USERS = None


def _db_users() -> dict:
    """Retail DB users keyed by user_id → {first_name, last_name, zip, email}."""
    global _DB_USERS
    if _DB_USERS is None:
        _DB_USERS = {}
        try:
            from common import TAU

            db = json.load(open(TAU / "data" / "tau2" / "domains" / "retail" / "db.json"))
            for uid, u in (db.get("users") or {}).items():
                name = u.get("name") or {}
                _DB_USERS[uid] = {
                    "first_name": name.get("first_name", ""),
                    "last_name": name.get("last_name", ""),
                    "zip": str((u.get("address") or {}).get("zip", "")),
                    "email": (u.get("email") or "").lower(),
                }
        except Exception:
            pass
    return _DB_USERS


_DB_ORDERS = None


def _db_orders() -> dict:
    """Retail DB orders keyed by order_id → user_id."""
    global _DB_ORDERS
    if _DB_ORDERS is None:
        _DB_ORDERS = {}
        try:
            from common import TAU

            db = json.load(open(TAU / "data" / "tau2" / "domains" / "retail" / "db.json"))
            for oid, o in (db.get("orders") or {}).items():
                if o.get("user_id"):
                    _DB_ORDERS[oid] = o["user_id"]
        except Exception:
            pass
    return _DB_ORDERS


def task_entities(task) -> dict:
    """Extract ground-truth identifiers from the task definition (+ DB user record)."""
    ents: dict[str, set[str]] = {"name": set(), "zip": set(), "email": set(), "order_id": set(), "user_id": set()}
    instr = task.user_scenario.instructions
    text = ""
    if isinstance(instr, str):
        text = instr
    else:
        for f in ("known_info", "reason_for_call", "task_instructions", "unknown_info"):
            v = getattr(instr, f, None)
            if v:
                text += "\n" + str(v)
    for m in re.finditer(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b", text):
        ents["email"].add(m.group(0).lower())
    for m in re.finditer(r"\b(?:zip(?: code)?|zipcode)\s*(?:is\s*)?(\d{5})\b", text, re.I):
        ents["zip"].add(m.group(1))
    for m in re.finditer(r"#W\d{6,8}", text):
        ents["order_id"].add(m.group(0))
    for m in re.finditer(r"\byou are ([A-Z][a-z]+(?: [A-Z][a-z]+)+)", text):
        ents["name"].add(m.group(1))
    # Reference trajectory tells us the canonical values the agent must land on.
    for a in task.evaluation_criteria.actions or []:
        args = a.arguments or {}
        if a.name == "find_user_id_by_name_zip":
            ents["name"].add(f"{args.get('first_name','')} {args.get('last_name','')}".strip())
            if args.get("zip"):
                ents["zip"].add(str(args["zip"]))
        if a.name == "find_user_id_by_email" and args.get("email"):
            ents["email"].add(str(args["email"]).lower())
        if args.get("user_id"):
            ents["user_id"].add(str(args["user_id"]))
        if args.get("order_id"):
            ents["order_id"].add(str(args["order_id"]))
    # Orders → owning user (tasks that only touch orders still imply one caller).
    for oid in list(ents["order_id"]):
        owner = _db_orders().get(oid)
        if owner:
            ents["user_id"].add(owner)
    # Enrich from the DB record of the gold user (canonical spelling of name/zip/email).
    for uid in list(ents["user_id"]):
        u = _db_users().get(uid)
        if u:
            if u["first_name"]:
                ents["name"].add(f"{u['first_name']} {u['last_name']}".strip())
            if u["zip"]:
                ents["zip"].add(u["zip"])
            if u["email"]:
                ents["email"].add(u["email"])
    # Identifiers the scenario says the caller does NOT know are not capture targets
    # (e.g. "You do not remember your email address"): drop that kind entirely.
    unknown = str(getattr(instr, "unknown_info", "") or "") if not isinstance(instr, str) else ""
    for kind, pat in (("email", r"e-?mail"), ("zip", r"zip"), ("name", r"\bname\b")):
        if re.search(pat, unknown, re.I):
            ents[kind] = set()
    return {k: sorted(v) for k, v in ents.items() if v}


def used_values(calls: list[dict]) -> dict[str, list[str]]:
    used: dict[str, list[str]] = defaultdict(list)
    for c in calls:
        a = c["arguments"] or {}
        if c["name"] == "find_user_id_by_name_zip":
            used["name"].append(f"{a.get('first_name','')} {a.get('last_name','')}".strip())
            used["zip"].append(str(a.get("zip", "")))
        if c["name"] == "find_user_id_by_email":
            used["email"].append(str(a.get("email", "")).lower())
        if a.get("order_id"):
            used["order_id"].append(str(a["order_id"]))
        if a.get("user_id"):
            used["user_id"].append(str(a["user_id"]))
    return used


def heard_text(run_dir: Path, sim) -> str:
    finals = [
        e.get("transcript", "")
        for e in load_sidecar(run_dir, sim)
        if e.get("type") == "user_input_transcribed" and e.get("is_final")
    ]
    return " ".join(finals)


SPOKEN_KINDS = ("name", "zip", "email")  # what the caller actually says; order/user ids come from tools


def _strict(kind: str, v: str) -> str:
    v = str(v).strip().lower()
    if kind == "email":
        return re.sub(r"\s+", "", v)
    if kind == "zip":
        return re.sub(r"[^0-9]", "", v)
    if kind == "name":
        return re.sub(r"[^a-z0-9]", "", v)  # keep digits: "Garcia2723" as a last name fails the lookup
    return re.sub(r"[^a-z0-9#_]", "", v)


_SPOKEN_TOKENS = re.compile(r"\b(at|dot|underscore|dash|hyphen|period|comma)\b", re.I)


def _norm_spoken(heard: str) -> str:
    """Loose transcript form: drop spoken punctuation words ("at", "dot", "underscore")
    so "m-i-a dot g-a-r-c-i-a at example dot com" contains the normalised gold email."""
    return _norm(_SPOKEN_TOKENS.sub("", heard))


def entity_status(gold: str, used: list[str], heard: str, kind: str = "other") -> str:
    """Attribute a gold identifier's fate to a pipeline stage.

    used_correct            → in a tool-call argument, exactly (normalised)
    used_wrong_heard_ok     → the agent *heard* the right letters/digits but called the tool
                              with something else (LLM reconstruction failure, e.g.
                              "Mia. Garcia2723@example, com")
    used_wrong_misheard     → the tool argument is wrong and the transcript never contained
                              the value (STT failure propagated)
    recognised_not_used     → heard correctly, never used in any lookup of that kind
    never_recognised        → never in the transcript and never used
    never_used              → no sidecar transcript available and never used
    """
    g = _norm(gold)
    used = [u for u in used if _norm(u)]
    # Tool arguments must be *exactly* right for the lookup to work, so compare them
    # strictly per kind (an email with a stray ". " or ", com" fails the lookup even
    # though every letter is present). Transcripts are compared loosely (_norm).
    if any(_strict(kind, u) == _strict(kind, gold) for u in used):
        return "used_correct"
    heard_ok = bool(g) and g in _norm_spoken(heard)
    if used:
        return "used_wrong_heard_ok" if heard_ok else "used_wrong_misheard"
    if heard_ok:
        return "recognised_not_used"
    return "never_recognised" if heard else "never_used"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    run_dir = resolve_run_dir(args.run_dir)
    results = load_results(run_dir)
    tasks = {str(t.id): t for t in results.tasks}

    per_sim = {}
    groups = defaultdict(lambda: defaultdict(int))
    for sim in results.simulations:
        task = tasks.get(str(sim.task_id))
        if task is None:
            continue
        gold = task_entities(task)
        calls = agent_tool_calls(sim)
        used = used_values(calls)
        heard = heard_text(run_dir, sim)
        agent_text = " ".join(t for _, t in agent_transcript(sim))
        user_text = " ".join(t for _, t in user_transcript(sim))

        statuses, grounding = {}, {}
        for kind, values in gold.items():
            for v in values:
                st = entity_status(v, used.get(kind, []), heard, kind)
                (statuses if kind in SPOKEN_KINDS else grounding)[f"{kind}:{v}"] = st

        lookup_calls = [c for c in calls if c["name"] in LOOKUP_TOOLS]
        first_write_tick = next((c["tick"] for c in calls if c["name"] in WRITE_TOOLS), None)
        # Heuristic auth outcome: a lookup call that actually returned a user id
        # (tool result without error). τ-bench's LLM classifier (after
        # `tau2 review`) overrides it.
        auth_heur = any(
            r.id in {c["id"] for c in lookup_calls} and not getattr(r, "error", False)
            for t in (sim.ticks or [])
            for r in (t.agent_tool_results or [])
        )
        auth = (
            sim.auth_classification.status
            if getattr(sim, "auth_classification", None)
            else ("succeeded" if auth_heur else ("failed" if lookup_calls else "not_attempted"))
        )
        spell_requests = len(SPELL_RE.findall(agent_text))
        letter_readbacks = len(LETTERS_RE.findall(agent_text))
        rec = {
            "task_id": str(sim.task_id),
            "persona": persona_of(sim) or "?",
            "environment": environment_of(sim) or "?",
            "reward": sim.reward_info.reward if sim.reward_info else None,
            "auth": auth,
            "auth_source": "classifier" if getattr(sim, "auth_classification", None) else "heuristic",
            "gold": gold,
            "used": dict(used),
            "entity_status": statuses,
            "grounding_status": grounding,  # order/user ids (from tools, not the caller)
            "n_lookup_calls": len(lookup_calls),
            "tick_first_lookup": lookup_calls[0]["tick"] if lookup_calls else None,
            "tick_first_write": first_write_tick,
            "spell_requests": spell_requests,
            "letter_readbacks": letter_readbacks,
            "heard_available": bool(heard),
            "user_said_chars": len(user_text),
        }
        per_sim[sim.id] = rec
        for g in ("all", f"persona={rec['persona']}", f"env={rec['environment']}"):
            G = groups[g]
            G["n"] += 1
            G["auth_succeeded"] += int(auth == "succeeded")
            G["spell_request_sims"] += int(spell_requests > 0)
            G["entities"] += len(statuses)
            for s in statuses.values():
                G[f"ent_{s}"] += 1

    summary = {}
    for g, G in groups.items():
        n = G["n"] or 1
        ents = G["entities"] or 1
        summary[g] = {
            "n": G["n"],
            "auth_success_rate": G["auth_succeeded"] / n,
            "spell_request_rate": G["spell_request_sims"] / n,
            "entity_recall_used": G["ent_used_correct"] / ents,
            "entity_never_recognised": G["ent_never_recognised"] / ents,
            "entity_recognised_not_used": G["ent_recognised_not_used"] / ents,
            "entity_used_wrong_heard_ok": G["ent_used_wrong_heard_ok"] / ents,
            "entity_used_wrong_misheard": G["ent_used_wrong_misheard"] / ents,
            "entity_used_wrong": (G["ent_used_wrong_heard_ok"] + G["ent_used_wrong_misheard"]) / ents,
        }

    out = {
        "run": run_dir.name,
        "definitions": {
            "auth_success_rate": "share of simulations where authentication succeeded (τ-bench LLM classifier if `tau2 review` ran, else a lookup call with gold arguments)",
            "entity_recall_used": "share of gold identifiers that appear verbatim (normalised) in a tool-call argument",
            "entity_never_recognised": "gold identifier never appeared in the agent-heard transcript (needs livekit sidecar)",
            "entity_recognised_not_used": "heard correctly at least once but never used in a lookup of that kind",
            "entity_used_wrong_heard_ok": "heard correctly but the tool argument differs (LLM reconstruction / normalisation failure)",
            "entity_used_wrong_misheard": "tool argument wrong and the transcript never contained the value (STT failure propagated)",
            "spell_request_rate": "share of simulations where the agent asked to spell / read back at least once",
        },
        "summary": summary.get("all", {}),
        "by_group": summary,
        "per_simulation": per_sim,
    }
    out_path = Path(args.out) if args.out else run_dir / "b1_identifiers.json"
    write_json(out_path, out)

    print(f"# Behaviour 1 — identifier capture — {run_dir.name}")
    print(f"{'group':28s} {'n':>4s} {'auth':>6s} {'recall':>7s} {'neverRec':>9s} {'notUsed':>8s} {'wrongLLM':>9s} {'wrongSTT':>9s} {'spellReq':>9s}")
    for g in sorted(summary, key=lambda x: (x != "all", x)):
        s = summary[g]
        print(
            f"{g:28s} {s['n']:>4d} {fmt(s['auth_success_rate']):>6s} {fmt(s['entity_recall_used']):>7s} "
            f"{fmt(s['entity_never_recognised']):>9s} {fmt(s['entity_recognised_not_used']):>8s} "
            f"{fmt(s['entity_used_wrong_heard_ok']):>9s} {fmt(s['entity_used_wrong_misheard']):>9s} {fmt(s['spell_request_rate']):>9s}"
        )
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
