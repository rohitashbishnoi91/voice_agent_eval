"""Behaviour 2 pre-screen (text mode, keyless on the local stack).

Simulates what the cascade delivers to the LLM when the caller backchannels:
short acknowledgements ("uh-huh", "mm-hmm") and whisper's garbled renderings
of them ("Amen.", "and mem him"). Asserts the agent does NOT treat them as a
turn to answer (no clarification request, no re-asking the previous question)
and keeps normal replies short.

    cd external/tau2-bench && uv run pytest ../../evals/text/test_b2_backchannel.py -q -s
    TAU2_AGENT_PROMPT_FILE=agent/prompts/v1_backchannel_concise.md uv run pytest ...
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent.retail_agent import build_text_session, build_voice_session_config  # noqa: E402
from evals.text.test_b1_spelling import _events, _messages, _llm_reachable, PRESET  # noqa: E402

PROMPT = os.getenv("TAU2_AGENT_PROMPT_FILE")
MAX_WORDS = int(os.getenv("TAU2_PRESCREEN_MAX_WORDS", "45"))
CLARIFY_RE = re.compile(r"(didn'?t (quite )?catch|did not (quite )?catch|what you meant|could you (please )?(clarify|repeat)|i'?m sorry, i)", re.I)

pytestmark = pytest.mark.skipif(not _llm_reachable(), reason=f"LLM endpoint for preset {PRESET!r} not available")

OPENER = "hi i want to exchange two items from a recent order"
BACKCHANNELS = ["uh-huh", "mm-hmm", "Amen.", "Okay.", "and mem him"]


def _words(s: str) -> int:
    return len(s.split())


def _similar(a: str, b: str) -> bool:
    """Crude 'same question again' check: high token overlap between two replies."""
    ta, tb = set(re.findall(r"[a-z']+", a.lower())), set(re.findall(r"[a-z']+", b.lower()))
    return bool(ta) and len(ta & tb) / len(ta | tb) > 0.6


@pytest.mark.parametrize("bc", BACKCHANNELS)
def test_backchannel_not_answered(bc):
    import asyncio

    asyncio.run(_run(bc))


async def _run(bc: str):
    session, rva = build_text_session(prompt_file=PROMPT, preset=PRESET)
    async with session:
        await session.start(rva.agent)
        first = _messages(_events(await session.run(user_input=OPENER)))
        assert first, "agent said nothing to the opener"
        reply = " ".join(first)
        print(f"\n[{bc!r}] opener reply ({_words(reply)} w): {reply[:160]}")
        assert _words(reply) <= MAX_WORDS, f"opener reply too long for voice: {_words(reply)} words"

        res = await session.run(user_input=bc)
        msgs = _messages(_events(res))
        bc_reply = " ".join(msgs)
        print(f"[{bc!r}] backchannel reply ({_words(bc_reply)} w): {bc_reply[:160]!r}")
        # 1. never treats the backchannel as something to clarify
        assert not CLARIFY_RE.search(bc_reply), f"asked for clarification of a backchannel: {bc_reply[:200]}"
        # 2. does not re-ask the same question in full
        assert not (_words(bc_reply) > 12 and _similar(bc_reply, reply)), f"re-asked the previous question: {bc_reply[:200]}"
        # 3. whatever it says stays short
        assert _words(bc_reply) <= MAX_WORDS, f"backchannel reply too long: {_words(bc_reply)} words"
