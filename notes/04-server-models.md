# Extraction notes 4 — Agent server, models, LiveKit Inference, realtime, avatars

Sources: agents/server/**, agents/models/** (overview, pipelines, inference, llm, stt, keyterms, tts, custom-voices, expressive, realtime, avatar, llm/anthropic, llm/openai, realtime/plugins/{openai,gemini}), reference/agents/inference-llm-parameters (22 pages, rendered 2026-09-27).

## 1. Agent server

Model: agent server registers with LiveKit, waits for job requests; LiveKit routes each dispatch to an available server; first available accepts and starts a process. Lifecycle: registration → job request (explicit API or automatic) → entrypoint runs → session close (room closes when last non-agent participant leaves). Load balancing via availability exchange; one process per job; drain on deploy; crash detected "within approximately 15 seconds" → re-dispatch. Any programmatic participant can use the entrypoint.

Startup modes: `start` (prod; `info` logs; drain on SIGINT/SIGTERM; prewarmed processes; JSON stdout), `dev` (`debug`; auto-reload; "No graceful shutdown drain period"), `console` (`--text`, `--input-device`, `--output-device`, `--list-devices`, `--record` → `console-recordings/session-<ts>/`, `Ctrl+T` toggles), `connect` (`--room`, `--participant-identity`). Auth via `LIVEKIT_URL/API_KEY/API_SECRET`; `lk app env -w`. Log levels Python `trace…critical`, Node `trace…fatal`.

Dispatch: "hundreds of thousands of new connections per second with a max dispatch time under 150 ms". `agent_name` (dispatch name) ≠ display name. Explicit: `AgentDispatchService` / `CreateAgentDispatchRequest(agent_name, room, metadata, deployment)`; `lk dispatch create`; token `RoomAgentDispatch` in `RoomConfiguration.agents`; SIP dispatch rules. `LIVEKIT_AGENT_DEPLOYMENT` (production = empty). Metadata ≤ 512 KiB. Automatic dispatch (no name) cautioned against.

Job lifecycle: separate process per job; runs until all standard/SIP participants leave or explicit shutdown. Entrypoint `@server.rtc_session()`; `ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)`; `single_peer_connection` (Python). Participant entrypoints (`add_participant_entrypoint`, `kind` filter). `ctx.log_context_fields`. Inputs: job metadata, room metadata, participant attributes; `ctx.wait_for_participant()`. Ending: `session.shutdown(drain=True)` vs `await session.aclose()`; `delete_room_on_close=True` / `ctx.delete_room()`; `ctx.add_shutdown_callback(fn)` (10 s default, `shutdown_process_timeout`).

Server options: `permissions` (`WorkerPermissions`), `drain_timeout` (one hour), `load_fnc` (avg CPU over 5 s), `load_threshold` `0.7`, `setup_fnc`/`prewarm` (`proc.userdata`), `num_idle_processes` (Python `ceil(cpu_count)`, Node `min(availableParallelism, 4)`; dev = 0), `log_level` `info` (`LIVEKIT_LOG_LEVEL` / `LOG_LEVEL`; CLI > env > code), `host`/`port` health check `0.0.0.0:8081` (`200`/`503`), `on_request`/`requestFunc` (`req.accept(name, identity="agent-<jobid>", attributes)`), `type` `ROOM`|`PUBLISHER`, `shutdown_process_timeout` 10 s.

## 2. Pipeline types, Inference, plugins

Pipeline comparison (STT-LLM-TTS / Realtime / Half-cascade): latency Moderate/Fastest/Moderate; tool calling Mature/Less mature/Less mature; realtime transcription Yes/Delayed/Delayed; `say()` Yes/No/Yes; prosody-aware No/Yes/Yes; expressive output Depends on TTS/Built-in/Depends on TTS; auditability Full/Limited/Output text only. "For most production agents, an STT-LLM-TTS pipeline is the right default." Realtime "when latency or expressive output matter more than fine-grained control"; recommended GPT-Live. Half-cascade = realtime text-only + TTS. Latency target "under one second".

LiveKit Inference: included in Cloud; no keys; ZDR by default for all models; providers OpenAI, Google, AssemblyAI, Deepgram, Cartesia, Fish Audio, Inworld, more. `inference.STT/LLM/TTS` or descriptors `provider/model[:lang|voice]`; `auto:<lang>`. Region restriction (off by default; on for new EU projects). Usage-based billing.

Active models (per docs tables):
- LLM: `deepseek-ai/deepseek-v4.1-flash`; `google/gemini-3-flash-preview`, `gemini-3.1-flash-lite`, `gemini-3.1-pro-preview`, `gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-3.6-flash`, `gemini-3.7-flash`, `gemini-3.8-flash`; `google/gemma-4-31b-it`; `openai/chat-latest`, `gpt-4.1`(-mini/-nano), `gpt-4o`(-mini), `gpt-5`(-mini/-nano), `gpt-5.1`, `gpt-5.2`, `gpt-5.4`(-mini/-nano), `gpt-5.5`, `gpt-5.6-luna/sol/terra`, `gpt-oss-120b`; `xai/grok-4.20-0309-non-reasoning`, `grok-4.20-0309-reasoning`, `grok-4.20-multi-agent-0309`, `grok-4.3`, `grok-4.5`, `grok-4.6`, `grok-4.7`; `moonshotai/kimi-k2.6` (deprecated).
- STT: `deepgram/flux-general-en`, `flux-general-multi`, `nova-3`, `nova-3-medical`, `nova-3-pharma`, `nova-2`, `nova-2-conversationalai`, `nova-2-medical`, `nova-2-phonecall`; `assemblyai/universal-3-5-pro`, `universal-3-6-pro`, `universal-streaming`, `universal-streaming-multilingual`; `cartesia/ink-2`, `ink-whisper`; `google/gemini-3.5-transcribe-live`; `speechmatics/linden-1`; `xai/stt-1`, `stt-2`.
- TTS: `cartesia/sonic-3`, `sonic-3-2026-01-12`, `sonic-3-latest`, `sonic-3.5`, `sonic-3.5-2026-05-04`, `sonic-3.6`, `sonic-3.6-2026-08-27`, `sonic-latest`, `sonic-preview`; `deepgram/aura-2`, `flux-tts`; `fishaudio/s2-pro`, `s2.1-pro`; `gradium/default`; `inworld/inworld-tts-1.5-max`, `-1.5-mini`, `-2`, `-2-flash`; `rime/coda`, `mistv3`; `xai/tts-1`.

Plugins: one per provider; `uv add "livekit-agents[openai]~=1.8"`; `pnpm add "@livekit/agents-plugin-openai@1.x"`; OpenAI-compatible via `base_url`/`api_key`; mixable and swappable mid-session.

## 3. LLM

`llm.chat(chat_ctx, tools)` → `ChatChunk` stream; `LLMStream.collect()` → `CollectedResponse` (`text`, `tool_calls`, `usage`, `extra`); `llm.execute_function_call`. Recommended default `google/gemma-4-31b-it` ("latency-optimized, open-weight"). `extra_kwargs`/`modelOptions`; `session.llm.update_options(...)` replaces (not merges).

Inference LLM params: `temperature` (1; not with `top_p`; not for reasoning; deprecated for Gemini 3), `top_p` (1), `max_tokens` (unsupported on newer), `max_completion_tokens`, `reasoning_effort` (`low|medium|high`), `frequency_penalty` (0), `presence_penalty` (0), `seed`, `stop`, `n`, `logprobs`, `top_logprobs`, `logit_bias`, `parallel_tool_calls`, `tool_choice` (`auto`), `user` (deprecated), `service_tier`, `metadata`, `store`, `prediction`, `modalities`, `web_search_options`, `verbosity`, `prompt_cache_key`, `safety_identifier`. Unsupported params silently ignored; reasoning-incompatible stripped.

OpenAI plugin: Responses API (`openai.responses.LLM()`, recommended; provider tools; lower cost; WebSocket `?model=`) vs Chat Completions (`openai.LLM()`, `with_fireworks/groq/perplexity/telnyx/together/x_ai/deepseek`). Defaults `gpt-4.1`, temp `0.8` (0–2), `tool_choice auto`, `timeout httpx.Timeout(connect=15.0, read=5.0, write=5.0, pool=5.0)`.

Anthropic plugin: `claude-sonnet-4-6`; `max_tokens 1024` (Node 4096); temp 1 (0–1); `parallel_tool_calls`; `tool_choice auto`; `timeout httpx.Timeout(5.0, read=30.0)`; `ComputerUse` (1280×720, display 1).

## 4. STT

Multilingual `deepgram/nova-3:multi`; `auto:<lang>`. `LanguageCode` normalization (`.language`, `.region`, `.iso`). Streaming: `stt.stream()`, `push_frame`, `end_input()`; events `FINAL_TRANSCRIPT`, `INTERIM_TRANSCRIPT`, `START_OF_SPEECH`, `END_OF_SPEECH`. Non-streaming: `StreamAdapter(stt, vad_stream)` with `inference.VAD(model="silero", min_speech_duration=0.1, min_silence_duration=0.5)`. Diarization `MultiSpeakerAdapter(stt, detect_primary_speaker=True, suppress_background_speaker=False, primary_format, background_format)`; providers AssemblyAI, Deepgram, NVIDIA Riva, Smallest AI, Speechmatics, Soniox, SpaceXAI; enable explicitly. Metadata: Inworld `voice_profile`, SpaceXAI `speech_final`.

Keyterms: `stt_context_options=STTContextOptions(keyterms=[...], keyterm_detection={enabled False, llm None (→ gemma-4-31b-it), turn_interval 1, max_keyterms None, instructions, timeout 10.0})`; static persist across handoffs; detection precision-focused; `session.keyterms`; `session.update_options(keyterms=[...])`; native mapping Deepgram `keyterm`, AssemblyAI `keyterms_prompt`, Speechmatics `additional_vocab`. Wrong keyterm "degrades recognition for the rest of the call with no recovery".

## 5. TTS

`inference.TTS(model, voice, language)`; `tts.stream()`; `SynthesizedAudio` with segment boundaries; custom via `tts_node`. Suggested voices e.g. `cartesia/sonic-3:9626c31c-bec5-4cca-baa8-f8ba9e84c8bc`, `deepgram/aura-2:athena`, `fishaudio/s2.1-pro:bf322df2096a46f18c579d0baa36f41d`, `rime/coda:astra`, `inworld/inworld-tts-1:Ashley`, `xai/tts-1:ara`.

Custom voices: Ship+; cloned to Cartesia, Inworld, Fish, Gradium; ~10 s sample ≤ 4 MB; 14 languages; `v_*` IDs; cross-provider fallback; samples deleted 12 months after last use.

Expressive mode: `expressive=True` (pipeline only; Fish `s2.1-pro`, Inworld `inworld-tts-2`, Cartesia Sonic, Gemini `gemini-3.8-flash-tts`/`-lite-tts`, SpaceXAI `tts-1`); injects markup guide, normalizes tags, batches sentences, strips tags from transcript; `ExpressiveOptions.speech_steering` {`disfluencies` on, `nonverbal_sounds` (laughing, breathing, sighing, crying, vocalizing, mouth_sounds, reflex_sounds), `pace`}, `tts_instructions_append/template`; frontend `useAgentExpression()` → `mood` (11 values), `lk.expression` attribute.

## 6. Realtime models

Plugins: Nova Sonic, Azure OpenAI Realtime, Gemini Live, PersonaPlex, GPT-Live, OpenAI Realtime, Phonic, SpaceXAI Grok Voice, Ultravox. Recommended `openai.realtime.GPTLiveModel(voice="marin")`. Full-duplex: model decides boundaries; "keeps talking until it stops on its own"; items arrive after audio; no verbatim scripts. Half-cascade `RealtimeModel(modalities=["text"])` + `tts`. Considerations: prefer built-in turn detection; LiveKit detector needs separate STT for text model; no interim transcripts; user transcripts after response; no `say()`; history text-only; OpenAI text-only drift after long history.

OpenAI Realtime: `gpt-realtime`, `marin`, temp 0.8 (0.6–1.2), `modalities ['text','audio']`, `reasoning`, semantic VAD default (`eagerness auto|low|medium|high`, `create_response`, `interrupt_response`) / server VAD (`threshold 0.5`, `prefix_padding_ms 300`, `silence_duration_ms 500`); video 1 fps speaking / 1 per 3 s.

Gemini Live: `gemini-2.5-flash` (3.1 = `gemini-3.1-flash-live-preview`), `Puck`, `modalities ["AUDIO"]`, `vertexai false`, `location us-central1`, `thinking_config`, `enable_affective_dialog false`, `proactivity false`, `media_resolution`; LiveKit detector via `automatic_activity_detection=disabled` + `TurnDetector()`; thoughts forwarded (`include_thoughts=False`); Gemini 3.1: no affective/proactive, no async function calling, `thinkingLevel` default `minimal`, plugin ≥ 1.8.2 / 1.9.0 for mid-session updates; separate TTS only with non-native-audio models.

## 7. Avatars

16 plugins. `AvatarSession` → `await avatar.start(session, room=ctx.room)` → `wait_for_join()` (30 s) → `session.start()` with audio output disabled; second participant (`lk.publish_on_behalf`); `DataStreamAudioOutput(wait_playback_start=True)`, `lk.playback_started` RPC; `AvatarMetrics` join latency + playback latency.

## 8. Notes / warnings

Console needs env creds; deployed containers lack `lk`; dev no drain; API keys as env vars; `load_fnc`/`load_threshold` fixed on Cloud; Silero bundled; health check for rolling deploys; sessions stateful ("should not be terminated abruptly"); log-level env quirks; automatic dispatch caution; token dispatch only at room creation; SIP explicit dispatch; metadata 512 KiB; Dockerfile assumptions; register participant entrypoints before connect; SIP `wait_for_participant`; shutdown hooks short; retired models inaccessible; OpenAI-compatible API mode; Responses gateway URL; Anthropic timeout/read; param compatibility rules; `update_options` replaces; keyterm degradation; diarizing STT for `MultiSpeakerAdapter`; custom voice caveats; expressive pipeline-only; realtime caveats; half-cascade modality support; Gemini 3.1 breaking changes; avatar join order; realtime tool calling "less mature".

## 9. Eval relevance

Latency statements: < 1 s e2e; dispatch < 150 ms; crash detection ≈ 15 s; avatar join/playback latency; `thinkingLevel minimal` for lowest latency; `silence_duration_ms` shorter = faster; Gemma 4 "latency-optimized"; `media_resolution`; `prediction`; `service_tier`. Cost/quality: model choice; Responses API cheaper; reasoning effort vs latency; `turn_interval`; `usage`. Provider variance: param support, keyterm params, diarization, metadata, markup dialects, EU endpoints, language coverage, clone rendition, text-only modality, Node vs Python, temperature ranges. Recommended defaults: `google/gemma-4-31b-it`; GPT-Live `marin`; `gpt-4.1`; `claude-sonnet-4-6`; `gpt-realtime`/`marin`; `gemini-2.5-flash`/`Puck`; `assemblyai/universal-3-5-pro:en` + `fishaudio/s2.1-pro`; `deepgram/nova-3:multi`. Determinism knobs: `seed` (best effort), `temperature`/`top_p`, `stop`, `logprobs`, `store` ("for model distillation or evals"), `metadata`, `update_options` for A/B; console `--record`; `MultiSpeakerAdapter` for multi-speaker audio.
