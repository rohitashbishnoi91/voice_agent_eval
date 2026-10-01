# LiveKit voice-agent evals on τ-bench (retail)

Evaluations for a [LiveKit Agents](https://docs.livekit.io/agents/) voice agent run against
[τ-bench / τ³-Voice](https://taubench.com) retail tasks, built to find behaviours where a base model fails
and to measure whether prompt changes fix them. Everything runs **keyless on a laptop** (Ollama, faster-whisper,
Kokoro); the same code runs the paid leaderboard stack (OpenAI + Deepgram + ElevenLabs) by switching a preset.

> Status (2026-10-01): baseline + three prompt versions run on a 10-task subset; results table below.

## What was built

| piece | where |
|---|---|
| **Agent under test** — a real LiveKit `AgentSession` (STT → LLM → TTS, LiveKit turn detector, VAD interruptions, LiveKit function tools) bridged into τ-bench's full-duplex 200 ms tick loop as a new audio-native provider `livekit_session` | `patches/tau2-bench-livekit-session.patch` → `src/tau2/voice/audio_native/livekit_session/` |
| **Behaviour evals** (scored from τ-bench trajectories + a LiveKit-side event sidecar written per call) | `evals/b1_identifiers.py`, `evals/b2_selectivity.py`, `evals/b3_bargein.py`, `evals/inspect_run.py`, `evals/report.py` |
| **Text pre-screens** (seconds per case, no audio; gate prompt candidates before a 2.5 h voice run) | `evals/text/test_b1_spelling.py`, `evals/text/test_b2_backchannel.py` |
| **Prompt versions** (one hypothesis per file; the retail policy and tools are τ-bench's, untouched) | `agent/prompts/` |
| **Run scripts** (local stack up/down, canonical τ-bench invocations, post-processing) | `scripts/` |
| **Knowledge base** extracted from the LiveKit docs and taubench.com, plus the running log of decisions and findings | `CLAUDE.md`, `notes/`, `runs/analysis/` |

### The three behaviours

| # | behaviour | why it matters on a phone call | eval signal (ours) | τ-bench signal |
|---|---|---|---|---|
| 1 | **Identifier capture** under noise/accents: names, emails, zip codes spelled letter by letter | authentication gates every retail task; the leaderboard's cascaded baseline fails auth in 44 % of calls | `b1`: gold identifiers from the task + DB record vs. what the agent *heard* (sidecar transcripts) vs. what it *used* in tool arguments → `used_correct` / `used_wrong_heard_ok` (LLM reconstruction failure) / `used_wrong_misheard` (STT failure) / `never_recognised`; spell-request rate; auth success | auth classifier, pass^1 |
| 2 | **Selectivity**: not treating "uh-huh", vocal tics or side-talk as the caller's turn | every false turn truncates the agent mid-sentence and triggers a re-ask | `b2`: S_BC/S_VT/S_ND per persona/environment, plus LiveKit-side `agent_false_interruption`, cancelled generations, reply length; pre-screen feeds "uh-huh"/"Amen." turns | S_BC, S_VT, S_ND |
| 3 | **Barge-in**: yielding fast and completely when the caller really interrupts | callers interrupt long confirmations; an agent that talks over them loses the information | `b3`: R_Y, L_Y (p50/p95), I_A, overlap share, false-interruption resumes | R_Y, L_Y, I_A |
| + | **Grounded action** (found while running, scored by `inspect_run.py`): tool calls spoken as JSON, placeholder arguments (`#W0000000`, `"the email the customer gave"`), claims of checking/cancelling with no tool call | the dominant failure of 7–8B local models; makes tasks unwinnable | `json_spoken`, `placeholder_arg_calls`, `unbacked_claims` per call | tool errors |

## Results (10-task retail subset, `regular` speech complexity, local stack)

| version | pass^1 | auth | b1 wrong (LLM / STT) | S_BC / S_VT / S_ND | R_Y / L_Y / I_A | R_R / L_R | tool calls (errors) | unbacked claims | placeholder args | JSON spoken | agent speech share |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **v0 (τ-bench prompt)** | 0/10 | 0 % | 0.00 / 0.11 | 0.32 / 0.39 / 0.72 | 0.90 / 0.77 s / 0.13 | 89 % / 4.74 s | 4 (2) | 2 | 0 | 0 | 35 % |
| **v1 backchannels+brevity** | 0/10 | 0 % | 0.00 / 0.16 | 0.26 / 0.47 / 0.71 | 0.93 / 0.84 s / 0.15 | 87 % / 3.91 s | 13 (13) | 6 | 3 | 0 | 31 % |
| **v3 + authenticate first** | 0/10 | 0 % | 0.00 / 0.16 | 0.22 / 0.56 / 0.85 | 0.78 / 0.75 s / 0.06 | 76 % / 4.75 s | 10 (9) | 9 | 0 | 0 | 22 % |
| *leaderboard cascaded baseline (gpt-4.1 + Deepgram, 114 tasks, paid)* | 28.9 % | 56 % | – | 0.57 / 0.50 / 0.52 | 0.99 / 0.84 s / 0.58 | 77 % / 4.02 s | – | – | – | – | – |

Leaderboard numbers are from Sierra's paid gpt-4.1 + Deepgram run and are shown only as an anchor; the local
turn-taking numbers (R_R, L_R, I_A) are not comparable (see deviations). Sleep-contaminated calls are flagged in each
run's `sleep_gaps.json`.

**What the iterations showed** (details per version in `runs/analysis/v*_findings.md`):
- **v0 → v1 (behaviour 2 + brevity):** replies halved (74 → 41 tokens median), LLM generations cancelled by caller
  speech halved (58 → 26), LiveKit false interruptions halved (16 → 9) — the LLM-side half of the backchannel problem
  is prompt-addressable. τ-bench's S_BC did **not** improve (0.32 → 0.26): on this stack the agent is cut off at the
  audio level (VAD interruption on a 0.5 s "mm-hmm") before any transcript exists, which only configuration can change
  (`local-bc` preset prepared: `min_interruption_words=2`, `min_interruption_duration=1.0`).
- **v1 → v3 (authenticate first, act in the same turn):** lookups 1 → 3 → 8; behaviour 1 is finally exercised in voice,
  and `b1` attributes the failures: whisper small.en hears "May" for "Mei" (STT), the agent passes the raw spelled
  transcript `"S.O.F.I."` as a first name (LLM reconstruction), a zip is truncated by a turn split, and clearly heard
  values are re-asked to be spelled (the v0 rule over-applied). Authentications stayed at 0/10.
- **v3 → v4 (saying is not doing):** the first v4 draft — three stacked sections, 667 words — regressed on the
  pre-screen to no tool calls at all and a reply in Chinese: for a 7B model, prompt length is itself a failure mode.
  Rewritten as one compact block (355 words) it recovered on the pre-screen; voice result below.
- **Grounded action is the base model's dominant failure:** qwen never fabricates ids (unlike llama3.1:8b, which spoke
  JSON aloud and called `cancel_pending_order("#W0000000")` on turn one), but it narrates — "let me check your account
  now", even "your order is being cancelled" — without calling a tool (`unbacked_claims` 2 → 6 → 9 across v0/v1/v3).
- **Barge-in (behaviour 3) is not where this base fails:** R_Y 0.78–0.93, L_Y 0.75–0.84 s, I_A ≤ 0.15 in every version;
  the eval is in place and the LiveKit `agent_false_interruption` resumes are counted, but no prompt work was spent on it.
- **No version passed a task** (pass^1 0/10 throughout). The subset is 8/10 voice-fragile tasks on which Sierra's
  paid cascaded baseline also fails, run by a 7B model on whisper-small; pass^1 was never the lever here — the
  behaviour metrics are. The same scripts run unchanged on the paid preset for comparable numbers.

## How to run

```bash
# 1. τ-bench + our patches (see patches/README.md)
git clone https://github.com/sierra-research/tau2-bench external/tau2-bench
(cd external/tau2-bench && git checkout b7ea907 && git apply ../../patches/*.patch && uv sync --extra voice)
# 2. local inference stack (Ollama qwen2.5:7b, faster-whisper small.en, Kokoro) — fully keyless
scripts/local_stack.sh up
# 3. runs
scripts/run_retail.sh smoke                                   # 2 tasks, control conditions
scripts/run_retail.sh subset agent/prompts/v0_tau_cascaded.md v0   # 10-task subset, regular conditions
scripts/run_retail.sh post  <run_dir>                         # τ-bench panel + review + report
uv run --project external/tau2-bench python evals/b1_identifiers.py <run>   # same for b2 / b3 / inspect_run / report
cd external/tau2-bench && uv run pytest ../../evals/text/ -q -s                  # text pre-screens (~30 s each)
# paid, leaderboard-comparable stack: fill external/tau2-bench/.env, then STACK=paid scripts/run_retail.sh ...
```
Long runs must be launched through `scripts/daemonize.py` (adds `caffeinate`; a closed lid or empty battery still
pauses them — `evals/sleep_gaps.py` flags affected simulations so their latency metrics can be excluded).

## Implementation approach

1. **Extract before building.** Two scrape passes (LiveKit agents docs, taubench.com) produced `CLAUDE.md`: how
   LiveKit agents are built and tested natively (`session.run`, `RunResult`, judges, `ChatMessage.metrics`,
   turn-handling options), τ-bench's domains, scoring (reward = DB state × NL assertions, pass^k), the τ³-Voice
   interaction-metric panel, and the leaderboard rows. Behaviours and model candidates were chosen from that.
2. **Test the real thing.** Instead of τ-bench's built-in plugin-level `livekit` provider (Deepgram VAD only),
   a real `AgentSession` — LiveKit turn detector, VAD/adaptive interruption, LiveKit tool execution — is driven
   by τ-bench's tick loop through custom `AudioInput`/`AudioOutput` sinks, so behaviours 2–3 exercise the
   components that actually govern them. The τ-bench tools are exposed as LiveKit raw function tools whose
   bodies await a future resolved by τ-bench's next-tick tool result. A JSONL sidecar records every LiveKit event
   (transcripts, state changes, LLM/STT/TTS metrics, false interruptions, overlapping speech) per call so evals can
   attribute failures to a pipeline stage.
3. **Validate the metrics before trusting them.** The eval scripts were run on Sierra's published cascaded-baseline
   retail trajectories (114 calls) and reproduce the leaderboard panel to two decimals (L_R 4.02, R_R 0.77,
   S_BC 0.57, …). Only then were they pointed at our agent.
4. **Iterate cheaply, then expensively.** Each prompt hypothesis is one file; it is screened in LiveKit text mode in
   seconds (`evals/text/`) and only then run on the frozen 10-task voice subset (~2.5 h locally). Every run is
   post-processed identically (`report.py --baseline`), and failure mining is done on the inspector timelines.

## Approaches considered and trade-offs

- **Paid vs. local stack.** The plan started on gpt-4.1 + Deepgram + ElevenLabs (leaderboard-comparable) and
  moved, at the owner's request, to a fully local keyless stack. Trade-off: no leaderboard comparability for
  turn-taking metrics (local LLM latency is ~10× higher; see deviations), no accented personas (Kokoro has
  US/UK voices only), and a 7B model whose failure profile is different from gpt-4.1's. Gain: unlimited runs at
  zero cost, and the harness, evals and prompts are stack-agnostic (one preset switch).
- **Which local LLM.** llama3.1:8b was tried first: it fabricated tool calls (`#W0000000`), spoke JSON aloud and
  its chat template defeated Ollama's prompt cache (6 s time-to-first-token). qwen2.5:7b keeps the cache warm
  (0.3 s TTFT), never fabricates arguments and authenticates sometimes — its failures are the voice-specific ones
  worth evaluating. Documented with the text pre-screen (llama 0/3, qwen 2/3).
- **Real AgentSession vs. plugin pipeline.** More integration work (thread/plugin registration, tool futures
  across ticks, real-time vs. tick-time pacing) but it is the only way to evaluate LiveKit's own turn detector and
  interruption handling, which is what behaviours 2–3 are about.
- **Subset size.** 30 tasks were planned; the machine (16 GB laptop that sleeps when closed) made that a
  multi-day run, so the iteration subset was cut to 10 tasks (two per persona, 8 of Sierra's voice-fragile ids)
  and the call cap to 10 minutes — a 7B agent that has not acted in 10 minutes never does.
- **Metric attribution vs. simplicity.** `b1` splits wrong identifiers into "heard right, used wrong" (LLM) and
  "never heard" (STT) using the sidecar transcripts; cruder designs (string match on tool args) would have hidden
  the most actionable finding (the agent had every letter and still called with `"Mia. Garcia2723@example, com"`).

## Local-stack deviations from the leaderboard protocol

All gated behind `STACK=local` / Ollama model names; nothing applies to the paid stack. They exist to make a
7B-on-a-laptop loop *run*, not to flatter it, and they apply identically to every prompt version so versions
remain comparable with each other.

| deviation | why | effect on comparability |
|---|---|---|
| caller patience `--wait-to-respond-other/self` 15 s (τ-bench: 1 s / 5 s) | a local tool turn (LLM → tool → LLM) takes ~12 s; at 1 s every reply became a barge-in and the agent never spoke | L_R, R_R, I_A not comparable to the leaderboard |
| call cap 600 s (τ-bench: 1200 s) | see subset trade-off | none between versions |
| LiveKit LLM timeout 40 s (SDK default 10 s) | cold prompt evaluation of the 4k-token retail prompt | none |
| user-simulator adaptations in `llm_utils.generate` (Ollama only): mid-conversation system notes rewritten as user notes; a late "never invent identifiers" reminder; 400-token cap | llama/qwen 7B user simulators role-flipped, adopted the agent's placeholder names ("D-O-E"), invented order ids, and once generated an 8.9k-token turn | user-simulator *prompts* are unchanged; the guard removes failures that were the simulator's, not the agent's |
| Kokoro voices (no accents), whisper small.en STT | keyless | accent dimension of `regular` absent; STT weaker than Deepgram nova-3 |

## Future improvements

- **Run the paid preset** (`cascaded-session`: gpt-4.1 + Deepgram + ElevenLabs) on the full 114 tasks with the
  same evals — the harness is ready; only keys are missing. That is the leaderboard-comparable baseline.
- **Behaviour 2 is mostly configuration on this stack.** The audio-level yield (VAD interruption on a 0.5 s
  "mm-hmm") is not prompt-addressable; the prepared `local-bc` preset (`min_interruption_words=2`,
  `min_interruption_duration=1.0`) and LiveKit's adaptive interruption model (cloud) should be ablated to bound
  what prompts can reach.
- **Per-call attribution in b1** (match each lookup's argument to the transcript segment just before it) instead
  of per-call aggregates.
- **A stronger local user simulator** (14B class once memory allows) — the 7B simulator still drifts and invents.
- **LiveKit-native regression suite**: the text pre-screens already use `session.run`; adding `JudgeGroup`
  judges and LiveKit Cloud agent simulations would move the cheap tier into CI.
- **More behaviours**: the inspector already scores grounded action; behaviours 4–6 from `CLAUDE.md` Part 9
  (latency under tool use, graceful silence handling, policy adherence under pressure) have signals in the sidecar.
