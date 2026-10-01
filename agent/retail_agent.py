"""LiveKit-native harness for the retail agent under test.

Two entry points:

* ``build_voice_session_config(...)`` – the exact SessionConfig the τ-bench
  ``livekit_session`` provider uses (single source of truth for models and
  turn handling), so LiveKit-side experiments and τ-bench runs share one agent
  definition.
* ``build_text_session(...)`` – an ``AgentSession`` in **text mode** (no
  STT/TTS/VAD) whose tools execute against a fresh in-process τ-bench retail
  environment. Used by ``evals/text/`` (cheap pre-screens with ``session.run``)
  and by the CLI debugger-style checks. Same system prompt as the voice runs.

Run inside the tau2 environment::

    uv run --project external/tau2-bench python -c "import agent.retail_agent"
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
TAU_SRC = ROOT / "external" / "tau2-bench" / "src"
if str(TAU_SRC) not in sys.path:
    sys.path.insert(0, str(TAU_SRC))

PROMPTS_DIR = ROOT / "agent" / "prompts"
DEFAULT_PROMPT = PROMPTS_DIR / "v0_tau_cascaded.md"
DOMAIN = "retail"

PLAIN_TEMPLATE = "{agent_instruction}\n\n{domain_policy}"


def load_prompt(prompt_file: Optional[Path | str] = None) -> str:
    p = Path(prompt_file) if prompt_file else DEFAULT_PROMPT
    return p.read_text().strip()


def build_system_prompt(prompt_file: Optional[Path | str] = None, policy: Optional[str] = None) -> str:
    """Same composition as τ-bench's AUDIO_NATIVE_SYSTEM_PROMPT_PLAIN."""
    if policy is None:
        from tau2.runner import build_environment

        policy = build_environment(DOMAIN).get_policy()
    return PLAIN_TEMPLATE.format(agent_instruction=load_prompt(prompt_file), domain_policy=policy)


def build_voice_session_config(preset: str = "cascaded-session"):
    from tau2.voice.audio_native.livekit_session.config import SESSION_CONFIGS

    return SESSION_CONFIGS[preset]


# ---------------------------------------------------------------------------
# Text-mode AgentSession backed by a real τ-bench retail environment
# ---------------------------------------------------------------------------


def _wrap_env_tools(env):
    """Expose τ-bench environment tools as LiveKit raw function tools that
    execute in-process (no orchestrator, no ticks)."""
    from livekit.agents.llm import ToolError, function_tool

    from tau2.data_model.message import ToolCall

    tools = []
    for tool in env.get_tools():
        schema = tool.openai_schema.get("function", tool.openai_schema)
        name = schema["name"]

        def _make(name=name):
            async def _fn(raw_arguments: dict) -> str:
                msg = env.get_response(ToolCall(id=f"text-{name}", name=name, arguments=dict(raw_arguments or {})))
                if getattr(msg, "error", False):
                    raise ToolError(msg.content or "tool error")
                return msg.content or ""

            _fn.__name__ = name
            return _fn

        tools.append(function_tool(_make(), raw_schema=schema))
    return tools


class RetailVoiceAgent:
    """Factory for the LiveKit ``Agent`` used in text-mode evals."""

    def __init__(self, prompt_file: Optional[Path | str] = None, env=None):
        from livekit.agents import Agent

        from tau2.runner import build_environment

        self.env = env or build_environment(DOMAIN)
        self.system_prompt = build_system_prompt(prompt_file, policy=self.env.get_policy())
        self.tools = _wrap_env_tools(self.env)
        self.agent = Agent(instructions=self.system_prompt, tools=self.tools)


def build_text_llm(preset: Optional[str] = None, llm_model: Optional[str] = None):
    """LLM for text-mode runs. Taken from the τ-bench session preset (default
    ``TAU2_SESSION_PRESET`` or ``local`` → Ollama, no key) so text pre-screens
    and voice runs exercise the same model; ``llm_model`` overrides the name."""
    import os

    from tau2.voice.audio_native.livekit_session.session_provider import build_llm

    cfg = build_voice_session_config(preset or os.getenv("TAU2_SESSION_PRESET", "local")).llm
    llm_model = llm_model or os.getenv("TAU2_AGENT_LLM_MODEL")  # e.g. qwen2.5:7b on the same Ollama
    if llm_model:
        cfg = cfg.model_copy(update={"model": llm_model})
    return build_llm(cfg)


def build_text_session(
    prompt_file: Optional[Path | str] = None,
    llm_model: Optional[str] = None,
    env=None,
    preset: Optional[str] = None,
):
    """Return (session, RetailVoiceAgent). Caller does ``await session.start(rva.agent)``."""
    from livekit.agents import AgentSession

    rva = RetailVoiceAgent(prompt_file=prompt_file, env=env)
    kwargs = {}
    cfg = build_voice_session_config(preset or os.getenv("TAU2_SESSION_PRESET", "local"))
    if getattr(cfg, "llm_timeout_s", None):  # same per-attempt LLM timeout as the voice runs (local: 40 s)
        from livekit.agents import APIConnectOptions
        from livekit.agents.voice.agent_session import SessionConnectOptions

        kwargs["conn_options"] = SessionConnectOptions(llm_conn_options=APIConnectOptions(timeout=cfg.llm_timeout_s))
    session = AgentSession(llm=build_text_llm(preset, llm_model), max_tool_steps=3, **kwargs)
    return session, rva


if __name__ == "__main__":  # quick smoke: print the composed prompt length and tool names
    rva = RetailVoiceAgent()
    print(f"system prompt: {len(rva.system_prompt)} chars; tools: {len(rva.tools)}")
    print(json.dumps([t.info.name for t in rva.tools], indent=1))
