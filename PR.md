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
| version | pass^1 | auth | b1 wrong (LLM / STT) | S_BC / S_VT / S_ND | R_Y / L_Y / I_A | R_R / L_R | tool calls (errors) | unbacked claims | placeholder args | JSON spoken | agent speech share |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **v0 (τ-bench prompt)** | 0/10 | 0 % | 0.00 / 0.11 | 0.32 / 0.39 / 0.72 | 0.90 / 0.77 s / 0.13 | 89 % / 4.74 s | 4 (2) | 2 | 0 | 0 | 35 % |
| **v1 backchannels+brevity** | 0/10 | 0 % | 0.00 / 0.16 | 0.26 / 0.47 / 0.71 | 0.93 / 0.84 s / 0.15 | 87 % / 3.91 s | 13 (13) | 6 | 3 | 0 | 31 % |
| **v3 + authenticate first** | 0/10 | 0 % | 0.00 / 0.16 | 0.22 / 0.56 / 0.85 | 0.78 / 0.75 s / 0.06 | 76 % / 4.75 s | 10 (9) | 9 | 0 | 0 | 22 % |
| **v4 + saying is not doing** | 0/10 | 0 % | 0.00 / 0.16 | 0.21 / 0.81 / 0.68 | 0.74 / 0.87 s / 0.09 | 68 % / 4.25 s | 7 (7) | 8 | 0 | 0 | 25 % |
| *leaderboard cascaded baseline (gpt-4.1 + Deepgram, 114 tasks, paid)* | 28.9 % | 56 % | – | 0.57 / 0.50 / 0.52 | 0.99 / 0.84 s / 0.58 | 77 % / 4.02 s | – | – | – | – | – |

- **Prompt changes moved what the LLM controls** (reply length −45 %, cancelled generations −55 %, lookups 1 → 8, authenticate-first ordering) and the evals measure each of them.
- **They did not move pass^1 or authentication (0/10 in all versions)**: the residual failures are STT (`Mei` → `May`, spelled letters) and VAD-level interruption, which the evals attribute explicitly (`used_wrong_misheard`, `agent_false_interruption`).
- Barge-in (behaviour 3) turned out not to be a failure of this base; grounded action (`unbacked_claims`, placeholder args, spoken JSON) is, and is scored.

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
