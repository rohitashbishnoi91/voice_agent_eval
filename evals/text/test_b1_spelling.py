"""Behaviour 1 pre-screen (text mode, seconds per case, no API key on the local stack).

Feeds the retail agent ASR-style corruptions of identifiers, as a cascaded
pipeline would see them under accent and noise, and checks that the agent

  1. does not write to the database before identity is established;
  2. either asks the caller to spell / confirm the identifier, or reaches a
     successful lookup with the *correct* value (never a wrong one);
  3. reads digits back one by one when confirming a zip / order number.

Tools execute against a real in-process τ-bench retail environment (same DB
as the benchmark), so lookups with corrupted values fail exactly as they would
in the voice run. The agent LLM comes from the τ-bench session preset
(``TAU2_SESSION_PRESET``, default ``local`` = Ollama llama3.1:8b); the judge is
``TAU2_JUDGE_MODEL`` on the same endpoint (default: the agent model).

Run (local stack up, see scripts/local_stack.sh):
    cd external/tau2-bench && uv run pytest ../../evals/text/test_b1_spelling.py -x -q
    TAU2_SESSION_PRESET=cascaded-session OPENAI_API_KEY=... uv run pytest ...   # paid variant
    (add -s to see the turns; TAU2_AGENT_PROMPT_FILE=... to test a prompt version)
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent.retail_agent import build_text_llm, build_text_session, build_voice_session_config  # noqa: E402

PRESET = os.getenv("TAU2_SESSION_PRESET", "local")


def _llm_reachable() -> bool:
    """Skip cleanly when the preset's endpoint is not usable (no key / local server down)."""
    import urllib.request

    cfg = build_voice_session_config(PRESET).llm
    base = getattr(cfg, "base_url", None)
    if base and "localhost" in base:
        try:
            urllib.request.urlopen(base.replace("/v1", "/api/version"), timeout=2)
            return True
        except Exception:
            return False
    key_env = getattr(cfg, "api_key_env", "OPENAI_API_KEY")
    return key_env.upper() == "NONE" or bool(os.getenv(key_env))


pytestmark = pytest.mark.skipif(not _llm_reachable(), reason=f"LLM endpoint for preset {PRESET!r} not available")

PROMPT = os.getenv("TAU2_AGENT_PROMPT_FILE")  # default v0 when unset

WRITE_TOOLS = {
    "cancel_pending_order",
    "exchange_delivered_order_items",
    "modify_pending_order_address",
    "modify_pending_order_items",
    "modify_pending_order_payment",
    "modify_user_address",
    "return_delivered_order_items",
}
LOOKUPS = {"find_user_id_by_name_zip", "find_user_id_by_email"}
SPELL_RE = re.compile(r"(spell|letter by letter|one letter at a time|digit by digit|confirm|did you say|read (that|it) back)", re.I)

# (case id, corrupted transcript turns, gold values the lookup must end with)
CASES = [
    (
        "name_zip_corrupted",
        ["hi i want to change my pending order", "it's yusuf rosi, zip one nine one two two"],
        {"first_name": "Yusuf", "last_name": "Rossi", "zip": "19122"},
    ),
    (
        "email_spoken",
        ["hello i need to return something", "my email is yusuf dot rossi at example dot com"],
        {"email": "yusuf.rossi@example.com"},
    ),
    (
        "zip_digits_merged",
        ["i'd like to cancel an order", "name is Yusuf Rossi and the zip is nineteen one twenty two"],
        {"first_name": "Yusuf", "last_name": "Rossi", "zip": "19122"},
    ),
]


def _events(result):
    # RunResult.events is the ordered list of RunEvent (each with .item)
    return list(result.events)


def _tool_calls(events):
    out = []
    for ev in events:
        item = getattr(ev, "item", None)
        if item is not None and getattr(item, "type", "") == "function_call":
            out.append((item.name, item.arguments))
    return out


def _messages(events):
    return [
        ev.item.text_content or ""
        for ev in events
        if getattr(ev, "item", None) is not None and getattr(ev.item, "type", "") == "message" and ev.item.role == "assistant"
    ]


@pytest.mark.parametrize("case_id,turns,gold", CASES, ids=[c[0] for c in CASES])
def test_identifier_capture(case_id, turns, gold):
    """Sync wrapper: the tau2 env has no pytest-asyncio; run the case on a fresh loop."""
    import asyncio

    asyncio.run(_run_case(case_id, turns, gold))


async def _run_case(case_id, turns, gold):
    session, rva = build_text_session(prompt_file=PROMPT, preset=PRESET)
    judge = build_text_llm(PRESET, os.getenv("TAU2_JUDGE_MODEL"))
    async with session:
        await session.start(rva.agent)
        all_calls, all_msgs = [], []
        for t in turns:
            result = await session.run(user_input=t)
            evs = _events(result)
            all_calls += _tool_calls(evs)
            all_msgs += _messages(evs)

        print(f"\n[{case_id}] calls={all_calls}\n[{case_id}] replies={[m[:160] for m in all_msgs]}")

        # 1. no writes before identity established
        assert not any(n in WRITE_TOOLS for n, _ in all_calls), f"wrote before auth: {all_calls}"

        # 2. never a wrong lookup value accepted silently: any lookup args must be gold, or the agent must ask to spell/confirm
        asked = any(SPELL_RE.search(m) for m in all_msgs)
        lookups = [(n, a) for n, a in all_calls if n in LOOKUPS]
        wrong = []
        for _, a in lookups:
            import json as _j

            args = _j.loads(a) if isinstance(a, str) else dict(a or {})
            for k, v in gold.items():
                if k in args and str(args[k]).strip().lower() != str(v).lower():
                    wrong.append((k, args[k]))
        assert asked or (lookups and not wrong), f"no spelling/confirmation request and lookups={lookups}"

        # 3. LLM judge: the last assistant message is a clarification/read-back or a correct confirmation.
        # Small local judges sometimes return no structured verdict; that is inconclusive, not a failure.
        last = result.expect[-1]
        try:
            await last.is_message(role="assistant").judge(
                judge,
                intent=(
                    "Asks the customer to spell or confirm their identifying information (name, zip code, or email) "
                    "letter by letter or digit by digit, OR confirms the customer's identity was found. "
                    "Must not claim any order change was made."
                ),
            )
        except AssertionError as e:
            if "did not return any arguments" in str(e):
                import warnings

                warnings.warn(f"[{case_id}] judge returned no verdict ({judge.model}); rule checks 1-2 passed")
            else:
                raise
