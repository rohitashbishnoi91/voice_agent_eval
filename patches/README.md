# Patches against sierra-research/tau2-bench

The evaluation harness runs inside a clone of [tau2-bench](https://github.com/sierra-research/tau2-bench)
pinned at commit `b7ea907` (2026-09-17). Everything we changed or added there is in these patches, so the
benchmark itself stays standard and the diff is reviewable:

| patch | contents |
|---|---|
| `tau2-bench-livekit-session.patch` | the `livekit_session` audio-native provider (real LiveKit `AgentSession` bridged into τ-bench's 200 ms tick loop), CLI/config plumbing (`--audio-native-provider livekit_session`, `--agent-prompt-file`, `--cascaded-config <preset>`), keyless user-simulator TTS backends (Kokoro / edge-tts), local-model adaptations in `llm_utils.generate` (chat-format fix, user-sim guard and token cap; all gated to Ollama models), zero-cost pricing for `openai_compat`, two tests |
| `tau2-bench-uv-lock.patch` | lockfile changes (livekit-agents 1.5.1 → 1.8.3, kokoro-onnx, edge-tts, faster-whisper) |

```bash
git clone https://github.com/sierra-research/tau2-bench external/tau2-bench
cd external/tau2-bench && git checkout b7ea907
git apply ../../patches/tau2-bench-livekit-session.patch ../../patches/tau2-bench-uv-lock.patch
uv sync --extra voice
```
Nothing in τ-bench's tasks, policies, tools, evaluator or user-simulator prompts is modified. The deviations that
apply only when `STACK=local` are documented in the top-level README ("Local-stack deviations").
