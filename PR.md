# Evals for a LiveKit voice agent on τ-bench retail, with prompt iterations

**TL;DR** — A real LiveKit `AgentSession` is driven by τ-bench's full-duplex tick loop; three behaviours are
scored with validated evals (identifier capture, backchannel selectivity, barge-in) plus a grounded-action score;
a keyless local stack (whisper → qwen2.5:7b → Kokoro) ran a 10-task baseline and prompt versions v1/v3/v4.
Full write-up: [README.md](README.md). Running log of decisions/findings: [CLAUDE.md](CLAUDE.md) Part 10,
[runs/analysis/](runs/analysis/).

## Implementation approach
- τ-bench provider `livekit_session` (patch in `patches/`): custom `AudioInput`/`AudioOutput` sinks, tool bridge
  with cross-tick futures, LiveKit event sidecar per call. Standard τ-bench tasks/policy/tools/evaluator.
- Evals (`evals/`): b1 identifier capture with STT-vs-LLM attribution, b2 selectivity, b3 barge-in, inspector
  (spoken JSON, placeholder args, unbacked claims), report with baseline diff; validated against Sierra's
  published cascaded-baseline trajectories (panel reproduced to 2 decimals).
- Cheap tier: LiveKit text-mode pre-screens (`session.run`) gate every prompt version in ~30 s.
- Prompt versions in `agent/prompts/` (one hypothesis per file; policy untouched).

## Results
RESULTS_PLACEHOLDER

## Approaches considered / trade-offs
See README § "Approaches considered and trade-offs" and § "Local-stack deviations": paid vs local stack,
llama3.1:8b vs qwen2.5:7b, real AgentSession vs plugin pipeline, 30→10 task subset and 10-minute call cap,
user-simulator guards for 7B models.

## Future improvements
See README § "Future improvements": paid preset on all 114 tasks, interruption-config ablation
(`local-bc`, adaptive interruption), per-call b1 attribution, stronger user simulator, LiveKit-native CI tier.

## Deliverables
- Claude Code session transcripts: `transcripts/`
- Loom: (link)
