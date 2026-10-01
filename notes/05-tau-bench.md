# Extraction notes 5 — τ-bench (taubench.com) : domains, tasks, scoring, voice track, leaderboard

Sources (fetched 2026-09-27): taubench.com (home, blog/tau3-task-fixes, blog/tau-knowledge, blog/tau-voice-examples), S3 leaderboard manifest + every `submission.json` (29 text, 21 voice, 16 legacy), github.com/sierra-research/tau2-bench (README, docs/evaluation.md, docs/interaction-metrics.md, docs/leaderboard-submission.md, docs/voice-personas.md, docs/running_simulations.md, src/tau2/voice/README.md, src/tau2/domains/README.md, src/tau2/knowledge/README.md, src/tau2/agent/README.md, task files for airline/retail/telecom-small/banking + tasks_voice.json), arXiv 2603.13686 (τ-Voice), arXiv 2603.04370 (τ-Knowledge), Sierra blog posts on τ-voice and τ²-bench, sierra-research.github.io/hyper-tau-bench.

## 1. What τ-bench is (Sierra Research)

"Can AI agents reliably complete real-world tasks? τ-bench measures how well agents converse with users, call tools, retrieve knowledge, and follow policy across enterprise domains — in text and voice." An agent (system under test) talks to an LLM **user simulator**, calls domain **tools** against a **database**, and must follow a domain **policy**. Success is verified against the **final database state**, not against how the conversation sounded.

Evolution:
| Release | Date | What it added | Domains |
|---|---|---|---|
| τ-bench | Jun 2024 (arXiv 2406.12045) | Tool-agent-user interaction; `pass^k` reliability metric | Retail, Airline |
| τ²-bench | Jun 2025 (arXiv 2506.07982) | **Dual control**: the user can act on the world too (user tools); agent must guide the user through steps only they can do. "Drop of up to 25 points" solo → interactive | + Telecom |
| τ³-bench | Feb–Mar 2026 | Task audit (53 tasks fixed: airline 27/50, retail 26/114; airline pass^1 +14–20 pts), **τ-Knowledge / τ³-Banking** (arXiv 2603.04370), **τ-Voice** (arXiv 2603.13686) | + Banking |
| ττ-bench ("hyper-tau-bench") | 2026 | Can coding agents *build* the customer-service agents τ-bench evaluates? Coding harnesses (Claude Code, Codex, OpenCode); human baseline 82.2 %; top Claude Opus 5 23.9 % | Airline, Retail, Telecom, Banking |

Three live leaderboards on taubench.com: **τ²-bench** (text; retail · airline · telecom), **τ³-Banking** (text; knowledge retrieval), **τ³-Voice** (real-time voice; retail · airline · telecom, banking as custom track).

## 2. Domains available to benchmark

| Domain | Tasks (`base` split) | Splits | What the agent does | User-side tools (dual control) | Reward basis observed in `tasks.json` |
|---|---|---|---|---|---|
| `retail` | **114** | base 114 (+train/test) | Order management: returns, exchanges, cancellations, modify pending orders, product inquiries; identity via name+zip/email | none | `[DB, NL_ASSERTION]` ×112, `[DB]` ×2 |
| `airline` | **50** | base 50, train 30, test 20 | Flight changes, cancellations, seat/cabin upgrades, bookings, refunds/compensation under policy (e.g. 24-h rule, insurance) | none | `[DB, COMMUNICATE]` ×50 |
| `telecom` | **114** (`full` 2285 generated, `small` 20) | base = 114 (categories: `mms_issue` 49, `mobile_data_issue` 36, `service_issue` 29; persona difficulty None 40 / Easy 38 / Hard 36) | Troubleshooting with a compositional task generator: fix broken data, MMS, network mode, roaming, data refuel; agent works backend tools, **user** performs device actions (toggle roaming, reboot, check status bar, run speed test) | yes (`toggle_roaming`, `get_status_bar`, `can_send_mms`, speed test …) | `[ENV_ASSERTION]` ×18, `[ENV_ASSERTION, ACTION]` ×2 in the 20-task `small` set (assertions on user device state, e.g. `assert_mobile_data_status`, `assert_internet_speed`; actions with `requestor: user`); no `communicate_info` |
| `banking_knowledge` (τ³-Banking) | **97** | base | Fintech support over an **unstructured KB of 698 docs / 21 product categories / ~195K tokens**, 51 *discoverable* tools (must be unlocked by reading the KB); avg 18.6 docs and 9.5 tool calls per task (1–33) | yes (e.g. `apply_for_credit_card`) | `[DB]` ×88, `[ACTION]` ×9 |
| `mock` | — | — | Lightweight dev domain | — | — |

Voice: every domain ships `tasks_voice.json` (pre-sampled per-task audio configs, `base_seed 42`). Standard voice leaderboard = retail + airline + telecom (**278 tasks**); banking voice runs exist only as custom submissions.

Each domain folder: `policy.md` (agent policy), `db.json`/`db.toml`, optional `user_db`, `tasks.json`, `tasks_voice.json`, `split_tasks.json` (must define `base`), telecom also `tech_support_manual.md`, `tech_support_workflow.md`; banking also `documents/`, `prompts/` (per-retrieval-config policy templates), `tasks/`.

## 3. Task schema (from `tasks.json`)

```
id
description        { purpose, relevant_policies, notes }
user_scenario      { persona, instructions { task_instructions, domain, reason_for_call, known_info, unknown_info } }
initial_state      { initialization_data, initialization_actions[{env_type: user|assistant, func_name, arguments}], message_history }
evaluation_criteria{ actions[], env_assertions[], communicate_info[], nl_assertions[], reward_basis[] }
annotations
telecom adds: ticket (free-text support ticket)
banking adds: user_tools[], required_documents[]
```

Example (airline task 0): user wants to cancel reservation EHGLP3 after 24 h, claims they were told insurance wasn't needed; `nl_assertions: ["Agent should refuse to proceed with the cancellation."]`; `actions: []`; reward `[DB, COMMUNICATE]` → a correct agent writes nothing, DB hash equals initial hash → reward 1.0. Refusal tasks are first-class.

Example (retail task 3): "private person" persona, wants count of t-shirt options and to modify all pending small t-shirts to purple/v-neck/polyester; doesn't remember email → agent must locate by name+zip; reference trajectory of 12 tool calls.

Example (telecom): user abroad in France, roaming off, data plan not to be changed, will accept only "excellent" speed test; user instructions force grounding in user-tool results ("Never make up the results of tool calls"); `initialization_actions` set the device/user state.

Example (banking task_001): consultant earning $100k with a free Rho-Bank+ subscription (only reveal if asked) wants the highest-cash-back card with no annual fee; user applies **themselves** via `apply_for_credit_card` once informed; graded on the resulting DB write; `required_documents` lists the 4 card docs.

## 4. Scoring (docs/evaluation.md)

- Final reward = **product** of the components in `evaluation_criteria.reward_basis`.
- `DB`: after the simulation, the predicted environment's DB hash must equal the target hash, where target = fresh env + replay of `evaluation_criteria.actions`. **`actions` is one reference trajectory, not a requirement**; any tool sequence producing an equivalent end state passes (including no tool calls when refusing is correct).
- `COMMUNICATE`: every string in `communicate_info` must appear (substring) in agent messages.
- `ENV_ASSERTION`: all `env_assertions` pass on the predicted env (telecom user-device state).
- `NL_ASSERTION`: LLM judge returns true for every `nl_assertions` entry (docs mark WIP; retail tasks carry it in `reward_basis`).
- `ACTION`: agent must reproduce each listed action (only ~9 banking tasks).
- Diagnostics always computed: `action_checks`, `partial_action_reward` (m/n reference actions matched, split READ vs WRITE) — a similarity signal, not correctness.
- **`pass^k`** = probability the task is completed in **all k** independent trials (reported k = 1..4). Leaderboard prefers ≥ 4 trials; voice reports pass^1 only. pass^4 gains exceeded pass^1 gains after task fixes → fixes reduced evaluation noise.
- Voice: same DB/communicate evaluation; agent communications judged by LLM to tolerate verbal variability.

## 5. User simulator

- Text: LLM (leaderboard standard **gpt-5.2, reasoning_effort low**; any LLM allowed but reported). Telecom user has tools. Banking uses **flow-based** simulation (explicit flow rules at task-critical junctures, free LLM elsewhere; 194 annotated trajectories → 4 task-critical sim errors).
- Voice (`VOICE_USER_SIMULATOR_VERSION` v1.0 = **GPT-4.1 at temperature 0** generating caller text): four-step pipeline — text generation → **ElevenLabs v3 TTS at 24 kHz** with a persona voice → environmental audio mix → channel degradation. Simulation time is decoupled from wall clock ("major voice provider APIs don't require a simulated call to play out in real-time"), so a capable LLM can drive the user without latency constraints.
- 7 personas (ElevenLabs Voice Design, fixed seed 42; Sierra-internal voice IDs, external users must create their own via `python -m tau2.voice.scripts.setup_voices`):
  - **control** (American, patient): Matt Delaney (Midwest, calm), Lisa Brenner (late-40s suburban, tense/impatient, threatens escalation).
  - **regular** (diverse): Mildred Kaplan (early-80s, needs help with tech), Arjun Roy (Bengali accent, calm, "sounds far away"), Wei Lin (Sichuan Mandarin accent, fast, upbeat), Mamadou Diallo (Senegalese, French accent, hurried), Priya Patil (Marathi Indian English, hurried, phone room tone). Airline regular sampling: Mildred 12, Mamadou 12, Arjun 11, Wei 9, Priya 6; environments outdoor 26 / indoor 24.
- `regular` per-task config (from `tasks_voice.json`): `telephony_enabled true` (**G.711 μ-law 8 kHz always**); background noise file (e.g. `street_and_metro_station_iphone_mic.wav`, TV news, people talking) at **15 dB SNR ± 3 dB drift**; burst noise (car horn, siren, engine idling) **1 event/min at −5..+10 dB SNR**; frame drops **2 %**, ~100 ms bursts, 150 ms each (Gilbert-Elliott); dynamic muffling **20 %** of utterances, 500 ms segment, 1500 Hz cutoff; vocal tics (`[cough]`, `[sneeze]`, `[sniffle]`, min 3 words) and non-directed phrases ("Hold on a second.", "I'm on the phone.", "Shh, quiet." …) at **0.7 events/min**; LLM backchannel with `backchannel_min_threshold 3`; `enable_interruptions true`; persona `verbosity minimal`, `interrupt_tendency interrupts`.
- `control` config: no noise/bursts/frame drops/muffling/tics/non-directed, no interruptions, `interrupt_tendency waits`, still G.711.
- Turn-taking policy: user waits **1.0 s** of agent silence before responding; interruption/backchannel decisions evaluated by LLM prompts **every 2.0 s**; yield window 1–5 s; backchannels ("mm-hmm") triggered on ≥ 2 substantive agent sentences without recent user input.
- Complexity presets (`--speech-complexity`): `control`, `regular` (default, required for leaderboard), ablations `control_audio`, `control_accents`, `control_behavior`, `control_audio_accents`, `control_audio_behavior`, `control_accents_behavior`.
- Orchestration: discrete **200 ms ticks** (`--tick-duration 0.2`), `--max-steps-seconds 600`; agent returns audio + transcript; the user LLM reads the agent's **transcript directly** (no ASR on the agent side, isolating speech degradation on the user channel). Eval transcription providers: Deepgram nova-2/nova-3, OpenAI whisper-1 / gpt-4o-transcribe.
- A "hallucination reviewer" (default Anthropic) checks user-simulator hallucinations; `hallucination_retries` in run config.

## 6. Voice interaction metrics (docs/interaction-metrics.md) — computed offline from tick trajectories, no judges

| Group | Metric | Field | Dir | Definition |
|---|---|---|---|---|
| Latency | L_R | `response_latency_mean` | ↓ | mean s from end of user turn to start of agent response |
| Latency | L_Y | `yield_latency_mean` | ↓ | mean s for agent to stop after a real user interruption |
| Responsiveness | R_R | `response_rate` | ↑ | fraction of user turns answered before the user had to speak again |
| Responsiveness | R_Y | `yield_rate` | ↑ | fraction of interruptions where agent yielded within 2.0 s |
| Interrupt | I_A | `agent_interruption_rate` | ↓ | agent-interrupts-user events per user turn (can exceed 1) |
| Selectivity | S_BC / S_VT / S_ND | `selectivity_*` | ↑ | fraction of backchannels / vocal tics / non-directed speech correctly ignored (yield within 1.0 s while speaking = error; respond within 2.0 s while silent = error) |

Rates hidden below 10 events; Selectivity column = unweighted mean of the three; Overall = per-metric mean across domains. Classification priority when overlapping: backchannel > vocal tic > non-directed > real interruption. Relationship to Full-Duplex-Bench: covers takeover rate, stop/response latency, backchannel handling; deliberately no MOS/prosody/LLM-judged quality.

## 7. τ-Voice paper results (arXiv 2603.13686; models as of early 2026)

- Text ceiling on the same 278 tasks: **GPT-5 (reasoning) 85 %**, **GPT-4.1 (non-reasoning) 54 %**.
- Voice pass@1, all domains: Clean → Realistic: Google gemini-live-2.5-flash-native-audio 31 → 26; OpenAI gpt-realtime-1.5 49 → 35; xAI grok-voice-agent 51 → 38. Voice retains 30–45 % of text SOTA under realistic conditions.
- Clean per-domain: Retail G 45 / O 71 / X 48 (GPT-4.1 text 76); Airline 28 / 48 / 46 (53); Telecom 20 / 28 / 58 (34).
- Realistic interaction quality: latency 1.14 / 0.90 / 1.15 s; responsiveness 69 / 100 / 83 %; interrupt rate 21 / 14 / 84 %; selectivity 54 / 6 / 57 %.
- Ablations (retail): +Noise −2..−4 pp; **+Accents −1..−18 pp** (most damaging on average, provider-specific: xAI −38 % relative); +Turn-taking −14..+4 pp; Realistic −10..−26 pp; effects non-additive.
- **Failure taxonomy** (Voice-Fragile = passes text, fails clean voice; Noise-Fragile = passes clean, fails realistic): agent-caused **79 % / 90 %** — Logical 13/16, **Transcription 10/16**, Hallucination 6/6, VAD/Unresponsive 1/4, Timeout 4/1; user-simulator-caused 21 % / 10 % (logical 9/1, early termination 0/4). Dominant pattern: **authentication bottleneck — agents fail to transcribe spelled names, emails, IDs**; also hallucinated completions without tool calls, multi-step request amnesia, unresponsiveness after repeated failures. "Wrong policy application or missed constraints — independent of audio quality" (logical failures).

## 8. τ-Knowledge / τ³-Banking paper results (arXiv 2603.04370)

- pass^1 with retrieval (Gold / emb-3-large / Qwen3-emb / BM25 / Terminal): GPT-5.2 high 32.7 / 23.5 / 24.7 / 24.5 / **25.5**; GPT-5.2 none 15.7 / 8.3 / 12.4 / 9.5 / 11.6; Claude-4.5-Opus high **39.7** / 18.3 / 19.6 / 17.8 / 24.7; Claude-4.5-Sonnet 33.8 / 17.5 / 17.8 / 16.8 / 22.4; Gemini-3-Pro 33.3 / 12.9 / 12.9 / 13.7 / 15.7; Gemini-3-Flash 36.3 / 18.6 / 18.6 / 18.6 / 20.6. Best pass^4 13.4. "3–4× harder than existing τ-bench domains."
- Terminal (shell grep/cat/find) beats embeddings by 2.4–3.6 pp but +6.6 s median turn.
- Failure modes: search inefficiency/unwarranted assumptions ~23 %, complex interdependencies ~14.5 %, implicit subtask ordering ~5 %, overtrusting user assertions ~4 %.
- Retrieval configs in repo: `no_knowledge`, `full_kb`, `golden_retrieval`, `grep_only`, `bm25`, `openai_embeddings`, `qwen_embeddings`, `terminal_use`, `terminal_use_write`, `alltools` (default: BM25 + OpenAI dense + sandboxed shell), `alltools-qwen`, `_reranker` / `_grep` suffixes. Shell needs Anthropic `sandbox-runtime` + ripgrep.

## 9. Live leaderboard snapshot (S3 manifest, 2026-09-27)

### τ³-Voice (regular complexity, pass^1; standard user sim = gpt-4.1 unless noted)
| avg | Model | Org | Type | Retail | Airline | Telecom | Banking | L_R s | L_Y s | R_R | R_Y | I_A | S_BC | S_VT | S_ND |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 81.7 | gpt-live-1 (frontend gpt-live-1-diamond-alpha + backend gpt-6-astra medium) | OpenAI | std | 78.9 | 82.0 | 84.2 | – | 2.54 | 1.08 | .96 | .36 | .22 | .99 | .59 | .46 |
| 80.2 | Pine Voice Preview (custom user sim gpt-5.5 xhigh) | Pine AI | custom | 85.1 | 80.0 | 75.4 | – | 2.00 | 1.19 | .95 | .41 | .77 | .97 | .54 | .40 |
| 75.4 | Pine Voice Preview (two agents: custom ASR→LLM→TTS + interaction model; background gemini-3.5-flash tool agent) | Pine AI | std | 70.2 | 70.0 | 86.0 | – | 2.14 | 1.13 | .93 | .45 | .47 | .97 | .53 | .49 |
| 74.8 | grok-voice-think-fast-1.0 + tool-mentor (gemini-3.5-flash gate on mutating tool calls) | Pickle | custom | 76.3 | 70.0 | 78.1 | – | 1.73 | 0.98 | 1.00 | .93 | .29 | .66 | .42 | .27 |
| 72.7 | gpt-live-1 (custom user sim gpt-5.5) | OpenAI | custom | 84.2 | 78.0 | 96.5 | 32.0 | 2.53 | 1.05 | .92 | .34 | .20 | .89 | .62 | .57 |
| 67.3 | grok-voice-think-fast-1.0 | xAI | std | 62.3 | 66.0 | 73.7 | – | 1.21 | 1.37 | 1.00 | .94 | .20 | .66 | .60 | .28 |
| 62.5 | grok-voice-think-fast-2.0 | xAI | std | 59.6 | 56.0 | 71.9 | – | 1.71 | 0.94 | 1.00 | .97 | .11 | .47 | .33 | .28 |
| 53.7 | qwen3.5-omni-plus-realtime | Qwen | std | 46.5 | 54.0 | 60.5 | – | 1.70 | 0.93 | .99 | .90 | .36 | .84 | .36 | .15 |
| 51.2 | gpt-realtime-2 (custom user sim) | OpenAI | custom | 50.0 | 66.0 | 37.7 | – | 1.94 | 0.61 | .97 | 1.00 | .21 | .01 | .11 | .20 |
| 43.8 | gemini-3.1-flash-live-preview thinking HIGH | Google | std | 45.6 | 64.0 | 21.9 | – | 3.15 | 0.86 | .85 | .50 | .19 | .94 | .62 | .43 |
| 42.4 | gpt-realtime-2 (xhigh) | OpenAI | std | 47.4 | 58.0 | 21.9 | – | 1.98 | 0.62 | .95 | 1.00 | .21 | .04 | .13 | .16 |
| 38.5 | gpt-realtime-2 | OpenAI | std | 39.5 | 56.0 | 20.2 | – | 1.44 | 0.62 | 1.00 | 1.00 | .19 | .02 | .06 | .17 |
| 38.3 | grok-voice-fast-1.0 | xAI | std | 38.6 | 36.0 | 40.4 | – | 1.15 | 1.15 | .91 | .75 | .84 | .93 | .58 | .21 |
| 35.3 | gpt-realtime-1.5 | OpenAI | std | 44.7 | 40.0 | 21.1 | – | 1.39 | 0.42 | 1.00 | 1.00 | .14 | .02 | .05 | .10 |
| **31.2** | **Cascaded baseline: Deepgram nova-3 STT + gpt-4.1 + Deepgram aura-asteria-en TTS** | Multiple | std | **28.9** | **48.0** | **16.7** | – | **4.24** | 0.87 | .79 | .99 | **.64** | .67 | .49 | .58 |
| 30.4 | gpt-realtime-1.0 | OpenAI | std | 36.0 | 36.0 | 19.3 | – | 1.53 | 0.41 | .98 | 1.00 | .12 | .04 | .05 | .13 |
| 28.6 | gemini-3.1-flash-live-preview thinking MINIMAL | Google | std | 26.3 | 42.0 | 17.5 | – | 1.64 | 0.82 | .97 | .52 | .10 | .90 | .51 | .27 |
| 25.8 | gemini-live-2.5-flash-native-audio | Google | std | 29.8 | 30.0 | 17.5 | – | 1.43 | 0.86 | .81 | .56 | .21 | .85 | .34 | .45 |
| – | xai-realtime (voice+banking, alltools) | xAI | custom | – | – | – | 16.5 | 1.43 | 0.99 | 1.00 | .95 | .25 | .68 | .40 | .16 |
| – | gemini-3.1-flash-live thinking HIGH (voice+banking) | Google | custom | – | – | – | 11.3 | 2.95 | 0.82 | .82 | .49 | .18 | .93 | .59 | .44 |
| – | gpt-realtime-2 (voice+banking) | OpenAI | custom | – | – | – | 10.3 | 2.04 | 0.61 | .94 | 1.00 | .27 | .03 | .15 | .16 |

Observations: the only cascaded STT-LLM-TTS entry scores 31.2 with the slowest response latency (4.24 s) and high agent-interruption rate (0.64); OpenAI realtime models have near-zero selectivity (they react to every backchannel/tic); Pine's production pipeline (cascaded + custom interaction model + background tool agent) reaches 75.4 standard. Voice tops out ~82 vs text ~88.

### τ²-bench text (pass^1 / pass^4; user sim gpt-5.2)
| Model | Retail p1/p4 | Airline p1/p4 | Telecom p1/p4 | Banking p1/p4 (retrieval) |
|---|---|---|---|---|
| Qwen3.5-397B-A17B (thinking) | 84.4 / 59.6 | 81.5 / 68.0 | 97.8 / 92.1 | 9.8 / 5.2 (emb-3-large) |
| Claude Opus 4.5 | 79.6 / 51.8 | 84.0 / 70.0 | 92.3 / 78.1 | 24.7 / 11.3 (alltools) |
| GPT-5.2 (reasoning) | 81.6 / 51.8 | 83.0 / 72.0 | 89.7 / 71.9 | 32.2 / 18.6 (alltools) |
| Gemini 3 Flash | 76.8 / 51.8 | 82.5 / 68.0 | 91.2 / 70.2 | 27.3 / 7.2 (terminal) |
| Gemini 3 Pro | 75.9 / 47.4 | 80.5 / 66.0 | 91.0 / 74.6 | 18.0 / 4.1 (terminal) |
| RAFT-30B-A3B (custom, fine-tuned Qwen3-30B) | 82.5 / 61.4 | – | – | – |
| GLM-5 | 73.7 / 43.9 | 82.5 / 70.0 | 86.8 / 62.3 | 9.8 / 3.1 |
| Claude Sonnet 4.5 | 72.4 / 39.5 | 72.0 / 48.0 | 84.9 / 64.0 | 25.3 / 10.3 (terminal) |
| GPT-5.2 reasoning none | 75.0 / 45.6 | 52.5 / 22.0 | 57.2 / 30.7 | 12.6 / 4.1 |

Per-domain costs reported (USD per trajectory, e.g. Opus 4.5: retail 0.39, airline 0.40, telecom 0.72).

### τ³-Banking text (pass^1 / pass^4, alltools unless noted)
Qwen 3.8 Max 55.2/35.1 · Claude Opus 5 48.7/32.0 · Grok 4.5 47.9/32.0 · GPT-5.6-sol 46.9/27.8 · GPT-5.5 44.6/29.9 · Muse Spark 1.1 40.5/20.6 · Claude Opus 4.8 39.7/22.7 · Claude Fable 5 39.7/28.9 · GPT-5.4 xhigh 39.4/21.6 · Claude Opus 4.7 40.2/24.7 · Kimi K3 37.1/17.5 · GLM-5.2 37.1/13.4 · Distyl ButtonAgent (custom) 31.2/13.4 · Claude Opus 4.6 max 27.3/11.3 · Gemini 3.1 Pro 26.0/9.3 · Inkling 25.0/11.3 · Grok 4.2 18.0/8.2 · Grok 4 fast 15.7/4.1 · Gemini 2.5 Pro 13.7/1.0 · Grok 4.1 fast 13.1/5.2.

### Legacy τ²-bench (pre-fix tasks, older user sim) pass^1 retail/airline/telecom
Claude Sonnet 4.5 86.2/70.0/98.0 · Gemini 3.0 Pro 85.3/73.0/98.0 · DeepSeek-V3.2 81.1/63.8/96.2 · GPT-5 81.6/62.5/95.8 · Qwen3-Max-Thinking 79.4/69.0/98.2 · Nemotron-Orchestrator-8B 84.2/56.0/88.6 · Claude Opus 4.1 82.4/56.0/– · GPT-4.1 74.0/56.0/34.0 · GPT-4.1-mini 61.4/48.7/48.9 · o4-mini 68.3/52.1/50.2 · Claude-3.7-Sonnet 72.1/64.2/49.0 · Kimi-k2 70.6/56.5/65.8.

## 10. Submission rules (docs/leaderboard-submission.md)

- **Standard**: default τ-bench agent scaffold, tools, prompts, user simulator, evaluator; no benchmark-side modification; model not trained on τ-bench. For τ-voice, *any* internal architecture behind the τ-voice agent interface is allowed (proprietary ASR/TTS, multiple models, routing) — "a transport or protocol adapter that only connects the system to the standard τ-voice agent interface is also allowed". Prepending a scaffold system message counts as standard (Pine).
- **Custom**: modified scaffolds/prompts/orchestration, extra tools, non-default user simulator, domain-specific fine-tuning; must document methodology and set `submission_type: "custom"`.
- Requirements: all tasks, `base` split, identical model config across domains, ≥ 4 trials preferred (voice: 1 trial typical), voice must be `--speech-complexity regular`; recommend gpt-5.2 user sim for text; trajectories uploaded to public S3; interaction metrics recomputed by maintainers.
- Versioning: tau2-bench ≥ 1.0.1 required for banking comparability; user simulator versioned via git tags `voice-user-sim-<version>`.

## 11. How to run (repo)

```
uv sync --extra voice            # Python >=3.12 <3.14; brew install portaudio ffmpeg
tau2 run --domain airline --agent-llm gpt-4.1 --user-llm gpt-4.1 --num-trials 4
tau2 run --domain retail --audio-native --audio-native-provider openai --audio-native-model <model> --speech-complexity regular --verbose-logs
tau2 run --domain banking_knowledge --retrieval-config alltools --agent-llm ...
tau2 view · tau2 evaluate-trajs --fresh-tasks · tau2 submit prepare/validate/interaction-metrics · tau2 intro
```
Python API: `TextRunConfig` / `VoiceRunConfig(domain, audio_native_config=AudioNativeConfig(provider, model), llm_user, speech_complexity)` → `run_domain(config)`; `compute_metrics(results)` → `avg_reward`, `pass_hat_ks`. Layer-3 API lets you plug a **custom agent**: subclass `HalfDuplexAgent.generate_next_message()` (text) or `FullDuplexAgent.get_next_chunk(state, participant_chunk, tool_results)` (voice, tick-based, receives audio chunks and returns audio + transcript), constructor `(tools, domain_policy)`. Voice providers with adapters: `openai`, `gemini`, `xai`; new providers implement a `DiscreteTimeAdapter` in `src/tau2/voice/audio_native/`. Output per run: `results.json`, `simulations/sim_*.json`, `artifacts/task_<id>/sim_<uuid>/audio/both.wav` (stereo, user L / agent R) + Audacity label files + `llm_debug/`.

## 12. Implications for our LiveKit-agent eval project

- **Domain choice**: `airline` (50 tasks) is the cheapest iteration loop and has the clearest policy/refusal structure; `retail` (114) is the paper's primary domain with the most head-to-head numbers; `telecom` (114) exercises dual control (agent must instruct the user to run device actions) — the hardest for voice per the paper (20–58 % clean); `banking_knowledge` (97) is a RAG stress test (best models ~50 % text, ~10–32 % voice).
- **Architecture-appropriate baseline**: a LiveKit STT-LLM-TTS agent is architecturally the "Cascaded baseline" row (31.2 avg, L_R 4.24 s, I_A 0.64), while LiveKit realtime agents map to the gpt-realtime / gemini-live rows. Pine's 75.4 shows a cascaded pipeline with a good interaction model and a background tool agent can beat most native-audio models.
- **Failure behaviours worth targeting with prompt changes** (from the paper and leaderboard): spelled-out identifiers (names, emails, reservation/order IDs, zip codes) during authentication; hallucinated completions without a tool call; forgetting later parts of multi-part requests; going silent after repeated failures; policy misapplication (24-h cancellation rule, insurance, refund method, basic-economy limits); over-trusting user claims; reacting to backchannels/vocal tics (selectivity); talking over the user; slow response latency.
- **Metrics we can reuse verbatim**: pass^k (DB end-state + communicate), the 8 interaction metrics with their detection windows, per-trajectory cost, and the failure taxonomy (Logical / Transcription / Hallucination / VAD-Unresponsive / Timeout / user-sim error).
- **Integration options**: (a) implement a τ-bench `FullDuplexAgent` adapter that bridges ticks to a LiveKit agent session; (b) run τ-bench's user simulator through a LiveKit room as a participant; (c) port τ-bench tasks into LiveKit `scenarios.yaml` (instructions ← `user_scenario`, `agent_expectations` ← nl_assertions/end state, `userdata.expected_state` ← replayed DB diff) and grade with `on_simulation_end`. Option (c) loses τ-bench comparability but keeps LiveKit's native tooling.
