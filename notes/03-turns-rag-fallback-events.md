# Extraction notes 3 — Turn detection, interruptions, RAG, fallbacks, events

Sources: agents/logic/turns/**, agents/logic/external-data, agents/logic/fallback-strategies, reference/agents/turn-handling-options, reference/agents/events (9 pages, rendered 2026-09-27).

## 1. Turn detection and interruptions

`TurnHandlingOptions` keys: `turn_detection`, `endpointing`, `interruption`, `preemptive_generation`, `user_turn_limit`.

`turn_detection`: `TurnDetector()` (recommended, default), `"stt"`, `"vad"`, `"realtime_llm"`, `"manual"`. Default auto-selects realtime server-side detection when the LLM is a realtime model.

Audio turn detector (`inference.TurnDetector`): encodes audio directly (intonation, pitch, rhythm); example commits at 6.41 s vs text model early commits at 2.76 s / 4.88 s. Versions `v1` (Inference, every region, free on Cloud) and `v1-mini` (local CPU). Built into SDK 1.6.1+ (Python) / 1.4.7+ (Node). Params `version`, `unlikely_threshold` (float or per-language dict; lower = eager). Selection: Cloud → v1; local dev with Cloud creds → v1 (free monthly allowance, then mini); `start` elsewhere → mini. Endpointing defaults with audio detector `min_delay 0.3`, `max_delay 2.5`. Prediction timeout ≈ 1 s → commit anyway; v1 timeout → sticky fallback to mini (emits probability 1.0 for in-flight prediction, rescales thresholds). VAD `min_silence_duration ≥ 0.25` s or `ValueError`. Languages (14): en, ar, de, es, fr, hi, id, it, ja, ko, nl, pt, tr, zh; uses STT-reported language; no STT → English thresholds. Realtime: set model `turn_detection=None`. Compute-optimized instances (c6i/c7i) for mini. Benchmarks: `eot-bench`, `livekit/eot-evals`. STT EOT signals (Deepgram Flux) only used with `turn_detection="stt"`.

Text turn detector (`MultilingualModel`): deprecated (removal 2.0); needs STT; Qwen2.5-0.5B, 396 MB, ~50–160 ms; `livekit-agents[turn-detector]~=1.8`; 14 languages (has ru, lacks ar). TPR/TNR: hi 99.4/96.3, ko 99.3/94.5, fr 99.3/88.9, pt 99.4/87.4, id 99.3/89.4, ru 99.3/88.0, en 99.3/87.0, zh 99.3/86.6, ja 99.3/88.8, it 99.3/85.1, es 99.3/86.0, de 99.3/87.8, tr 99.3/87.3, nl 99.3/88.1.

Silero VAD `load()` defaults: `min_speech_duration 0.05`, `min_silence_duration 0.55`, `prefix_padding_duration 0.5`, `max_buffered_speech 60.0`, `activation_threshold 0.5`, `sample_rate 16000` (8000/16000), `force_cpu True`.

`EndpointingOptions`: `mode` `fixed`|`dynamic` (dynamic Python only; EMA of pauses), `min_delay 0.5` s (VAD mode ≈ `max(VAD silence, min_delay)`; STT mode applied after provider signal), `max_delay 3.0` s, `alpha 0.9`. Node ms. Runtime `session.update_options(endpointing_opts=...)`.

`InterruptionOptions`: `enabled True` (bool shorthand no longer supported), `mode` `adaptive`|`vad` (adaptive when turn detector + aligned-transcript STT, or realtime with server detection off), `discard_audio_if_uninterruptible True`, `min_duration 0.5` s, `min_words 0` (needs STT), `false_interruption_timeout 2.0` s (`None` disables), `resume_false_interruption True`, `backchannel_boundary (1.0, 1.0)` (Python). Interrupted speech truncates history to what was heard. `interrupt()` works even when disabled.

Realtime: `"realtime_llm"` → model handles; `InterruptionOptions` mostly ignored (`enabled` must stay `True`; `enabled=False` → `ValueError`). OpenAI server VAD `threshold`, `prefix_padding_ms`, `silence_duration_ms` (telephony example 0.7/300/400); semantic VAD `eagerness`, `interrupt_response`. Client-side turn-taking supported by OpenAI Realtime (incl. Azure), not Gemini Live.

Adaptive interruption handling: Cloud (unlimited) or dev mode (40,000 free requests/month); Python 1.5.0+ / Node 1.2.0+; acoustic barge-in model after VAD; filters backchannels, incidental sounds, noise; "might perform better with English"; auto-enabled when deployed/dev + VAD + (aligned-transcript STT or realtime with server detection off). Realtime gate all-or-nothing (turn dropped); STT gate drops only the backchannel portion. `backchannel_boundary` cooldowns: start 1.0 s (VAD interruption used instead), end 1.0 s (late transcripts counted as real turn). Aligned transcripts required (`stt.capabilities.aligned_transcript`). `InterruptionMetrics` per detection.

Manual turn control: `session.interrupt()`, `clear_user_turn()`, `commit_user_turn()` (Python returns `Future[str]`; `transcript_timeout`, `stt_flush_duration` 2.0; `skip_reply=True`); `set_audio_enabled`; push-to-talk RPC `start_turn`/`end_turn`/`cancel_turn`; `StopResponse` on empty turns.

User turn limit: `max_words`, `max_duration` (None); counters reset only when agent enters `speaking`; `on_user_turn_exceeded` default reply `allow_interruptions=False`, `tool_choice="none"`; skipped if agent already speaking.

Preemptive generation: `enabled True`, `preemptive_tts False`, `max_speech_duration 10.0` s, `max_retries 3`. "Doesn't always reduce latency."

Other: `min_consecutive_speech_delay 0.0` (Python); voice isolation (ai-coustics `QUAIL_VF_L`, Krisp BVC, `BVCTelephony`) and background noise suppression both off by default; recommended noisy config: `TurnDetector()`, fixed 0.5/3.0, adaptive, `min_duration 0.5`, `min_words 0`, `preemptive_tts False`, `QUAIL_VF_L`.

Troubleshooting matrix: cuts off → turn detector / raise `min_delay` / adaptive / voice isolation; short acks interrupt → adaptive / `min_words` / `min_duration`; slow → preemptive, `preemptive_tts`, lower `min_delay`, dynamic; partial-transcript replies → lower `max_speech_duration`/`max_retries`, don't return early from `on_user_turn_completed`; noisy misfires → isolation/suppression; no breath → `min_consecutive_speech_delay 0.2–0.4`. Tune with metrics, not feel.

## 2. External data & RAG

Initial context at job start (`ChatContext` + `add_message` + `Agent(chat_ctx=)` + `generate_reply(instructions=)`). Load-time: static data → prewarm; user data → job/room metadata or participant attributes; unavoidable network calls before `ctx.connect()`. Tool calls for precision/actions. `on_user_turn_completed` injection (fast; STT-LLM-TTS only; bounded by search accuracy). User feedback for ops > a few hundred ms, writes, failures: delayed `generate_reply` (0.5 s, cancel if done), cached TTS hold phrases, `BackgroundAudioPlayer(thinking_sound=[AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING, volume=0.8)])`, RPC popups (`response_timeout=500`). `AsyncToolset` automates progress delivery. Integrations: Letta, AgentMail, LlamaIndex, Mem0.

## 3. Fallback strategies

Trigger on any error (connection, timeout, 4xx/5xx, mid-stream); mark unhealthy, probe in background, restore; `AgentSession` emits `error`. Inference Fallback Adapter (STT/TTS; `fallback=[{"model": ...}]`; mid-stream failure restarts request from the beginning; custom voices fall back across providers). Agent Fallback Adapter (STT/TTS/LLM; `stt/llm/tts.FallbackAdapter([...])`; TTS no mid-utterance switch once audio played; LLM raises after chunks streamed unless `retry_on_chunk_sent=True`; emits `stt/llm/tts_availability_changed`). No retry counts/timeouts documented.

## 4. Events reference

- `user_input_transcribed`: `transcript`, `is_final`, `speaker_id`, `language`.
- `user_transcription_timeout`: `speech_duration`, `vad_speech_started_at`, `created_at`; off by default.
- `conversation_item_added`: `item` (`ChatMessage` with `metrics`, `role`, `text_content`, `interrupted`, `content`).
- `function_tools_executed`: `function_calls`, `function_call_outputs`, `has_tool_reply`, `has_agent_handoff`; `zipped()`, `cancel_tool_reply()`, `cancel_agent_handoff()`; `FunctionCallOutput.reply_required` (Python). Gemini honors `reply_required` only with `tool_behavior=NON_BLOCKING` (never on Vertex); Nova Sonic never.
- `session_usage_updated`: `usage.model_usage`.
- `metrics_collected` (session-level deprecated): `STTMetrics | LLMMetrics | TTSMetrics | VADMetrics | EOUMetrics | InterruptionMetrics`.
- `speech_created`: `user_initiated`, `source` (say / generate_reply / tool_response), `speech_handle`.
- `agent_state_changed`: `old_state`, `new_state` ∈ `initializing, idle, listening, thinking, speaking`; mirrored in `lk.agent.state` attribute.
- `user_state_changed`: `speaking`, `listening`, `away`.
- `overlapping_speech`: `type`, `detected_at`, `is_interruption`, `total_duration`, `prediction_duration`, `detection_delay`, `overlap_started_at`, `speech_input`, `probabilities`, `probability`, `num_requests`.
- `agent_false_interruption`: `type`, `resumed`, `created_at`.
- `close`: `error` (`LLMError | STTError | TTSError | RealtimeModelError | None`), `reason` (`error`, `job_shutdown`, `participant_disconnected`, `user_initiated`, `task_completed`).
- `error`: `error.recoverable`, `source`. `recoverable=False` closes session unless set `True` (safe for LLM/TTS/realtime; STT needs `session.update_agent(session.current_agent)`). Graceful exit: `say(..., allow_interruptions=False)` with pre-recorded `audio=`.
- `stt/llm/tts_availability_changed`.

## 5. Notes / warnings (consolidated)

Turn detector default; realtime `interruption.enabled=False` → `ValueError`; realtime only `enabled` + `discard_audio_if_uninterruptible` apply; Gemini Live no client-side turn-taking; VAD silence ≥ 0.25 s; ~1 s prediction timeout + sticky fallback; no STT → English thresholds; text detector deprecated; compute-optimized instances; Python s vs Node ms; STT-mode `min_delay` stacks; Python-only knobs; bool shorthand removed; `min_words` needs STT; `false_interruption_timeout=None`; adaptive constraints; realtime gate all-or-nothing; turn-edge misfilters; turn-limit semantics; preemptive caveats; don't return early from `on_user_turn_completed`; RAG hook STT-only; entrypoint calls before connect; feedback thresholds; fallback partial-output guards; `say()` needs audio when TTS down; STT recovery via `update_agent`; `user_transcription_timeout` off by default; session `metrics_collected` deprecated; reply suppression provider gaps; Node lacks `reply_required`; interrupted speech truncated in history.

## 6. Eval relevance

EOT quality: `eot-bench` methodology ("under production endpointing policies"), TPR/TNR axes, early-commit-on-pause failure mode. Latency signals: `ChatMessage.metrics`, `OverlappingSpeechEvent` (`detection_delay`), `InterruptionMetrics`, `user_transcription_timeout` fields, text detector 50–160 ms. State timelines from `agent_state_changed`/`user_state_changed`. Robustness: false interruptions (`resumed`), backchannel misclassification, `recoverable` errors, `CloseReason.error`, availability events, transcript timeouts, fallback restarts vs raised errors. Sweep axes: `turn_detection`, `unlikely_threshold`, endpointing (`min_delay`/`max_delay`/`mode`/`alpha`), interruption knobs, `backchannel_boundary`, preemptive options, noise cancellation, VAD thresholds. Stated goal: an agent that "listens while the user speaks and replies after they finish their thought."
