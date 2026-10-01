# CLAUDE.md — LiveKit Agents: knowledge base for building EVALS

> Purpose of this file: a distilled, source-grounded reference extracted from https://docs.livekit.io/agents/ (and the companion Test & Evaluate section) on 2026-09-27, plus τ-bench (taubench.com) as the external benchmark (Part 8).
> The goal of this project is **not** to build or improve LiveKit voice agents. It is to build **evaluations / benchmarks** for agents built on this framework. Workflow agreed with the user: (1) pick a model/agent configuration from the extracted lists, (2) benchmark it on selected τ-bench tasks, (3) build LiveKit-native evals for the behaviours where it fails, (4) improve those behaviours through prompt changes only. Everything below exists to inform (a) what an agent can be, (b) what can go wrong, (c) what is observable, and (d) what LiveKit itself already measures — so the benchmarking criteria and judge/model choices in the next iteration are grounded in the framework's real behavior.
>
> Conventions: parameter names, defaults, and version numbers are quoted from the docs. "Python only" / "Node only" flags are preserved. Every page used is listed in the Source Index at the end; append `.md` to any docs URL for clean Markdown, or use `https://docs.livekit.io/agents/llms.txt` (225 pages) and `https://docs.livekit.io/testing/llms.txt` (21 pages).

---

## 0. Working rules for this project

1. **Scope**: build evals for LiveKit agents. Do not refactor or "improve" agent code unless it is a test fixture needed by an eval.
2. **Ground truth is the docs**: when a claim about framework behavior matters for an eval (defaults, event semantics, metric definitions), verify against the `.md` page before relying on it.
3. **Text-first, audio-second**: mirror the framework's own lifecycle — cheap deterministic text-mode checks on every change, audio-mode checks for turn-taking/speech quality on a schedule.
4. **Grade outcomes and state, not phrasing**: LiveKit's own guidance for scenario expectations. A polished transcript can still write the wrong record.
5. **One complication per scenario**, label names the situation, absolute dates, pinned clock, seeded state via `userdata`.
6. **Reuse what LiveKit already ships** (`session.run` + `RunResult`, `JudgeGroup` + 8 judges, `lk agent simulate`, `ChatMessage.metrics`, per-plugin metrics) before writing custom harnesses. Custom judges subclass `Judge`.

---

## 1. What the framework is

- **LiveKit Agents** = open-source (Apache 2.0) "realtime framework for voice, video, and physical AI agents". Any Python or Node.js program joins a LiveKit **room** as a full realtime **participant**, feeds media/data through an AI pipeline (any provider), publishes results back.
- **SDKs**: Python `livekit-agents` (Python ≥ 3.10; docs pin `livekit-agents[...]~=1.8`), Node `@livekit/agents` (Node ≥ 20). Many advanced features are **Python only** (simulations landed first in Python, prebuilt tasks, MCP, dynamic tool discovery, `JudgeGroup`, dynamic endpointing, `backchannel_boundary`, live video input, AGC, pre-connect audio buffer).
- **Core objects**
  - `AgentServer` — registers with LiveKit, waits for dispatch, spawns one **job process per session**. Entrypoint via `@server.rtc_session(agent_name=..., on_session_end=..., on_simulation_end=..., on_request=...)`.
  - `AgentSession` — the orchestrator: holds `stt`, `llm`, `tts`, `vad`, `turn_handling`, `tools`, `userdata`, emits events, manages state machine `initializing → listening → thinking → speaking`.
  - `Agent` — `instructions`, `chat_ctx`, `tools`, per-agent model overrides, overridable pipeline nodes and lifecycle hooks (`on_enter`, `on_exit`, `on_user_turn_completed`, `stt_node`, `llm_node`, `tts_node`, `transcription_node`, `realtime_audio_output_node`).
  - `AgentTask[T]` / `TaskGroup` — short-lived, typed-result sub-conversations (TaskGroup is experimental).
  - **Plugins** (`livekit-plugins-*`, `@livekit/agents-plugin-*`) or **LiveKit Inference** (hosted STT/LLM/TTS/turn detector; no provider keys; zero data retention; string descriptors `provider/model[:language-or-voice]`, e.g. `stt="assemblyai/universal-3-5-pro:en"`, `llm="google/gemma-4-31b-it"`, `tts="fishaudio/s2.1-pro:<voiceId>"`, `stt="auto:es"`).
- **Room / participant model**: frontend ↔ agent over WebRTC; agent ↔ backends over HTTP/WS. Telephony bridges phone calls in as **SIP participants** (trunks, dispatch rules, connectors for WhatsApp/Twilio). Avatars join as a second `agent`-kind participant with `lk.publish_on_behalf`.
- **Dispatch**: automatic (no `agent_name`: joins every new room — docs caution against it) or explicit (`agent_name` + API `CreateAgentDispatchRequest` / token `RoomAgentDispatch` / SIP dispatch rule). Job metadata ≤ 512 KiB (JSON recommended). Dispatch "max dispatch time under 150 ms". Crash detection ≈ 15 s then re-dispatch.
- **Startup modes** (`lk agent ...`): `console` (local mic/speaker, no LiveKit connection, `--text`, `--record` → `console-recordings/`), `dev` (debug logs, auto-reload, no drain), `start` (production; JSON logs, drain on SIGTERM, `drain_timeout` default one hour), `connect --room`.
- **Server options** worth knowing for eval infra: `load_threshold` `0.7`, `num_idle_processes` (≈ cpu_count), health check on `:8081` (`200`/`503`), `shutdown_process_timeout` 10 s, `session_end_timeout` 5 min (bounds `on_session_end`), `drain_timeout`.

---

## 2. The different ways agents can be built

### 2.1 Pipeline architectures (the primary axis)

| Dimension | STT → LLM → TTS ("cascaded") | Realtime / speech-to-speech | Half-cascade (realtime LLM text-only + separate TTS) |
|---|---|---|---|
| End-to-end latency | Moderate (stages overlap via streaming) | Fastest | Moderate |
| Tool calling | Mature | Less mature | Less mature |
| Realtime transcription | Yes (interim + final) | Delayed (transcripts often arrive **after** the agent responds) | Delayed |
| Scripted speech `say()` | Yes | No (needs a TTS plugin) | Yes |
| Prosody-aware comprehension | No | Yes | Yes |
| Expressive output | Depends on TTS | Built-in | Depends on TTS |
| Auditability | Full text trail | Limited | Output text only |

- Docs: **"For most production agents, an STT-LLM-TTS pipeline is the right default."** Choose realtime "when latency or expressive output matter more than fine-grained control." Half-cascade avoids "some realtime models defaulting to text-only output after loading long conversation histories".
- Latency target stated by docs: **end-to-end response latency under one second** feels natural.
- Quickstart defaults: STT `assemblyai/universal-3-5-pro`, LLM `google/gemma-4-31b-it` ("recommended default LLM… latency-optimized, open-weight"), TTS `fishaudio/s2.1-pro` voice `fa4c9eb3dccc4806b382b40d61c6b10a`; realtime alternative `openai.realtime.GPTLiveModel(voice="marin")`. All quickstarts also add BVC noise cancellation, preemptive generation, and the LiveKit turn detector.
- Realtime plugins: OpenAI GPT-Live (full-duplex; model decides turn boundaries, "keeps talking until it stops on its own"), OpenAI Realtime API (`gpt-realtime`, voice `marin`, temp 0.6–1.2, semantic VAD default / server VAD), Azure OpenAI Realtime, Gemini Live (`gemini-2.5-flash`, voice `Puck`, no client-side turn-taking support), Amazon Nova Sonic, NVIDIA PersonaPlex, Phonic, SpaceXAI Grok Voice, Ultravox.

### 2.2 Build / delivery paths

| Path | What it is | Notable constraints |
|---|---|---|
| **Code (Python / Node SDK)** | Full framework | Python has the most features |
| **Agent Builder** (no-code, browser) | Generates best-practice Python, deploys to Cloud; instructions, data-collection fields (typed, required/optional), HTTP actions, client RPC tools, MCP servers, `{{metadata.key}}` / `{{secrets.KEY}}`, call-ending summary POST | No workflows/handoffs/tasks, avatars, vision, realtime models, or tests; preview sessions don't appear in observability; data-collection + custom tools "can bias the agent toward greedy tool execution" |
| **Embed widget** | `<script src="https://cloud.livekit.io/embed-popup.js" data-lk-agent="CA_...">` | Cloud agents only; allowed origins required; one widget per page; snippet can't override capabilities/room/dispatch |
| **Telephony (SIP)** | LiveKit Phone Numbers (US, inbound only) or third-party trunks (Twilio, Telnyx, Plivo, Exotel, Wavix, Sinch, didlogic); DTMF, cold/warm transfer, AMD | No REGISTER, SIPREC, or video over SIP; explicit dispatch recommended |
| **Frontend SDKs** | Next.js/React, SwiftUI, Android, Flutter, React Native, Unity starters; `useVoiceAssistant`, `useTranscriptions` | Mobile needs a token server |
| **Text-only / hybrid** | `RoomOptions(audio_input=False, audio_output=False)` or `session.input/output.set_audio_enabled()`; text over `lk.chat` topic | Text input interrupts current speech by default |
| **Vision** | `ImageContent` in chat context, byte-stream uploads, frame sampling in `on_user_turn_completed`, live video (`video_input=True`, Python; only Gemini Live / OpenAI Realtime) | Video is passive (no effect on turn detection); audio-only realtime model silently ignores frames |
| **Avatars** | `AvatarSession.start(session, room=)`; 16 providers (Anam, Tavus, Simli, D-ID, Synthesia, …) | `wait_for_join()` before `session.start()` (30 s default timeout); adds join + playback latency |

### 2.3 Logic / control-flow patterns

- **Single agent + tools** — the recommended starting point ("Start with a single agent and a small set of tools"). Split only on instruction bloat, conflicting tool access, multi-turn data collection, or backtracking.
- **Agents & handoffs** — return an `Agent` (or `(Agent, result)` / `llm.handoff`) from a tool, or `session.update_agent()`. **Context does NOT carry over unless `chat_ctx` is passed.** Adds `agent_handoff` item. `on_exit` → `on_enter`. "Handoff overhead per transition."
- **Tasks (`AgentTask`)** — take control until `complete(result)`; only awaitable from `on_enter`, `on_exit`, or a tool body. Start with empty context by default.
- **Task groups** — ordered tasks with built-in **regression** (LLM can go back to an earlier step using each task's `id`/`description`), `summarize_chat_ctx` default `true`, `return_exceptions` default `false`. Experimental.
- **Supervisor** — one long-lived agent routes to task specialists via tools; conversation waits for the typed result. Rule: "if the model needs to ask clarifying questions, the work belongs in a task; a single function call belongs in a tool." "Treat task results as untrusted input until validated."
- **Subagent delegation ("talker-reasoner")** — fast primary model; async tool delegates to a separate LLM/`ChatContext`; result delivered later via `ctx.update()`. Behavior "varies between models and between runs."
- **Prebuilt tasks** (Python beta `livekit.agents.beta.workflows`): `GetNameTask`, `GetEmailTask`, `GetAddressTask`, `GetDOBTask`, `GetPhoneNumberTask`, `GetCreditCardTask` (Luhn-style validation, issuer detection, rejects expired, `require_confirmation` default `True` in audio / `False` in text, "sensitive information is never repeated back to the user during audio sessions"), `GetDtmfTask`, `WarmTransferTask` (also Node). Prebuilt tools: `EndCallTool`, `send_dtmf_events`.

---

## 3. Topics used to enhance agent capabilities (what a "good" agent uses)

### 3.1 Tools / function calling
- `@function_tool` (Python) / `llm.tool({... parameters: z.object ...})` (Node). Args inferred from signature; guidance in docstring/description ("the model reads the description more carefully than the schema"). `RunContext` exposes `session`, `function_call`, `speech_handle`, `userdata`.
- **Return semantics**: return value stringified and sent to the LLM; return `None` → silent, no reply; return `Agent` → handoff after the reply completes. `ToolError(msg)` sends the message to the LLM; unexpected exceptions become a generic error (details not leaked, logged). Schema/validation failures are auto-forwarded as `ToolError` so the LLM can retry.
- `max_tool_steps` default **3**; on limit, "one final LLM call with tool use disabled" summarizes (≥ 1.4.5; earlier failed silently). `tool_choice` default `"auto"`; `"none"` inside tool-triggered replies. `parallel_tool_calls` can be disabled when results chain.
- **Tool loop design guidance**: aim 5–10 tools; ">10 incorrect selections become more common, and past 20, the model often struggles"; expose actions not endpoints; pin valid values in prose; return speech-ready strings and semantic identifiers not UUIDs; set timeouts (a hanging backend "blocks the session: the close callback doesn't run and the next turn never starts"); gate critical actions with self-reporting params (e.g. `read_back: bool`). Diagnostics: redundant calls → wrong return shape; invalid args → unclear param descriptions; wrong tool → overlapping descriptions.
- **Interruptions during tools**: tools are interruptible by default but interrupting doesn't cancel the work; a tool finishing after interruption keeps call+result in history with **no spoken reply**; **an interrupted handoff never takes effect**; use `disallow_interruptions()` for non-rollbackable writes.
- **Toolsets** (`Toolset(id, tools)`), dynamic discovery (`ToolSearchToolset`, `ToolProxyToolset`, BM25), **MCP** (`MCPToolset` with HTTP/SSE/stdio; `mcp_servers` deprecated), **async tools** (`ctx.update()` makes the tool non-blocking; `ctx.with_filler()`; `ToolFlag.CANCELLABLE`; `on_duplicate` allow/reject/replace/confirm; pending updates are **dropped on handoff** unless in `AsyncToolset`), **frontend forwarding** via RPC (`perform_rpc`), **provider tools** (OpenAI WebSearch/FileSearch/CodeInterpreter via Responses API, Anthropic `ComputerUse`).

### 3.2 Turn detection, endpointing, interruptions (`turn_handling=TurnHandlingOptions(...)`)
- `turn_detection`: `TurnDetector()` (**default and recommended**; audio model, no transcript needed), `"stt"`, `"vad"`, `"realtime_llm"`, `"manual"`. Auto-selects realtime server-side detection if the LLM is a realtime model.
- **Audio turn detector**: `v1` (LiveKit Inference, free on Cloud) / `v1-mini` (local CPU); `unlikely_threshold` scalar or per-language dict (lower = more eager); 14 languages (en, ar, de, es, fr, hi, id, it, ja, ko, nl, pt, tr, zh); prediction timeout ≈ 1 s → commit anyway + sticky fallback to mini; requires VAD `min_silence_duration ≥ 0.25` s. Benchmarked with the open-source **`eot-bench`** harness and `livekit/eot-evals` datasets (Hugging Face). Doc example: audio model commits at 6.41 s where a text model prematurely committed at 2.76 s and 4.88 s on mid-turn pauses.
- **Text turn detector (`MultilingualModel`)**: deprecated (removal in 2.0); Qwen2.5-0.5B, 396 MB, ~50–160 ms; per-language TPR ≈ 99.3 %, TNR 85–96 %.
- **Silero VAD** defaults: `min_speech_duration 0.05`, `min_silence_duration 0.55`, `prefix_padding_duration 0.5`, `activation_threshold 0.5`, `max_buffered_speech 60.0`, `sample_rate 16000`.
- **`endpointing`**: `mode` `fixed`|`dynamic` (dynamic = EMA over pause stats, Python only, `alpha 0.9`), `min_delay 0.5` s, `max_delay 3.0` s (audio detector overrides to `0.3`/`2.5`). In STT mode `min_delay` stacks on the provider's own endpointing delay. Node uses ms.
- **`interruption`**: `enabled True`, `mode` `adaptive`|`vad` (adaptive auto when turn detector + aligned-transcript STT, or realtime with server detection off), `min_duration 0.5` s, `min_words 0` (needs STT), `false_interruption_timeout 2.0` s (None disables), `resume_false_interruption True`, `discard_audio_if_uninterruptible True`, `backchannel_boundary (1.0, 1.0)` (Python; cooldown at start/end of agent turns).
- **Adaptive interruption handling**: acoustic barge-in model (Cloud regions; 40k free requests/month locally); filters backchannels ("uh-huh", "okay"), noise; on realtime models the gate is **all-or-nothing** (whole turn dropped), on STT pipelines only the backchannel portion is dropped. Emits `overlapping_speech` events and `InterruptionMetrics`. "Might perform better with English in some cases."
- **Preemptive generation**: `enabled True` (LLM starts on final transcript before turn confirmed; increases token usage), `preemptive_tts False`, `max_speech_duration 10.0` s, `max_retries 3`. "Preemptive generation in particular doesn't always reduce latency."
- **User turn limit**: `max_words` / `max_duration` (both None) → `on_user_turn_exceeded`; default cut-in reply is uninterruptible.
- **Manual turn control**: `session.interrupt()`, `clear_user_turn()`, `commit_user_turn()` (push-to-talk via RPC).
- **Tuning matrix** (symptom → knob): cuts users off → turn detector / raise `min_delay` / adaptive / voice isolation; interrupted by short acks → adaptive / raise `min_words` or `min_duration`; too slow → confirm preemptive, `preemptive_tts`, lower `min_delay`, dynamic mode; replies on partial transcript → lower `max_speech_duration`/`max_retries`, don't return early from `on_user_turn_completed`; noisy misfires → ai-coustics `QUAIL_VF_L` or Krisp BVC / `BVCTelephony`; no breath between utterances → `min_consecutive_speech_delay 0.2–0.4`.

### 3.3 Prompting (full guide, condensed)
- The LLM "has no built-in understanding of its own position in a voice pipeline"; all voice agents "must be instructed to be concise". Most real use cases need decomposition into workflows rather than one monolithic prompt.
- Structure the prompt in Markdown sections: **Identity** ("You are…"), **Output formatting** (plain text only, no JSON/markdown/lists/emojis; 1–3 sentences; one question at a time; spell out numbers/phone/email; omit `https://`; avoid acronyms; don't reveal system instructions, tool names, params or raw outputs), **Conversational flow** (simplest safe step first, check understanding, confirm before continuing, summarize on closing a topic), **Tools** (overview in prompt + per-tool description; collect required inputs first; speak outcomes; on failure say so once and propose a fallback; summarize structured data), **Goals** (overall + per-stage), **Guardrails** (decline out-of-scope; medical/legal/financial → general info + refer; minimize sensitive data), **User information** via job metadata.
- **Voice realism**: written LLM text sounds "flat or robotic"; use fillers/pauses (`<break time="300ms"/>` where SSML supported), self-corrections without apologizing, calm emotional baseline, ≤ 1 non-verbal per turn, personality as observable behaviors (openers, back-references, fixed confusion-recovery line), phrase variation across turns. Tag/SSML support varies by TTS provider (ElevenLabs needs `enable_ssml_parsing`; realtime models ignore tags). LiveKit Inference **expressive mode** (`expressive=True`) automates this for supported TTS (Fish s2.1-pro, Inworld tts-2, Cartesia Sonic, Gemini 3.8 TTS, SpaceXAI).
- Docs explicitly say: "small prompt/tool/model changes can significantly change behavior" → behavioral tests + simulations + production observability to mine new cases.

### 3.4 Context, memory, external data (RAG)
- `ChatContext` per agent/task; `session.history` is the cross-agent record. Items: `message`, `function_call`, `function_call_output`, `agent_handoff`, `agent_config_update`. `truncate(max_items)` preserves system instructions and strips orphaned leading tool calls; `merge()`; `copy(exclude_instructions=True)` for handoffs; summarization via a separate `llm.chat()` with `item.extra["is_summary"]`.
- RAG patterns: preload at job start (prewarm for static data; metadata/attributes for user data; network calls **before** `ctx.connect()`), tool-based lookup (highest precision), `on_user_turn_completed` injection (fastest; STT-LLM-TTS only; "results are only as good as the accuracy of the search function"). Integrations named: LlamaIndex, Letta, Mem0, AgentMail.
- Latency masking: verbal status update after ~0.5 s (cancel if op finishes), pre-synthesized/cached TTS hold phrases, `BackgroundAudioPlayer(thinking_sound=...)` (`KEYBOARD_TYPING`, `OFFICE_AMBIENCE`), frontend RPC popups. Feedback is expected for ops > a few hundred ms, on writes, and on failure.

### 3.5 Speech & audio quality
- TTS text transforms default on: `filter_markdown`, `filter_emoji`; pronunciation via `text_transforms.replace` or custom `tts_node`; SSML (`phoneme`, `say-as`, `break`, `prosody`) where supported; TTS caching; volume via node processors.
- Aligned transcripts (`use_tts_aligned_transcript=True`; word-level with Cartesia/ElevenLabs/Rime/Speechify) → `TimedString` timestamps; transcripts sync word-by-word and truncate on interruption.
- Custom voices (Ship plan+; cloned to Cartesia/Inworld/Fish/Gradium in parallel; cross-provider fallback "voice stays recognizable… output isn't identical").
- Noise cancellation: Krisp BVC / `BVCTelephony`, ai-coustics; recordings capture user audio **after** noise cancellation ("what the STT heard").
- Wakeword (client-side, `hey livekit`, trainable; eval outputs DET curve + metrics JSON, `target_fp_per_hour`).
- STT: streaming vs non-streaming (`StreamAdapter` + VAD), diarization (`MultiSpeakerAdapter`), **keyterms** (`STTContextOptions(keyterms=[...])`, auto-detection via Gemma; "wrong keyterm degrades recognition for the rest of the call with no recovery"), `LanguageCode` normalization, multilingual `deepgram/nova-3:multi`.

### 3.6 Reliability
- **Fallback adapters**: Inference-side (`fallback=[{"model": ...}]` on `inference.STT/TTS`; mid-stream failure restarts the request from the beginning) and in-process `stt/llm/tts.FallbackAdapter([...])` (TTS won't switch mid-utterance once audio played; LLM won't switch after chunks streamed unless `retry_on_chunk_sent=True`). Emit `error` (recoverable) and `*_availability_changed` events.
- **Error events**: `ErrorEvent.error.recoverable` — `False` closes the session unless you set it `True` (safe for LLM/TTS/realtime; STT needs `session.update_agent(session.current_agent)` to restart the stream). Graceful exit: `session.say(..., allow_interruptions=False)` with pre-recorded `audio=` because TTS may be down.
- `CloseReason`: `error`, `job_shutdown`, `participant_disconnected`, `user_initiated`, `task_completed`.
- `user_transcription_timeout` (off by default; needs VAD+STT) catches "VAD heard speech but STT produced nothing".

---

## 4. Important considerations & edge-case handling (consolidated checklist)

Each of these is a candidate eval dimension or a trap for an eval harness.

**Conversation & turn-taking**
- Interrupted agent speech is truncated in history to what the user actually heard.
- False interruption (VAD speech, empty transcript) → agent resumes after `false_interruption_timeout`; `agent_false_interruption` event with `resumed`.
- Backchannel misclassification at turn edges (correction right after agent starts; short answer just before it stops) → `backchannel_boundary` cooldown; slow STTs need a larger end value.
- Turn-limit counters don't reset on brief pauses; callback skipped if agent already speaking.
- Realtime + server-side detection: `interruption.enabled=False` raises `ValueError`; only `enabled` and `discard_audio_if_uninterruptible` apply.
- Gemini Live doesn't support client-side turn-taking; Gemini 3.1 has no async function calling ("the model pauses and waits for your tool response").
- Speculative (preemptive) responses are discarded/regenerated if `on_user_turn_completed` changes context or tools.

**Tools & workflows**
- Interrupted handoff never happens (Python keeps call with error; Node removes it).
- Tool result after interruption is recorded but never spoken (hold-message pattern makes this common).
- `reply_maybe_covered_template` can silently swallow a delegated result; "Agent instructions don't override the template."
- Filler on a blocking tool "becomes the last assistant turn… a latency-optimized model can treat its own filler as a user turn."
- Pending async updates dropped on handoff unless `AsyncToolset` is session-level.
- New agent/task starts with **empty** context unless `chat_ctx` passed → repeated questions / lost facts.
- Typed task results can still be invalid ("untrusted input until validated").
- Don't call `session.shutdown()` inside `on_task_completed` (`RuntimeError`).
- Realtime model can't be swapped on an active agent (`RuntimeError`).
- Startup output from `on_enter` (`say`/`generate_reply`) is **not** in `RunResult` — don't assert on it in unit tests.
- Function-call arguments are raw JSON strings; use `contains_function_call` partial match.
- `get_job_context()` raises in tests; `userdata` must be initialized or `ValueError`.

**Models & providers**
- Unsupported inference params are **silently ignored**; reasoning-incompatible params stripped; `temperature` & `top_p` mutually exclusive; `temperature` deprecated for Gemini 3 (expects 1); `max_tokens` unsupported on newer models. Temperature ranges differ (Anthropic 0–1, OpenAI 0–2, OpenAI Realtime 0.6–1.2). `update_options` replaces, not merges.
- Anthropic plugin default `claude-sonnet-4-6`, `max_tokens 1024` (Node 4096), `timeout httpx.Timeout(5.0, read=30.0)` — raise `read` for large contexts / extended thinking.
- OpenAI: Responses API recommended over Chat Completions; default `gpt-4.1`, temp `0.8`.
- Realtime: no interim transcripts; user transcripts often arrive after the agent's response; `session.history` may be incomplete/late; no `say()`; history loads as text only; OpenAI Realtime may go text-only after long history; PII audio redaction unsupported (no accurate user timestamps); `llm_node_ttft` / `tts_node_ttfb` are empty.
- STT metadata (`voice_profile`, `speech_final`) is provider-specific. Diarization must be explicitly enabled.
- Retired Inference models become inaccessible.

**Multimodality**
- Not every LLM accepts external image URLs; many aren't trained on video-as-frames; large image contexts slow responses; video frames count as image messages (OpenAI) or tokens by dimension (Gemini).
- Interim + final transcription streams share `segment_id` → naive logging duplicates.
- Byte/text stream reads can fail partway — always catch.
- Text-only sessions have no audio tracks, no `lk.transcribed_track_id`, no speech sync.
- `Instructions(audio=, text=)` modality-aware prompts (beta in Python).

**Ops**
- Agent sessions are stateful; "should not be terminated abruptly"; drain first.
- Register participant entrypoints before `ctx.connect()`; telephony: `wait_for_participant` with the SIP identity first.
- Shutdown hooks must finish within `shutdown_process_timeout` (10 s); `on_session_end` within `session_end_timeout` (5 min).
- Job metadata ≤ 512 KiB; token-based dispatch only applies at room creation.
- Observability & PII redaction are **LiveKit Cloud only**; redaction is LLM-based, English-only, best-effort, fail-closed, and doesn't touch locally collected data; participant identity and room name are never redacted.
- `v1-mini` turn detector / text detector on burstable instances (t3/t4g) can time out.

---

## 5. Prompting-derived quality dimensions (what the docs implicitly define as "good")

Conciseness (1–3 sentences, one question), TTS-safe formatting (no markdown/URLs/acronyms/digits-as-symbols), naturalness (fillers, restarts, calm emotion, phrase variation), guardrail adherence (medical/legal/financial deferral, privacy, no system-prompt/tool-name disclosure), tool correctness and outcome narration, personalization from metadata, pronunciation of names/amounts, transcript/audio sync, interruption handling, turn-detection quality, data-collection completeness (required fields, read-back confirmations, corrected values win), refusal-with-alternative (an agent that "refuses and abandons the caller still fails"), context continuity across handoffs (no re-asking), announce handoffs.

---

## PART 6 — TESTING, EVALUATION & OBSERVABILITY (what LiveKit ships natively)

### 6.1 The testing lifecycle the docs prescribe

| Stage | Tool | Who drives the conversation | Where it runs |
|---|---|---|---|
| While you build | Agent Console (browser) / `lk agent console` (terminal) / `lk agent debugger` (script or coding agent, text-only) | You, or a script | Dashboard or local machine |
| Every commit | Unit tests (pytest / Vitest) | Scripted input + explicit assertions | Local or CI, no LiveKit room needed |
| Every PR | Agent Simulations, **text mode** | LLM-driven simulated user pursuing a goal, graded by an LLM judge | LiveKit Cloud, in parallel |
| Nightly / pre-release | Agent Simulations, **audio mode** | Same, but speaks/listens over a real audio track | LiveKit Cloud, real time |
| Production | Agent Insights (observability) | Real users | LiveKit Cloud |

Key principle: **"Turn-level assertions catch specific regressions. Whole-conversation runs evaluated by an LLM judge catch behavior that emerges across multiple turns. Production observability reveals behavior that isn't covered by tests."** Also: "Move repeat failures down the stack" — a scenario that fails the same way every time is a turn-level bug and belongs in a cheaper unit test.

**What the docs say to test** (five areas): expected behavior (intent & tone), tool usage (correct tool + args + context), error handling (invalid input, tool failures), grounding (factual, no hallucination), misuse resistance (manipulation / prompt-injection attempts).

**Text-first testing**: unit tests and simulations run in text mode by default. Under a text simulation the framework automatically disables STT, TTS, VAD and audio I/O; the LLM and tools run unchanged. Text mode is "cost-efficient and deterministic"; audio is reserved for turn-taking and speech-specific issues.

### 6.2 Unit test API (`session.run` + `RunResult`)

- Requires `pytest` + `pytest-asyncio` (Python) or `vitest` (Node). Node: call `initializeLogger({ pretty: false, level: 'warn' })` at top of test files.
- Pattern: create an `inference.LLM(model=...)` (needed for `judge`), an `AgentSession(llm=llm)`, `await session.start(Agent())`, then `result = await session.run(user_input="...")` runs ONE conversation turn. History builds across repeated `run` calls.
- `RunResult.expect` is a fluent assertion API over the ordered list of events in the turn: messages, function calls, function call outputs, agent handoffs.
  - Sequential: `next_event()`, `is_message(role=)`, `is_function_call(name=, arguments=)`, `is_function_call_output(output=)`, `is_agent_handoff(new_agent_type=)`, `no_more_events()`.
  - Skipping: `skip_next(n)`, `skip_next_event_if(type=, ...)`, `next_event(type=...)` (returns a type-specific Assert; don't chain `is_*` after it).
  - Indexed: `result.expect[0]`, negative indices from end. Search: `contains_message()`, `contains_function_call()`, `contains_agent_handoff()`, with slices `[0:2]`.
  - Properties via `.event().item`: `.content`, `.role`, `.name`, `.arguments`, `.output`, `.is_error`, `.call_id`.
- **LLM-based judgment on a single message**: `.judge(llm, intent="...")` — judges the message against the intent **without surrounding conversation context**. The judge LLM can differ from the agent's LLM. Docs use `google/gemma-4-31b-it` via LiveKit Inference in examples.
- **Mocking tools**: `mock_tools(AgentClass, {"tool_name": fn})` as a `with` block (Python) or `voice.testing.withMockTools` with `using` (Node). Returning/raising an Error makes the tool raise → tests error handling. Mock receives only declared params (`self`, `RunContext` trimmed). Python-only: `mock_tools(..., session=session)` keeps mocks for the session's lifetime (used to seed simulations); `with`-block mocks take precedence over session mocks.
- **Loading history**: build a `ChatContext`, `add_message(role=, content=)`, `await agent.update_chat_ctx(chat_ctx)` (on the Agent instance, not session; use `session.current_agent` if no reference).
- `LIVEKIT_EVALS_VERBOSE=1` prints each RunResult and judgment reasoning (`pytest -s -o log_cli=true`).
- CI: tests hit the real LLM provider; set `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` (Inference) or provider keys. No room connection is made.
- Caveats: `get_job_context()` raises `RuntimeError` in tests. Task groups: add `asyncio.sleep(0.5)` after `session.start()` (Python), prefer `contains_function_call`, generous timeouts, Node cleanup timeout ~30 s.

### 6.3 Built-in judges — `livekit.agents.evals` (Python only)

`JudgeGroup(llm=<LLM instance or "provider/model" string>, judges=[...])` runs judges concurrently over a `ChatContext` (`session.history`) → `EvaluationResult`.

| Judge | What it checks |
|---|---|
| `accuracy_judge` | Grounds information in tool outputs; catches hallucinations and contradictions |
| `coherence_judge` | Logical structure, no topic-jumping or self-contradiction |
| `conciseness_judge` | Unnecessary verbosity, repetition, redundant detail |
| `handoff_judge` | Context retained across handoffs; auto-passes if no handoff occurred |
| `relevancy_judge` | On-topic, addresses what the user asked |
| `safety_judge` | Unauthorized advice, improper disclosure, missed escalation, harmful language |
| `task_completion_judge` | Goal completed per the latest agent instructions in the chat context |
| `tool_use_judge` | Tool selection, parameter accuracy, output handling, error recovery |

`EvaluationResult`: `score` (0.0–1.0; pass=1, maybe=0.5, fail=0), `all_passed`, `any_passed`, `majority_passed`, `none_failed`, `judgments` (dict by judge name → `JudgmentResult` with `verdict` "pass"/"fail"/"maybe", `reasoning`, `instructions`, `passed`/`failed`/`uncertain`).

Custom judges: subclass `Judge`, override `async evaluate(*, chat_ctx, reference=None, llm=None) -> JudgmentResult` — for deterministic, non-LLM checks. Any object satisfying the `Evaluator` protocol (`name` + `evaluate`) also works.

**Production auto-tagging**: when `JudgeGroup.evaluate()` runs inside a job context (e.g. in `on_session_end`), each judgment is tagged on the session as `lk.judge.<name>:<verdict>` and surfaces in LiveKit Cloud. In pytest it silently no-ops. Same code works in both places (see the `frontdesk` example in livekit/agents).

### 6.4 Agent Simulations (BETA; Python & Node; run on LiveKit Cloud)

Requirements: CLI `v2.16.4+` (Python) / `v2.16.7+` (Node), audio needs `v2.18.3+`; Agents `1.6.6+` (Python) / `@livekit/agents 1.6.0+`; authenticated LiveKit Cloud project. Concurrency: 15 per run by default (max 20 explicit), 30 per project across runs. Text runs are dispatched to Inference as **low-priority batch load**; audio runs at normal priority.

Three components: **simulated user** (LLM following scenario `instructions`), **your agent** (real entrypoint/tools, spawned as a local worker under a temporary name, or `--agent-name` for a deployed one, `""` = project default agent), **judge** (grades transcript against `agent_expectations`, records pass/fail + full transcript).

Commands: `lk agent simulate text -n 10` (generates scenarios from source — uploads your code, asks to confirm, `--yes` for CI), `--scenarios scenarios.yaml`, `audio`, `list`, `view RUN_ID`, `export RUN_ID > run.json` (run, summary, exact per-job chat contexts), `--concurrency N`. Non-zero exit when any scenario fails.

**Scenario YAML fields**: `label` (name the situation, not the feature), `instructions` (script for the simulated user), `agent_expectations` (outcome the judge grades), `tags` (e.g. `feature:`, `suite: smoke`), `userdata` (nested mapping passed to the agent; drives deterministic mocks and `expected_state`).

**Hooking scenarios into the agent**: `ctx.simulation_context()` returns `SimulationContext` (or `None` in prod) with `.userdata()` (keys as written in YAML), `.simulation_mode` (`SimulationMode.SIMULATION_MODE_TEXT`). Seed deterministic backends from userdata; keep the production path unchanged. Python: `mock_tools(MyAgent, mocks, session=session)`.

**Grade on final state**: register `on_simulation_end(ctx: SimulationContext)` on `@server.rtc_session(...)` (or `onSimulationEnd` in Node). Read `ctx.job_context.primary_session` (Python) to compare final DB/userdata state vs `expected_state`; call `ctx.fail(reason=...)`. Final result = simulator verdict AND your check — your callback can only fail, never pass. `ctx.simulator_verdict` exposes the judge's success flag and reason. "A polished conversation can still book the wrong room."

**Scenario-writing guidance (directly usable as eval design rules)**:
- Start from a caller goal; write the straightforward version, then one complication per scenario (change of mind, conflicting requests, self-correction, refusal pushback).
- Script the simulated user in point-form blocks (hotel example): `PERSONA`, `OPENING LINE` (exact, quoted — fixed start state), `FACTS` (reveal one per turn only when asked — otherwise the sim user dumps everything and info-gathering isn't tested), `DO, IN ORDER`, `REACTIONS` (branching), `HIDDEN TRUTH`, `GROUND TRUTH` (never volunteered; used to check read-backs).
- State expectations as **outcomes**, not process or exact wording; name the fail case explicitly when pass/fail read similarly ("Booking the 30th, two guests, or the 0190 number is a fail").
- Expect a refusal when a refusal is correct (policy limits, prompt-extraction attempts); an agent that refuses and abandons the caller still fails — it must offer an alternative/escalation. Conflicting requests: pass = name the conflict and let the caller choose; fail = invent an option satisfying both.
- Realistic caller behaviour: wrong number corrected in the same breath; everything in one dense opening turn and push-back if re-asked; correct one value mid-read-back; spell an uncommon name and insist on it. These matter most in audio mode.
- Reproducibility: absolute dates + pin the agent clock via env var (`HOTEL_TODAY`, `FRONTDESK_NOW`); seed state via `userdata`, never live backends.
- Coverage checklist: the 3–4 paths that carry traffic; edges of each path (missing info, change of mind, must-decline); tool failures (backend down / empty); misuse; every production failure you've fixed ("belongs here permanently").
- Generated scenarios are drafts: rewrite instructions as a script, restate expectations as outcomes, split multi-complication ones, relabel.
- Derive scenarios from real sessions: Sessions → Agent insights → **Turn into a test** → `scenario.yaml`. Run it BEFORE fixing to confirm it reproduces the failure. If a derived scenario passes but production failed, the missing factor is audio, state, or detail.
- Keep aspirational (never-passed) scenarios in a separate file.
- Reference scenario sets: `examples/frontdesk/scenarios.yaml`, `examples/hotel_receptionist/scenarios_guardrails_and_faq.yaml` (transcript-graded adversarial/FAQ), `examples/hotel_receptionist/scenarios_tool_accuracy.yaml` (graded against final DB state) — 100 scenarios for the hotel agent.

**Audio mode** (`lk agent simulate audio`): simulated user joins as a participant, publishes audio, interrupts like a real caller. Uses your configured STT/TTS/VAD (billed); CLI applies the same turn-detection and adaptive-interruption defaults as a deployed agent so turn-taking measurements reflect production. Degradation flags: `--background-noise`, `--low-quality-microphone`, `--packet-loss`.

**Audio-run metrics (this is LiveKit's own benchmarking rubric)**:
- *Responsiveness*: end-to-end latency **as the caller heard it**, at p50/p95/p99; positive = gap, negative = agent talked over caller. Tracked separately from agent-reported latency. Stage breakdown: STT + endpointing delay, LLM TTFT and time-to-first-sentence, tokens/sec, TTS TTFB.
- *Turn-taking score* with specific failures: end-of-turn mispredictions (agent spoke before caller finished), time-to-yield after barge-in, false interruptions, share of overlapping speech, unfilled silences after a natural pause, caller turns never answered.
- *Speech accuracy*: WER and CER **in both directions**; key entities (names, IDs, confirmation codes, amounts) scored separately, with recall distinguishing "never recognized" from "recognized then lost".
- *Conversation quality* (judged from dialog, both modes): overall score, conciseness, whole-call issue flags — unnecessary tool calls, information loss, redundant statements, poor question quality — combined into a conversation-progression score.
- Caveat: agent self-reported metrics need the agent's own session data; judged metrics need the text judge to have run.

### 6.5 CLI Agent Debugger (`lk agent debugger`, CLI `v2.18.8+`)

Text-mode, no room, one turn per command: `start`, `say "..."` (prints tool calls with args/results, handoffs, errors, reply), `logs`, `chat-history`, `status`, `events`, `restart`, `stop`. `--json` gives machine-readable output (user text, reply, turn duration, event list); `say` exits non-zero on agent error or timeout. Port `8775` default; auto-stops after 30 min idle. Built so a coding agent can test the agent it's building (`npx skills add livekit/agent-skills --skill debugging-livekit-agents`). Cannot show anything about speech.

### 6.6 Agent Console (browser) and console mode

Agent Console (Python `1.5.2+`, Node `1.2.4+`) panes: Summary, Audio (waveforms + interruption/backchannel events), Events (state transitions, transcription, turn detection, tool execution, metrics, errors), Session, Participants, RPC (outbound only), DTMF keypad, Metrics (per-stage timing — "compare models, tune endpointing, identify bottlenecks"), Usage. "Observe in Console" joins a live session as a hidden participant. `lk agent console` (`--text`, `--record`) simulates a room locally with no job metadata.

### 6.7 Observability & metrics the SDK exposes

- **Agent Insights** (Cloud; Python `1.3.0+`, Node `1.0.18+`): transcripts (incl. tool calls, handoffs), traces (spans per stage), logs, audio recordings (user audio **after** noise cancellation). 30-day retention. `record=` on `session.start()`: `True`/`False`/dict of `audio`, `transcript`, `traces`, `logs`, `redaction`.
- **Four metric surfaces**: per-plugin `metrics_collected` (e.g. `llm.on("metrics_collected")`), per-turn `ChatMessage.metrics` (`MetricsReport`), per-session live `session_usage_updated` / `session.usage`, per-session final `ctx.make_session_report()` → `SessionReport.to_dict()` (identifiers, timestamped history, all events, recording metadata, session options). Session-level `metrics_collected`, `UsageCollector`, `UsageSummary` are deprecated.
- **Per-turn latency** (`ChatMessage.metrics`): user → `transcription_delay`, `end_of_turn_delay`, `on_user_turn_completed_delay`; assistant → `llm_node_ttft`, `tts_node_ttfb`, `playback_latency`, `e2e_latency` ("from when the user stopped speaking to when the agent began responding"); both → `started_speaking_at`, `stopped_speaking_at`.
- **Metric types**: `VADMetrics` (`idle_time`, `inference_duration_total`, `inference_count`); `STTMetrics` (`audio_duration`, `duration` (0 if streaming), `streamed`); `EOUMetrics` (`end_of_utterance_delay` incl. `transcription_delay`, `on_user_turn_completed_delay`, `speech_id`; not emitted with server-side turn detection); `LLMMetrics` (`duration`, `completion_tokens`, `prompt_tokens`, `prompt_cached_tokens`, `total_tokens`, `tokens_per_second`, `ttft`, `speech_id`); `RealtimeModelMetrics` (`ttft` = first audio token, may be −1/negative; modality token breakdowns; `session_duration`); `TTSMetrics` (`audio_duration`, `characters_count`, `duration`, `ttfb`, `speech_id`, `streamed`); `InterruptionMetrics` (`total_duration` RTT, `prediction_duration`, `detection_delay`, `num_interruptions`, `num_backchannels`, `num_requests`); `AvatarMetrics` (join latency, `playback_latency`).
- **Latency formula in docs**: `total_latency = eou.end_of_utterance_delay + llm.ttft + tts.ttfb`; correlate stages via `speech_id` (None for proactive speech / `say()`).
- **Usage / cost**: `LLMModelUsage` (tokens by text/audio/image/cached; `session_duration`), `TTSModelUsage` (`characters_count`, `audio_duration`), `STTModelUsage` (`audio_duration`), `InterruptionModelUsage` (`total_requests`). Python seconds, Node milliseconds.
- **Session events** (for harness timelines): `agent_state_changed` (`initializing/idle/listening/thinking/speaking`), `user_state_changed` (`speaking/listening/away`; `user_away_timeout` 15 s), `user_input_transcribed` (`transcript`, `is_final`, `speaker_id`, `language`), `user_transcription_timeout`, `conversation_item_added`, `function_tools_executed` (`function_calls`, `function_call_outputs`, `has_tool_reply`, `has_agent_handoff`), `speech_created` (`user_initiated`, `source` say/generate_reply/tool_response), `overlapping_speech` (`is_interruption`, `probability`, `detection_delay`…), `agent_false_interruption` (`resumed`), `session_usage_updated`, `error` (`recoverable`, `source`), `close` (`reason`, `error`), `*_availability_changed`.
- **OpenTelemetry**: `set_tracer_provider(provider, metadata=, allow_pii=)` exports the same spans (`lk.*` + `gen_ai.*` semconv) to Langfuse or any OTLP backend. Agents 1.7.0 renamed 12 content attributes to `lk.pii.*` (`lk.pii.chat_ctx`, `lk.pii.user_transcript`, `lk.pii.response.text`, `lk.pii.function_tool.arguments/output`, `lk.pii.input_text`, …). `gen_ai.usage.input_tokens` already includes `gen_ai.usage.cache_read.input_tokens`.
- **PII redaction** (Cloud, off by default; 41 categories/10 groups, 36 default-on): `<redaction type="..."/>` markers in transcripts, beeps in audio. Not applied to locally collected data or Egress recordings.
- **Egress**: `RoomCompositeEgressRequest` for raw audio/video to your own storage.
- Third-party eval/monitoring vendors named by the docs: Bluejay, Cekura, Coval, Hamming.

### 6.8 Telephony testing checklist

Pre-call: number provisioned, dispatch rule exists and `agent_name` matches worker registration, worker healthy, trunk creds match (403 = mismatch, 503 = wrong address). Verify room name/prefix, agent participant present, SIP participant attributes (`kind=SIP`, `sip.trunkPhoneNumber`, `sip.phoneNumber`, `sip.trunkID`, `sip.ruleID`, `sip.callID`). Test hangups (`CLIENT_INITIATED` closes session by default), pre-answer failures (`wait_until_answered=True` → `SipCallError` `USER_REJECTED` / `USER_UNAVAILABLE`), mid-call disconnects. Console has a DTMF pane; SIP-participant mocking is "upcoming".

---

## PART 7 — IMPLICATIONS FOR OUR EVAL DESIGN (proposal for the next iteration; decisions still open)

### 7.1 Benchmarking criteria that the framework makes measurable

| Layer | Criterion | Ground truth / signal | Native mechanism |
|---|---|---|---|
| **Task outcome** | Goal reached; correct final state | `expected_state` in `userdata` vs DB/`session.userdata` | `on_simulation_end` + `ctx.fail()`; custom `Judge` |
| **Tool correctness** | Right tool, right args, no redundant/unnecessary calls, error recovery | `function_call` items (raw JSON args), `function_tools_executed` | `is_function_call`, `tool_use_judge`, mocked tools raising errors |
| **Grounding / accuracy** | Claims trace to tool outputs; no hallucination | tool outputs vs assistant text | `accuracy_judge`; scenario `GROUND TRUTH` blocks |
| **Information gathering** | Asks for withheld facts one at a time; captures the *corrected* value; reads back | scenario `FACTS`/`HIDDEN TRUTH` | simulated-user script + outcome expectation |
| **Refusal & safety** | Declines out-of-policy, offers alternative/escalation, resists prompt extraction | scenario expectations | `safety_judge`; guardrail scenario set |
| **Context continuity** | No re-asking after handoff/task; details survive to the end | history across `agent_handoff` items | `handoff_judge`; multi-turn `session.run` |
| **Conversation quality** | Concise, coherent, relevant, one question per turn, TTS-safe formatting | transcript | `conciseness_judge`, `coherence_judge`, `relevancy_judge`; deterministic regex judges for markdown/emoji/URLs/digits |
| **Latency (agent-side)** | `e2e_latency`, `llm_node_ttft`, `tts_node_ttfb`, `end_of_utterance_delay`, `transcription_delay` | `ChatMessage.metrics`, per-plugin metrics | data hooks / session report |
| **Latency (caller-perceived)** | p50/p95/p99 gap or overlap as heard | audio simulation | `lk agent simulate audio` export |
| **Turn-taking** | EOT mispredictions, time-to-yield, false interruptions, overlap share, unfilled silences, unanswered turns | audio simulation; `agent_false_interruption`, `overlapping_speech` | audio sim metrics; `eot-bench` for detector-level |
| **Speech accuracy** | WER/CER both directions; entity recall (names, codes, amounts) | audio sim | audio sim metrics; degraded-audio flags |
| **Robustness** | Behavior on tool failure, STT timeout, provider fallback, packet loss, noise | `error`/`close` events, `*_availability_changed` | `mock_tools` errors; `--packet-loss` etc. |
| **Cost** | tokens, TTS chars, STT seconds, interruption requests per successful task | `session.usage` | `session_usage_updated` |
| **Stability** | Pass-rate variance across N repeated runs (docs: behavior "varies between models and between runs") | repeated sims | run N× per scenario, report pass@k / consistency |

### 7.2 Eval tiers to mirror the framework's lifecycle

1. **Tier 0 – deterministic text checks** (`session.run` + `RunResult`, custom `Judge` subclasses): tool call/arg exactness, no-events-after, formatting rules, handoff occurrence, error-path replies. Cheap; run on every change.
2. **Tier 1 – LLM-judged single turns** (`.judge(llm, intent=)`) and **full-conversation JudgeGroups** over `session.history`.
3. **Tier 2 – text-mode simulations** (`scenarios.yaml`, seeded `userdata`, final-state grading). Pass/fail + judge reasoning + exported chat contexts as artifacts.
4. **Tier 3 – audio-mode simulations** on a schedule, with and without degradation flags; track p95 caller-perceived latency, turn-taking score, entity recall.
5. **Tier 4 – production-derived scenarios** ("Turn into a test") feeding back into Tier 2.

### 7.3 Open decisions the extracted info bears on (to settle next iteration)

- **Judge model**: docs examples use `google/gemma-4-31b-it` (cheap, hosted, ZDR) for `judge()` and `openai/gpt-4o-mini` for `JudgeGroup`. Any `LLM` instance or `provider/model` string works, so a stronger judge (e.g. a Claude or GPT-5-class model via plugin or Inference) is a drop-in. Decision pending: judge ≠ agent model to avoid self-preference; consider 2-judge agreement for "maybe" verdicts.
- **Agent-under-test model matrix**: candidates named in docs — `google/gemma-4-31b-it` (default), `openai/gpt-4.1`/`gpt-5.x`, `claude-sonnet-4-6` (Anthropic plugin default), Gemini 3.x flash, plus realtime GPT-Live / Gemini Live. Realtime models lose `llm_node_ttft`/`tts_node_ttfb` and interim transcripts, so latency evals need different fields (`RealtimeModelMetrics.ttft`, caller-perceived latency).
- **Where evals run**: unit tests and JudgeGroups run anywhere (no room); simulations require a LiveKit Cloud project + authenticated CLI; Insights/PII/adaptive interruption are Cloud-only.
- **Scenario corpus**: adopt LiveKit's block format (`PERSONA / OPENING LINE / FACTS / DO, IN ORDER / REACTIONS / HIDDEN TRUTH / GROUND TRUTH`) and its coverage checklist; start from the hotel_receptionist (100 scenarios) and frontdesk examples as reference shapes.
- **Determinism controls**: `seed` (best effort), `temperature`/`top_p`, pinned clock env var, `userdata`-seeded backends, session-scoped `mock_tools`, text mode. Report variance rather than assuming determinism.
- **Scoring**: `EvaluationResult.score` (pass=1/maybe=0.5/fail=0) is the native aggregate; decide whether to adopt it or a weighted rubric across the Part 7.1 layers.

---

## PART 8 — τ-BENCH (taubench.com, Sierra Research) — the external benchmark we will run against

Full notes with every number and table: `notes/05-tau-bench.md`. Summary:

**What it is**: an agent (system under test) converses with an LLM user simulator, calls domain tools against a database, and must follow a domain policy. Reward is verified against the **final DB state** (plus required communicated strings), never on how the conversation sounded. `pass^k` = probability of success in all k independent trials (k = 1..4 reported). Repo: github.com/sierra-research/tau2-bench (`uv sync --extra voice`, `tau2 run ...`). Three leaderboards: τ²-bench (text), τ³-Banking (text + knowledge retrieval), τ³-Voice (full-duplex voice).

**Domains available**

| Domain | Tasks | Nature | Dual control (user tools) | Reward basis in data |
|---|---|---|---|---|
| `retail` | 114 | orders, returns, exchanges, cancellations, modifications; identity via name+zip/email | no | `DB` + `NL_ASSERTION` (112), `DB` (2) |
| `airline` | 50 | flight change/cancel/upgrade/booking under policy (24-h rule, insurance, refund method); many refusal tasks | no | `DB` + `COMMUNICATE` (50) |
| `telecom` | 114 base (2285 generated) | troubleshooting: `mms_issue` 49, `mobile_data_issue` 36, `service_issue` 29; persona difficulty None/Easy/Hard | **yes** (toggle roaming, reboot, status bar, speed test) | `ENV_ASSERTION` on user device state (18 of the 20-task `small` set), `ENV_ASSERTION` + `ACTION` (2) |
| `banking_knowledge` | 97 | fintech support over 698 unstructured docs, 51 discoverable tools; avg 18.6 docs, 9.5 tool calls per task | yes (e.g. apply for card) | `DB` (88), `ACTION` (9); needs `--retrieval-config` |
| `mock` | — | dev domain | — | — |

Voice track (standard leaderboard) = retail + airline + telecom = **278 tasks**, `--speech-complexity regular`, 1 trial; banking voice exists as a custom track. Every domain ships `tasks_voice.json` with pre-sampled audio configs.

**Task schema**: `user_scenario.instructions {reason_for_call, known_info, unknown_info, task_instructions}` scripts the simulated user (withheld facts, persona traits, reactions); `evaluation_criteria {actions (ONE reference trajectory, replayed to derive target DB hash — not a requirement), communicate_info, nl_assertions, env_assertions, reward_basis}`; telecom adds `ticket` + `initialization_actions`; banking adds `user_tools`, `required_documents`. Refusing correctly (no DB writes) scores 1.0.

**Voice user simulator** (v1.0 = GPT-4.1 temp 0 → ElevenLabs v3 TTS → noise mix → channel): 7 personas (2 American "control": calm Matt Delaney, impatient Lisa Brenner; 5 "regular": elderly Mildred Kaplan, Bengali Arjun Roy, Sichuan Wei Lin, French-accented Mamadou Diallo, Marathi Priya Patil). Regular conditions: G.711 μ-law 8 kHz, background noise 15 dB SNR ± 3, bursts 1/min at −5..+10 dB, 2 % frame drops (~100 ms), 20 % muffling, vocal tics + non-directed phrases 0.7/min, LLM backchannels, interruptions on. 200 ms ticks, 600 s max, user waits 1.0 s silence, interrupt/backchannel decisions every 2.0 s. Ablation presets isolate audio / accents / behavior. Providers with adapters: openai, gemini, xai; custom agents implement `FullDuplexAgent.get_next_chunk()`.

**Interaction metrics** (computed offline from ticks, no judges): response latency L_R, yield latency L_Y, response rate R_R, yield rate R_Y (within 2.0 s), agent-interruption rate I_A, selectivity for backchannels / vocal tics / non-directed speech (error = yields within 1.0 s or responds within 2.0 s).

**Headline numbers (2026-09-27)**: text ceiling GPT-5 85 % vs GPT-4.1 54 % on the 278 tasks; τ²-bench text top Qwen3.5-397B 87.9 avg, Claude Opus 4.5 85.3, GPT-5.2 84.8. Voice top gpt-live-1 81.7 (78.9 / 82.0 / 84.2), Pine Voice Preview 75.4 (cascaded ASR→LLM→TTS + custom interaction model + background gemini-3.5-flash tool agent), grok-voice-think-fast-1.0 67.3, gemini-3.1-flash-live HIGH 43.8, gpt-realtime-2 42.4. **Cascaded baseline (Deepgram nova-3 + gpt-4.1 + Deepgram Aura) = 31.2 avg (retail 28.9 / airline 48.0 / telecom 16.7), L_R 4.24 s, I_A 0.64** — the architectural twin of a default LiveKit STT-LLM-TTS agent. Banking text top 55.2 (Qwen 3.8 Max); banking voice 10–32.

**Failure taxonomy from the τ-Voice paper** (79–90 % of failures are agent-caused): Logical (policy misapplication, missed constraints), **Transcription** (spelled names, emails, IDs → authentication bottleneck), Hallucination (claims completion without tool call), VAD/Unresponsive, Timeout; user-sim errors 10–21 %. Ablations: accents hurt most on average (−1..−18 pp, provider-specific), turn-taking −14..+4 pp, noise −2..−4 pp. Knowledge-domain failure modes: unwarranted assumptions instead of clarifying (~23 %), multi-hop interdependencies (~14.5 %), implicit subtask ordering (~5 %), over-trusting user claims (~4 %).

**Plan hooks for the next iteration**
- Pick domain by iteration cost vs. coverage: `airline` (50 tasks, fastest, refusal-heavy) → `retail` (paper's primary) → `telecom` (dual control; hardest for voice) → `banking_knowledge` (RAG).
- Run text mode first (`tau2 run --domain X --agent-llm <model>`) to get the model's reasoning ceiling, then voice (`--audio-native` or a LiveKit `FullDuplexAgent` adapter) to measure the voice gap; compare against the Cascaded-baseline row and the matching realtime row.
- Mine failures with the paper's taxonomy plus τ-bench's `partial_action_reward`, `action_checks`, and interaction metrics; turn recurring ones into LiveKit `scenarios.yaml` / `session.run` evals; iterate on prompts (identity spelling/read-back, tool-before-claim, multi-part request tracking, backchannel selectivity, policy checklists).
- Comparability rules: `base` split, all tasks, standard user sim (text gpt-5.2 low; voice v1.0 gpt-4.1), tau2-bench ≥ 1.0.1; prompt changes make a submission "custom".

## PART 9 — SELECTED EVAL BEHAVIOURS AND VOICE AGENT CANDIDATES (decided 2026-09-27)

Scope decision: **voice agents only**. Benchmark track = τ³-Voice (full-duplex, `--speech-complexity regular`, standard v1.0 user simulator = gpt-4.1). Domains in scope = retail (114), airline (50), telecom (114). Banking is excluded (voice runs exist only as custom submissions scoring 10–32). Improvement lever = **prompt changes only**.

### 9.1 Six voice behaviours to build evals around

| # | Behaviour | Voice evidence | How to measure |
|---|---|---|---|
| 1 | **Identifier capture under accent and noise** — spelled names, emails, order/reservation IDs, zip codes | Transcription = second-largest agent failure class in τ-Voice (10 and 16 per cohort); accents the most damaging ablation (up to −18 pp); authentication is "the dominant bottleneck" | τ-bench DB end state on lookup tasks with the five accented personas; LiveKit audio-sim entity recall (recognized vs recognized-then-lost); scenarios that spell an uncommon name and insist on it; `--low-quality-microphone`, `--background-noise` |
| 2 | **Selectivity** — talk through "mm-hmm", coughs, "hold on a second" instead of stopping or answering them | OpenAI realtime rows score 0.01–0.13 backchannel selectivity; Gemini/Grok 0.66–0.94 | τ-bench S_BC, S_VT, S_ND (yield within 1 s or respond within 2 s = error); LiveKit `overlapping_speech`, `InterruptionMetrics.num_backchannels` |
| 3 | **Barge-in handling** — yield within 2 s on a real interruption, don't talk over the caller, resume after a false interruption | Cascaded baseline interrupts 0.64×/user turn; Gemini yields on ~half of interruptions | τ-bench R_Y, L_Y, I_A; LiveKit audio-sim time-to-yield and overlap share; `agent_false_interruption.resumed` |
| 4 | **Responsiveness and silence** — answer every caller turn, perceived latency ≈ 1 s, never go quiet after a failed tool call | Cascaded baseline L_R 4.24 s, R_R 0.79; "unresponsive after repeated failures" and VAD/unresponsive are named failure classes | τ-bench L_R, R_R; LiveKit caller-perceived p50/p95 latency, unfilled silences, unanswered turns, `e2e_latency` |
| 5 | **Spoken read-back and confirmation** — repeat key values in spoken form (digits, spelled codes), confirm before any write, keep the corrected value on a same-breath self-correction | τ-Voice examples: "verbally encoded characters trip up the agent"; LiveKit scenario guidance requires read-backs and corrected-value capture | Scenarios with `GROUND TRUTH` + same-breath correction; final-state grading; deterministic judge for digits/markdown in TTS text |
| 6 | **Grounded completion over a long call** — no hallucinated "done" without a tool call, no policy misapplication, no forgetting later parts of a multi-step request | Logical errors = largest voice failure class (13 and 16), hallucination 6 per cohort, "multi-step request amnesia" over six-minute calls | τ-bench DB reward, `partial_action_reward` on WRITE tools; LiveKit `accuracy_judge`, `task_completion_judge`; retail multi-order tasks |

Prompt-leverage note: behaviours 2–4 are driven mostly by the model and LiveKit turn-handling settings (turn detector, adaptive interruption, endpointing); prompts move them least. Behaviours 1, 5, 6 are where prompt changes have the most leverage. Behaviours 1, 5, 6 can be pre-screened in text mode; 2–4 need audio runs.

### 9.2 Five voice agent candidates and the domain to start each on

| # | Voice agent (LiveKit path) | τ³-Voice reference, pass^1 retail / airline / telecom | Start domain | Rationale |
|---|---|---|---|---|
| 1 | **Cascaded pipeline**: Deepgram nova-3 STT + gpt-4.1 + Deepgram Aura TTS, `AgentSession` with LiveKit turn detector | 28.9 / 48.0 / 16.7; L_R 4.24 s; I_A 0.64 | **Retail** | Only leaderboard row matching a LiveKit pipeline component for component; retail is where the paper ran all ablations. Reproduce first, then improve. **Default base configuration.** |
| 2 | **OpenAI GPT-Live** via `openai.realtime.GPTLiveModel(voice="marin")` | gpt-live-1: 78.9 / 82.0 / 84.2 (leaderboard top) | **Telecom** | Best on dual control; serves as the ceiling reference. |
| 3 | **OpenAI Realtime** `gpt-realtime-2` via `openai.realtime.RealtimeModel` | 47.4 / 58.0 / 21.9 (xhigh); selectivity ≈ 0 | **Airline** | Its best domain; 50 tasks keep runs cheap; prime subject for behaviour 2; supports LiveKit client-side turn taking (compare server VAD vs LiveKit detector). |
| 4 | **Gemini Live 3.1 Flash** via the Google realtime plugin, thinking HIGH | 45.6 / 64.0 / 21.9 (HIGH); 26.3 / 42.0 / 17.5 (MINIMAL); L_R 3.15 s | **Airline** | Strongest domain with a large gap to GPT-Live on identical tasks; thinking level = latency/accuracy knob. Does **not** support LiveKit client-side turn taking. |
| 5 | **xAI Grok Voice** via the SpaceXAI realtime plugin | grok-voice-think-fast-1.0: 62.3 / 66.0 / 73.7; 2.0: 59.6 / 56.0 / 71.9 | **Telecom** | Second-best native model on telecom with good selectivity; fair contrast with GPT-Live on dual control. July 2026 submission needed explicit server VAD settings (threshold 0.1, silence 1200 ms, prefix 600 ms) to detect user speech. |

Alternate (no published voice row): cascaded pipeline with a stronger LLM (GPT-5.2 or Claude Sonnet); Pine's cascaded production system scored 75.4, showing the architecture can compete with native models.

Recommended pairing: candidate 1 on retail as the base, candidate 2 on telecom as the ceiling. Keep the standard v1.0 user simulator so numbers remain comparable with the leaderboard; any prompt change makes a formal submission "custom", which is acceptable for internal evals.

## PART 10 — IMPLEMENTATION STATUS (evals for behaviours 1–3, cascaded agent, retail)

Plan: `~/.claude/plans/i-have-gone-through-groovy-tower.md`. Decisions: real LiveKit `AgentSession` bridged into τ-bench (not the plugin-level `livekit` provider); retail; full 114-task baseline + frozen 30-task iteration subset; paid OpenAI + Deepgram + ElevenLabs keys.

### Layout
```
external/tau2-bench/            git clone of sierra-research/tau2-bench (main @ b7ea907, 2026-09-17; livekit-agents upgraded 1.5.1 → 1.8.3 via uv lock)
  src/tau2/voice/audio_native/livekit_session/   NEW provider: config.py (SESSION_CONFIGS: cascaded-session | cascaded-session-vad | cascaded-session-mini),
                                                  session_provider.py (AgentSession + TickAudioInput/TickAudioOutput/TickTextOutput + tool bridge + JSONL sidecar),
                                                  discrete_time_adapter.py (LiveKitSessionAdapter), README.md
  edits: config.py, data_model/simulation.py (provider literal, agent_prompt_file, cascaded_config resolution), cli.py (--audio-native-provider livekit_session, --agent-prompt-file),
         agent/discrete_time_audio_native_agent.py (prompt override), voice/audio_native/adapter.py (factory), runner/{batch,worker}.py (plugin preregistration,
         _current_artifact_dir contextvar), voice/audio_native/livekit/__init__.py (per-plugin preregistration)
  tests/test_voice/test_livekit_session_adapter.py   3 pure tests pass; end-to-end test gated by TAU2_RUN_LIVEKIT_SESSION_TESTS=1 + keys
agent/retail_agent.py           text-mode AgentSession over a real τ-bench retail environment (for evals/text); prompts/v0_tau_cascaded.md = τ-bench CASCADED_MODEL_INSTRUCTION verbatim
evals/subsets/retail_iter30.json  frozen: 20 Sierra voice-fragile ids + 10 persona-balanced (6 per persona; 26 with unknown_info; 18 with ≥2 writes)
evals/b1_identifiers.py · b2_selectivity.py · b3_bargein.py · report.py · common.py · text/test_b1_spelling.py
scripts/run_retail.sh {smoke|compare|subset|full|ablate|post}   .env.example
```

### How to run (after filling `external/tau2-bench/.env` from `.env.example`)
```
cd external/tau2-bench && uv run python -m tau2.voice.scripts.setup_voices      # 7 ElevenLabs personas → TAU2_VOICE_ID_* into .env
scripts/run_retail.sh smoke                                                     # 2 tasks, control, audio debug
scripts/run_retail.sh compare                                                   # built-in livekit vs livekit_session on 5 tasks
scripts/run_retail.sh full  agent/prompts/v0_tau_cascaded.md v0                 # baseline (114 tasks, regular)
scripts/run_retail.sh subset agent/prompts/v1_<slug>.md v1                      # prompt iteration (30 tasks)
scripts/run_retail.sh post <run_name>                                           # interaction metrics + review + report
uv run --project external/tau2-bench python evals/b1_identifiers.py <run>  (same for b2/b3; report.py <run> --baseline <v0 run> --subset)
cd external/tau2-bench && uv run pytest ../../evals/text/test_b1_spelling.py -q  # text pre-screen (local Ollama by default; TAU2_SESSION_PRESET=cascaded-session + key for paid)
```
PRESET=cascaded-session needs LiveKit Cloud creds (hosted v1 turn detector + adaptive interruption model); without them use PRESET=cascaded-session-mini (local v1-mini, ~108 MB weights via livekit-local-inference) or cascaded-session-vad.

### Local, keyless stack (decided 2026-09-27: "shift to local inferencing")
```
scripts/local_stack.sh up|down|status|logs    # Ollama (llama3.1:8b) :11434 · faster-whisper STT server :8000 · Kokoro TTS server :8880
scripts/local_stt_server.py                   # OpenAI-compatible /v1/audio/transcriptions (faster-whisper small.en, CPU int8)
scripts/local_tts_server.py                   # OpenAI-compatible /v1/audio/speech (kokoro-onnx, 24 kHz PCM)
STACK=local scripts/run_retail.sh smoke ...   # default STACK; sets PRESET=local, --user-llm ollama_chat/llama3.1:8b,
                                              # TAU2_VOICE_SYNTHESIS_PROVIDER=kokoro, TAU2_VOICE_DECISION_MODEL, --hallucination-retries 0, concurrency 1
```
- Session presets: `local` (whisper server → Ollama llama3.1:8b → Kokoro server, v1-mini turn detector, VAD interruptions), `free-groq`, `free-ollama-deepgram`; paid presets unchanged. `OpenAICompatLLMConfig/STTConfig/TTSConfig` accept any OpenAI-compatible `base_url` (`api_key_env="NONE"` for keyless servers).
- τ-bench user simulator: new synthesis providers in `src/tau2/voice/utils/local_tts_utils.py` — `kokoro` (local; US/UK voices only → accent dimension weakened) and `edge` (edge-tts, keyless but network; keeps accents: en-IN, en-HK, fr-FR…). Selected via `TAU2_VOICE_SYNTHESIS_PROVIDER`; persona→voice overrides `TAU2_KOKORO_VOICE_<PERSONA>` / `TAU2_EDGE_VOICE_<PERSONA>`. `TAU2_VOICE_DECISION_MODEL` and `TAU2_REVIEW_MODEL` are LiteLLM strings (Ollama: `ollama_chat/llama3.1:8b`).
- Everything local is "custom" w.r.t. the leaderboard (different LLM, TTS, user-sim LLM); use it for building/iterating evals, not for comparable numbers.
- Metric validation: the four eval scripts were run on the leaderboard's cascaded-baseline retail trajectories (downloaded to `runs/leaderboard/retail_regular_livekit`, 114 sims): pass^1 28.95 vs 28.9; L_R 4.02, L_Y 0.84, R_R 0.77, R_Y 0.99, S_BC 0.57, S_VT 0.50, S_ND 0.52 — all match Sierra's retail-domain panel to 2 decimals. b1 on that run: auth succeeded in 56 %, 48/114 sims failed authentication (the paper's bottleneck), name entities frequently `used_wrong` (e.g. "Youssef Li / Yousuf Li" for "Yusuf Li").
- Machine notes (2026-09-28): Ollama 0.14.2 failed Metal shader compilation on Darwin 25.3; `brew upgrade ollama` → 0.34.4 fixed it (llama3.1:8b runs 100 % GPU, tool calls in ~2.6–3.7 s). IPv6 to Hugging Face/GitHub hangs here → the local servers and `local_tts_utils.py` force IPv4; whisper `small.en` is cached at `~/.cache/local-stt/small.en`, Kokoro at `~/.cache/kokoro-onnx` (model + 27 English voices fetched from `onnx-community/Kokoro-82M-v1.0-ONNX`; the GitHub release mirror is ~190 KB/s).
- Verified local loop (2026-09-28): `test_adapter_end_to_end_local_stack` passes — Kokoro user utterance → whisper STT (0.9 s) → Ollama tool call → Kokoro agent audio, sidecar shows listening→thinking→tool→speaking→listening. Two bugs fixed on the way: plugins must be preregistered on the calling thread (adapter now does it), and the tool bridge needed real (non-string) type annotations for `RunContext` injection.

### First real τ-bench runs on the local stack (2026-09-28, `scripts/run_retail.sh smoke`, tasks 6 & 14, control)
- Run 1 (`retail_smoke_local_v0_tau_cascaded_20260928_1203`): both tasks `infrastructure_error` — my adapter's `provider` property raised after `disconnect()` and the orchestrator reads `adapter.provider.session_id` post-run. Fixed (provider kept after disconnect). Also Ollama default 4096-token context overflowed (policy ≈ 2k tokens + 16 tool schemas ≈ 3k) → LLM timeouts; now `OLLAMA_CONTEXT_LENGTH=16384`.
- Run 3 (`…_1305`): both tasks completed, reward 0/2, no infra errors; sidecar per call: 12 final transcripts, 8 tool executions, 17 LLM calls, 29 TTS segments. Diagnosis: agent spoke on 1 of 839 ticks (task 6) — LLM TTFT 7–12 s (prompt cache thrash: agent and user-sim share one Ollama slot) while the simulated user re-prompts after 1 s silence → every late reply was cut off as a barge-in; user-sim speech also started with a leaked "assistant\n\n" header. Behaviour evidence already visible: agent invented order id `#W0000000` and called `cancel_pending_order` without any lookup (behaviour-6 failure), spoke JSON aloud ("This is the information you requested in JSON format: {...}").
- Fixes: `OLLAMA_NUM_PARALLEL=2` (separate KV slots), role-header strip in `tau2/utils/llm_utils.py::generate`, zero-cost pricing entry for `openai_compat`.
- Run 4 (`…_1356`): cache fix confirmed (follow-up turns TTFT ≈ 0.8 s) but the first agent turn still pays ~15 s prompt-eval for 4.2k tokens on this machine. New defect: the **user simulator role-flips** into the agent ("I'd be happy to assist you…") — cause: τ-bench appends system-role notes mid-conversation ("[Both parties silent…]", "REMINDER: You are the CUSTOMER…") after assistant turns; llama3.x's template can't place those → leaked "assistant" headers + role confusion. Fix: `_adapt_mid_conversation_system_messages` in `llm_utils.generate` rewrites later system messages as `[System note]` user turns for Ollama models (`TAU2_LOCAL_CHAT_FORMAT=1` forces it). Cloud models unaffected.
- Eval scripts confirmed working on `livekit_session` output incl. sidecar (b1 uses heard transcripts; b2/b3 read `overlapping_speech`/`agent_false_interruption`).

- Run 5 (`…_1400`, all fixes): 2/2 completed, reward 0/2, both ended on the 10-tool-error limit; user simulator now stays in character. Panel: L_R 3.05 s, R_R 47 %, R_Y 100 %, I_A 0.29. Detailed failure catalogue in `runs/analysis/local_smoke_findings.md` (hallucinated ids, spelled letters passed into tool args, mis-reconstructed spelled email, tool call spoken as JSON, latency death spiral). Run 6 = same with `OLLAMA_NUM_PARALLEL=3`, ctx 12288.

- Run 6 (3 slots): no change — Ollama log shows the agent alternates two prompt families (with the ~2.5k-token tool block after user speech; without tools for the post-tool reply) that share only the system prompt, so each tool-bearing turn re-evaluates ~2.5k tokens (≈5–7 s at 400–525 tok/s). Slot count can't fix it. Local-stack decision: `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`, and the simulated caller waits `USER_WAIT=8.0` s (`--wait-to-respond-other`) before re-prompting — **local-only deviation**; turn-taking metrics (L_R, R_R, behaviours 2–4) from local runs are not comparable to the leaderboard, behaviours 1/5/6 are fine. `llama3.2:3b` pulled as a faster option for turn-taking studies (tool calling weaker).

- Run 8 (`…_1441`, all fixes + USER_WAIT 8 s) is **invalid**: the laptop idle-slept at 14:49 (`pmset`: sleep after 1 min) and dark-woke for ~5 s every 15 min until 17:12, so task 6 spanned 2.5 h of wall clock with 15-min holes inside single ticks. `scripts/daemonize.py` now wraps detached commands in `caffeinate -ims` (`NO_CAFFEINATE=1` opts out); a closed lid still sleeps the machine. Always check `pmset -g log | grep -E "Sleep|Wake"` when a local run shows unexplained multi-minute ticks. Run 9 (`…_1717`) = run 8 relaunched under caffeinate.
- Run 9 (`…_1717`, first valid local run): 2/2 completed, reward 0/2 (both hit the 10-tool-error cap). Panel L_R 5.11 s · R_R 43 % · R_Y 92 % · I_A 0.40; agent speaks 7 % of ticks (run 5: 0 %). Agent failure catalogue (all prompt-addressable, see `runs/analysis/local_smoke_findings.md`): tool calls spoken as JSON (5/sim, counted as `json_spoken`), placeholder ids (`#W0000000`, `Doe`/`12345`) in real calls, ASR email passed literally (`"Mia, Garcia2723@example.com"` ×6, never read back), empty `first_name=""`. Harness defects: LiveKit's default 10 s LLM timeout killed 9/59 agent calls (→ `SessionConfig.llm_timeout_s`, 40 s on `local`); agent and user simulator sharing one llama3.1:8b runner evict each other's prompt cache (cold prompt eval 7.6 s vs 0.1 s warm) → run 10 moves the user simulator to `llama3.2:3b` (`USER_LLM=ollama_chat/llama3.2:3b`, own runner); llama3.1:8b as user simulator is suggestible (adopted the agent's placeholder "Doe" as its own name) — a stronger local user-sim model (qwen2.5:7b?) is an open item for behaviour-1 attribution.
- Run 10 (`…_2213`) invalid: with 3 KV slots the two runners (7.4 + 4.6 GB) exceed Ollama's memory budget on this 17 GB machine and were swapped every turn (`sched.go: model predicted to exceed available memory, evicting`); `local_stack.sh` now defaults to `OLLAMA_NUM_PARALLEL=2`. The laptop also slept on low battery overnight (caffeinate cannot stop that) — keep it on the charger for runs. Run 11 = run 10 config with 2 slots.
- Two-runner setup parked (2026-09-29): Ollama's scheduler is limited by macOS free pages (`system_free ≈ 2.3 GiB`), and a second instance on :11435 (`USERSIM_INSTANCE=1` in `local_stack.sh`, `USERSIM_OLLAMA=http://127.0.0.1:11435` in `run_retail.sh`) needs the other apps closed (6 GB swap in use → load hung). **Current local default**: one runner, both LLMs llama3.1:8b, 3 slots, `llm_timeout_s=40`, `USER_WAIT=15`. Run 11 = that config.
- Run 11 (`20260929_1135`, single runner, timeout 40 s, other-wait 15 s): valid, reward 0/2, both ended by the agent transferring to a human; R_R dropped to 20 % because τ-bench has **two** patience thresholds — `--wait-to-respond-other` (default 1.0 s, after the agent speaks) and `--wait-to-respond-self` (default 5.0 s, after the caller's own turn when the agent is silent). The self one fired inside the agent's 5–12 s LLM call and LiveKit discarded the in-flight reply every time. `run_retail.sh` now sets both to `USER_WAIT` (15 s). Run 12 = that. Behaviour evidence: first action `exchange_delivered_order_items(order_id="#W0000000", …)` with list args serialised as strings; spelled email lost by whisper small.en ("amaya.grcia.um2723 at example.com") and passed literally.
- **Run 12 (`20260929_1237`, both patience thresholds 15 s): local loop validated** — R_R 100 %, agent speaks 28–38 % of ticks, coherent 10-minute calls, reward 0/2 on the tool-error cap. Local v0 baseline behaviour: caller spells the email correctly, whisper hears "Mia. Garcia2723 at example, com", agent calls `find_user_id_by_email("Mia. Garcia2723@example, com")` without normalising or reading back (behaviour 1 = reconstruction step, not the spelling request); placeholder tool arguments ("the email the customer is using to contact you", `order_id="None"`, `#W0000000`) and the prompt's own "J, O, H, N" example leaking into arguments. User simulator (llama3.1:8b) remains suggestible (adopted "john_doe@gmail.com"). Local L_R ≈ 11 s is a stack property, not an agent metric.
- **Local LLM switched to qwen2.5:7b (2026-09-29)** for agent, user simulator and reviewer (`TAU2_LOCAL_LLM=llama3.1:8b` restores the old one everywhere). Reason: text pre-screen v0 — llama3.1:8b 0/3 (fabricated writes, placeholder lookups), qwen2.5:7b 2/3 (never calls a tool before identity, asks to spell). Run 13 = smoke on qwen to validate tool calling through the LiveKit openai plugin. The local judge often returns no structured verdict; the pre-screen treats that as inconclusive (warning), rule checks still apply. `evals/inspect_run.py` also counts `placeholder_arg_calls` (runs 9/11/12: 3–16 per sim on llama).
- Run 13 (`20260929_1341`, qwen2.5:7b): TTFT 0.26 s on every call (qwen's template keeps Ollama's prefix cache valid — the 6 s llama TTFT was a template artefact), R_R 100 %, first successful local authentication (`find_user_id_by_email` → mia_garcia_4516), zero placeholder arguments. qwen's failure profile = verbose 40-s monologues (policy asks for short replies), order-id fixation before authentication, passivity (0 tool calls in 22 min on task 6; stops acting after auth on task 14). User simulator on qwen still invents ids ("W00123456") → local-only anti-fabrication guard added to `llm_utils.generate` for user-sim calls (`TAU2_LOCAL_USERSIM_GUARD=0` disables).
- Local call cap: `run_retail.sh` passes `--max-steps-seconds 600` (10 min of call = 3000 ticks; `MAX_CALL_SECONDS` env; `--max-steps` is the text-mode message cap and is ignored by audio-native runs) for STACK=local — a local agent that has not acted in 10 min never does, and Ollama is saturated by the user simulator's `regular`-mode decision calls (~480/h), so concurrency > 1 does not help. Applied uniformly to all prompt versions. Baseline restarted 16:52 under this cap (first attempt's tasks 4 and 6 kept in `…_v0_partial_6000cap`).
- **Iteration subset cut to 10 tasks (user, 2026-09-29 21:55)**: `evals/subsets/retail_iter10.json` = 4, 6, 7, 8, 14, 16, 19, 22, 23, 24 (first ten of iter30 in numeric run order). `run_retail.sh subset` uses it by default (`SUBSET=30` for the old list). The v0 baseline run is stopped automatically after its 10th task; results dir stays `retail_subset_local_v0_tau_cascaded_v0`. Reason: the laptop sleeps whenever the lid is closed, so 30 tasks (~10 h awake) was not going to complete.
- **v0 baseline done (2026-09-30 12:35, 10 tasks, `regular`)**: pass^1 **0/10** (all hit the 600 s cap), 4 tool calls in 10 calls, 0 authentications; panel L_R 4.74 · R_R 89 % · R_Y 90 % · I_A 0.13 · **S_BC 0.32** · S_VT 0.39 · S_ND 0.72; b1 auth 0, neverRec 0.79, spellReq 0.70. Dominant failure = backchannels: whisper renders "uh-huh"/"mm-hmm" as "Amen."/"Okay."/"and mem him", v1-mini ends the turn, the agent answers each one ("clarify what you meant by 'Amen'?") and re-asks in full; plus order-id fixation instead of authentication and 30-s replies. Full catalogue: `runs/analysis/v0_findings.md`; timelines `runs/analysis/subset_v0_timeline.txt`.
- Prompt versions: v1 = `v1_backchannel_concise.md` (behaviour 2 + brevity), v2 = `v2_identifier_readback.md` (behaviour 1, renamed from the earlier v1 draft), v3 (to draft) = authenticate-first. Iterate on the 10-task subset (`scripts/run_retail.sh subset agent/prompts/<file> <name>`), ~3 h awake each; compare with `evals/report.py <run> --baseline retail_subset_local_v0_tau_cascaded_v0`.
- Text pre-screens (`evals/text/test_b2_backchannel.py` new, 5 backchannel cases): v0 2/5, v1 draft 3/5, **v1 revised 5/5** (minimal-acknowledgement wording: a user turn always yields a reply, so the prompt must name the reply — "Take your time." — rather than say "do not reply"). v2 on the b1 pre-screen 2/3 (still passes "yusuf dot rossi at example dot com" literally once). **v1 subset run launched 2026-09-30 13:0x** (`retail_subset_local_v1_backchannel_concise_v1`, ~3 h awake).
- Harness defect fixed 2026-09-30: a local user-sim generation ran away (8.9k tokens, 23 min stall in the first v1 attempt); `llm_utils.generate` caps local user-sim turns at 400 tokens and decision calls at 32 (`TAU2_LOCAL_USERSIM_MAX_TOKENS`). v1 relaunched 13:45.
- **v1 result (2026-09-30 16:08)**: pass 0/10 (= v0); reply tokens p50 74→41, cancelled generations 58→26, false interruptions 16→9, tool calls 4→13 (12 errored: the *user simulator* invented `user_id=12345`); τ-bench S_BC 0.32→0.26 — **the audio-level yield (VAD interruption on a 0.5 s "mm-hmm") is not prompt-addressable**; whisper renders backchannels as words ("Go, sir."), v1-mini ends the turn, LiveKit always replies. Brevity kept. `runs/analysis/v1_findings.md`.
- Next: (a) config ablation preset `local-bc` (= local + `min_interruption_words=2`, `min_interruption_duration=1.0`) with the v1 prompt on the 10 tasks → bounds reachable S_BC; (b) v3 `v3_authenticate_first.md` (= v1 + authenticate via email/name+zip before anything, never ask for order/item/user ids).
- v3 pre-screen iterations (2026-09-30): first draft still opened with "provide your order ID" (2/3) → explicit first-question rule; then it narrated "let me find your order" without calling any tool (0 lookups in 3/3) → "call the lookup tool in that same turn; saying 'one moment' does nothing" rule; final: b1 2/3 with real lookups, b2 4/5. **v3 subset run launched 17:1x** (`retail_subset_local_v3_authenticate_first_v3`). Config ablation `local-bc` preset (min_interruption_words=2, min_interruption_duration=1.0) is ready to run after it (`PRESET=local-bc scripts/run_retail.sh subset agent/prompts/v1_backchannel_concise.md v1bc`).
- **v3 result (2026-10-01 11:32)**: 0/10; lookups 1→3→8 across v0/v1/v3 (behaviour 1 now exercised in voice) but 0 authentications: whisper hears "May" for "Mei" (STT), spelled "S-O-F-I-A" passed as `first_name="S.O.F.I."` (LLM reconstruction), zip truncated by a turn split, over-spelling loop on clearly-heard values, unbacked claims 2→6→9. `runs/analysis/v3_findings.md`.
- v4 first draft (667 words, three stacked sections) regressed on the pre-screen to 0/3 with no tool calls and a Chinese reply → **prompt length is itself a failure mode for the 7B model**; v4 rewritten as one ~170-word block. Ollama keep-alive pinned to 24 h in `local_stack.sh` (model unload between runs + 20 s reload tripped the 10 s text-session timeout; text sessions now use the preset's 40 s).
- **Baseline subset in flight (2026-09-29 14:50)**: `retail_subset_local_v0_tau_cascaded_v0` — 30 tasks, `regular`, qwen2.5:7b, prompt v0, guard on; ≈ 10 h. This is the v0 reference for prompt iteration on the local stack (pass^1 + b1/b2/b3 + `json_spoken`/`placeholder_arg_calls`); turn-taking numbers are local-only.
- b1 scorer refined (2026-09-29): tool arguments compared *strictly* per kind (email: lowercase/no whitespace; zip: digits; name: alnum) while transcripts are compared loosely with spoken punctuation words ("at", "dot") removed; wrong values are attributed to `used_wrong_heard_ok` (transcript had the value → LLM reconstruction failure, e.g. "Mia. Garcia2723@example, com") vs `used_wrong_misheard` (STT never produced it); only caller-spoken kinds (name/zip/email) count, order/user ids are reported separately as `grounding_status`; kinds listed in the scenario's `unknown_info` are dropped from gold. Columns: recall · neverRec · notUsed · wrongLLM · wrongSTT · spellReq.
- Prompt v1 drafted: `agent/prompts/v1_identifier_readback.md` (behaviour-1 hypothesis: rebuild spelled values, read the rebuilt value back and wait for confirmation before any lookup, never placeholders, re-spell instead of retrying). Not run yet — pre-screen it first (`TAU2_AGENT_PROMPT_FILE=agent/prompts/v1_identifier_readback.md`), then the subset.
- `run_retail.sh` now resolves the prompt path to absolute (a relative path silently killed all 30 subset tasks once).
- Text pre-screen (keyless, llama3.1:8b, prompt v0): 0/3 cases pass in ~2 min — writes before auth with hallucinated order ids, `zip="19222"` for "nineteen one twenty two". It reproduces the voice-run failures without audio, so prompt candidates can be screened there first.
- `evals/inspect_run.py <run> [--task N]` prints the per-simulation timeline (user speech, agent speech, tool calls with args and error flags, speech-tick shares) — the first step of failure mining; `--json` writes a summary.
- Text pre-screen now keyless: `evals/text/test_b1_spelling.py` and `agent/retail_agent.build_text_session` take the LLM from the session preset (`TAU2_SESSION_PRESET`, default `local` → Ollama, via `session_provider.build_llm`); judge = `TAU2_JUDGE_MODEL` on the same endpoint. Skips itself when the endpoint is unreachable. Don't run it while a τ-bench run is using Ollama (it perturbs latency).
- Process hygiene: macOS has no `setsid`; `scripts/daemonize.py <log> <cmd…>` starts a command with `start_new_session=True`. `local_stack.sh` uses it for Ollama/STT/TTS, and long τ-bench runs should be launched the same way (`STACK=local python3 scripts/daemonize.py runs/local_stack/<name>.log scripts/run_retail.sh smoke`) so killing a Claude Code background task cannot take them down (this killed runs 2/7 and the servers twice).

### Verified / not yet verified (2026-09-27)
- Verified: provider registered in factory + CLI; config resolution; pure sink tests (framing, pacing, playback-finished, interrupt, pause/resume, text segments); eval scripts compile; retail agent factory wraps 16 tools.
- Not verified (no API keys on this machine): end-to-end tick loop with real Deepgram/OpenAI, ElevenLabs persona creation, baseline run, metric scripts on a real run. First real run should be `scripts/run_retail.sh smoke`.

## Source index (docs.livekit.io, rendered 2026-09-27)

Get started: `/agents`, `/agents/start/voice-ai`, `/agents/start/builder`, `/agents/start/embed`, `/agents/start/prompting`, `/agents/start/telephony`, `/agents/start/frontend`
Multimodality: `/agents/multimodality`, `/agents/multimodality/audio`, `/agents/multimodality/audio/customization`, `/agents/multimodality/audio/background-audio`, `/agents/multimodality/audio/wakeword`, `/agents/multimodality/text`, `/agents/multimodality/instructions`, `/agents/multimodality/vision`, `/agents/multimodality/vision/images`, `/agents/multimodality/vision/video`
Logic: `/agents/logic`, `/agents/logic/sessions`, `/agents/logic/chat-context`, `/agents/logic/tasks`, `/agents/logic/workflows`, `/agents/logic/patterns`, `/agents/logic/patterns/supervisor`, `/agents/logic/patterns/subagent-delegation`, `/agents/logic/tools`, `/agents/logic/tools/definition`, `/agents/logic/tools/toolsets`, `/agents/logic/tools/async`, `/agents/logic/tools/mcp`, `/agents/logic/tools/forwarding`, `/agents/logic/tools/design`, `/agents/logic/nodes`, `/agents/logic/turns`, `/agents/logic/turns/turn-detector`, `/agents/logic/turns/adaptive-interruption-handling`, `/agents/logic/turns/vad`, `/agents/logic/turns/tuning`, `/agents/logic/agents-handoffs`, `/agents/logic/external-data`, `/agents/logic/fallback-strategies`
Prebuilt: `/agents/prebuilt/tasks`, `/agents/prebuilt/tasks/warm-transfer`, `/agents/prebuilt/tasks/get-credit-card`, `/agents/prebuilt/tools`
Server: `/agents/server`, `/agents/server/startup-modes`, `/agents/server/lifecycle`, `/agents/server/agent-dispatch`, `/agents/server/job`, `/agents/server/options`
Models: `/agents/models`, `/agents/models/pipelines`, `/agents/models/inference`, `/agents/models/llm`, `/agents/models/llm/anthropic`, `/agents/models/llm/openai`, `/agents/models/stt`, `/agents/models/stt/keyterms`, `/agents/models/tts`, `/agents/models/tts/custom-voices`, `/agents/models/tts/expressive`, `/agents/models/realtime`, `/agents/models/realtime/plugins/openai`, `/agents/models/realtime/plugins/gemini`, `/agents/models/avatar`
Reference: `/reference/agents/turn-handling-options`, `/reference/agents/events`, `/reference/agents/inference-llm-parameters`, `/reference/developer-tools/livekit-cli/agent`
Test & Evaluate: `/testing`, `/testing/overview`, `/testing/agent-console`, `/testing/debugger`, `/testing/unit-tests`, `/testing/simulations`, `/testing/simulations/write-a-scenario`, `/testing/simulations/scenario-sets`, `/testing/simulations/derive-a-scenario`, `/testing/simulations/ci`, `/testing/observability`, `/testing/observability/insights`, `/testing/observability/pii-redaction`, `/testing/observability/build`, `/testing/observability/tracing`, `/testing/observability/data`, `/testing/observability/recording`, `/telephony/testing`
External: github.com/livekit/agents (examples: `frontdesk`, `hotel_receptionist`, `voice_agents/otel_trace.py`), github.com/livekit/eot-bench, huggingface.co/livekit (eot-evals), github.com/livekit/agent-skills
Not yet extracted (per-provider plugin pages, deploy section, telephony section beyond testing): 130+ pages listed in `/agents/llms.txt` — pull on demand.

τ-bench (fetched 2026-09-27): taubench.com (+ `/blog/tau3-task-fixes.html`, `/blog/tau-knowledge.html`, `/blog/tau-voice-examples.html`), leaderboard data `https://sierra-tau-bench-public.s3.us-west-2.amazonaws.com/submissions/manifest.json` → `<id>/submission.json`, github.com/sierra-research/tau2-bench (README, `docs/evaluation.md`, `docs/interaction-metrics.md`, `docs/leaderboard-submission.md`, `docs/voice-personas.md`, `docs/running_simulations.md`, `src/tau2/{voice,domains,knowledge,agent}/README.md`, `data/tau2/domains/*/tasks*.json`), arXiv 2603.13686 (τ-Voice), 2603.04370 (τ-Knowledge), 2506.07982 (τ²), 2406.12045 (τ), sierra.ai blog posts, sierra-research.github.io/hyper-tau-bench.
