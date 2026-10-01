# Extraction notes 2 — AgentSession, chat context, workflows, tools, pipeline nodes

Sources: agents/logic/{sessions, chat-context, tasks, workflows, patterns/*, tools/*, nodes, agents-handoffs}, agents/prebuilt/* (21 pages, rendered 2026-09-27).

## 1. AgentSession

Phases: Initializing (state `initializing`) → Starting (`start()` sets up I/O; → `listening`) → Running (`listening`/`thinking`/`speaking`) → Closing (drain pending speech, commit transcripts, close I/O; emits `close`).

Events (sessions doc): `agent_state_changed`, `user_state_changed` (`speaking`/`listening`/`away`), `user_input_transcribed`, `user_transcription_timeout` (`speech_duration`), `conversation_item_added`, `close`. Also `speech_created` (once per `SpeechHandle`).

Constructor options and defaults:
- `stt`, `llm`, `tts`, `vad`; overridable per agent/task.
- `turn_handling` (`TurnHandlingOptions`); `preemptive_generation` default enabled ("increases LLM token usage").
- `tools` shared by all agents; `mcp_servers` deprecated (use `MCPToolset`).
- `max_tool_steps` default **3**; final no-tool reply on limit (≥ 1.4.5).
- `ivr_detection` default `False`.
- `user_away_timeout` default `15.0` s; `None` disables.
- `transcription_timeout` default `None`; needs VAD+STT.
- `min_consecutive_speech_delay` default `0.0`.
- `tts_text_transforms`: `"filter_markdown"`, `"filter_emoji"`, custom; `None` disables; all built-ins applied by default.
- `use_tts_aligned_transcript` default off.
- `video_sampler` (Python) default `VoiceActivityVideoSampler` `speaking_fps 1.0`, `silent_fps 0.3`.
- `userdata`; `tool_handling` (`async_options` templates).

`rtc_session` options: `agent_name`, `type`, `on_session_end`, `on_request`.

RoomIO / `RoomOptions`: `text_input`, `audio_input`, `video_input` (off by default), `text_output`, `audio_output` (on); text input interrupts and replies by default; `AudioInputOptions` (noise cancellation, AGC Python, pre-connect Python); `participant_kinds` default `[SIP, STANDARD]`; `participant_identity` default first-to-join, `session.room_io.linked_participant`, `RoomIO.set_participant()`; `close_on_disconnect` for `CLIENT_INITIATED`, `ROOM_DELETED`, `USER_REJECTED`; `delete_room_on_close` default `False`.

Speech control: `say()` (bypasses LLM), `generate_reply(instructions=, chat_ctx=)` (chat_ctx replaces context for that reply only); `add_to_chat_ctx=False`; `disallow_interruptions()`; `session.shutdown()` / `ctx.shutdown()`; inactive-user pattern via `away` state.

## 2. Chat context

`ChatContext` = history sent to the LLM each turn. Each agent/task has its own; **new agent/task starts empty by default**. `session.history` = full cross-agent record.

Item types: `message` (system/user/assistant), `function_call`, `function_call_output`, `agent_handoff` (`old_agent_id`, `new_agent_id`), `agent_config_update` (`instructions`, `tools_added`, `tools_removed`).

Ops: `add_message`, `copy()` (filters `exclude_instructions`, `exclude_function_call`, `exclude_handoff`, `exclude_empty_message`, `exclude_config_update`), `truncate(max_items=n)` (preserves system instructions; strips leading orphaned function calls), `merge(other, exclude_function_call=)`, `insert()`, `get_by_id()`.

Patterns: `Agent(chat_ctx=initial_ctx)`; per-turn injection in `on_user_turn_completed` (turn-only unless `update_chat_ctx()`); handoff `NextAgent(chat_ctx=self.chat_ctx.copy(exclude_instructions=True))`; summarization via separate LLM (`item.extra["is_summary"]`); `truncate(max_items=6)`.

## 3. Workflows

Selection: start with single agent + tools; split on instruction bloat, conflicting tool access, multi-turn data collection, backtracking. Comparison: single agent (manual correction), supervisor (scoped copy; minimal latency; re-run task), subagent delegation (no turn latency; result later), handoffs ("Handoff overhead per transition"; manual hand back), task groups (built-in regression). Best practices: separate agents for distinct reasoning/tool access; tasks for must-complete-first ops; meaningful return values; plan context continuity; build incrementally with tests/evals/simulations; "announce handoffs, preserve relevant context to avoid repetition, and handle correction paths cleanly."

Agents & handoffs: `session.update_agent()` or return agent from tool (`(Agent, result)` / bare `Agent` / `llm.handoff({agent, returns})`); tool call + reply complete before handoff; `AgentHandoff` item added; **context doesn't carry over unless `chat_ctx` passed**; `on_enter` after activation, `on_exit` before leaving; `userdata` on `AgentSession[T]`; per-agent model overrides; `Agent.update_options()` (realtime models can't be replaced on active agent → `RuntimeError`).

Tasks: `AgentTask[T]` with `instructions`, `chat_ctx`, tools; `complete(result)`; only awaitable from `on_enter`, `on_exit`, or a tool body (else `RuntimeError`); empty context by default. `TaskGroup` (experimental): `summarize_chat_ctx` default `true`, `chat_ctx`, `return_exceptions` default `false`, `on_task_completed(TaskCompletedEvent: agent_task, task_id, result)`; `add(factory, id, description)`; `TaskGroupResult.task_results`; regression via internal exception; don't `session.shutdown()` inside `on_task_completed`.

Testing task groups: Python `TaskGroup` sets `llm=None` during transitions → add `asyncio.sleep(0.5)` after `session.start()`; raw JSON args → prefer `contains_function_call`; init `userdata`; **startup output not in `RunResult`**; don't await `generate_reply` playout in tool-triggered `on_enter`; LLM may need multiple turns → `containsFunctionCall()` + generous timeouts; Node cleanup timeout 30000 ms; `get_job_context()` raises in tests.

Prebuilt tasks (Python beta `livekit.agents.beta.workflows`): `GetNameTask`, `GetEmailTask`, `GetAddressTask`, `GetDOBTask`, `GetPhoneNumberTask`, `GetCreditCardTask`, `GetDtmfTask`, `WarmTransferTask`; `extra_instructions`, `tools`.
- `GetCreditCardTask`: TaskGroup of name → card number → security code (3–4 digits) → expiration; validates format, detects issuer (Visa, Mastercard, Amex, Discover), rejects expired; `restart_card_collection`; "Sensitive information is never repeated back to the user during audio sessions." Returns `cardholder_name`, `issuer`, `card_number`, `security_code`, `expiration_date`. `require_confirmation` audio `True` / text `False`.
- `WarmTransferTask` (Python + Node): separate room, SIP dial to human, hold music, context to human; human tools `connect_to_caller`, `decline_transfer`, `voicemail_detected`; params `sip_call_to`, `sip_trunk_id` (env `LIVEKIT_SIP_OUTBOUND_TRUNK`) or `sip_connection`, `sip_number`, `sip_headers`, `dtmf` (`w` ≈ 0.5 s pause), `ringing_timeout` (→ `ToolError`, conversation resumes), `hold_audio` default `BuiltinAudioClip.HOLD_MUSIC`, `extra_instructions`, `tools`.

Prebuilt tools: `EndCallTool` (`end_instructions`, optional room delete), `send_dtmf_events`.

Supervisor: long-lived agent routes to `AgentTask` specialists via tools; waits for typed result. "If the model needs to ask clarifying questions, the work belongs in a task. If it's a single function call with arguments, it belongs in a tool." Name each specialist tool explicitly. "Treat task results as untrusted input until validated." Test supervisor and tasks independently, then simulate end-to-end.

Subagent delegation: primary fast model; subagent separate `LLM` + `ChatContext` via `chat()`; async tool calls `ctx.update()` first. Flags `ToolFlag.CANCELLABLE`, `on_duplicate="reject"`, `duplicate_scope="name_and_args"`. Override `reply_maybe_covered_template` (can swallow results; "Agent instructions don't override the template"). Blocking variant: filler may be treated as a user turn by latency-optimized models. Handoffs drop pending updates unless `AsyncToolset`. "Varies between models and between runs" → simulations covering delegated, interrupted, blocking paths.

## 4. Tools

Definition: `@function_tool` (`name`, `description`, `raw_schema`, `flags` default `ToolFlag.NONE`); Node `llm.tool({name, description, parameters: z.object, execute, flags})` (prefer `.nullable()` over `.optional()` for OpenAI strict). `RunContext`: `session`, `function_call`, `speech_handle`, `userdata`. Flags: `NONE`, `IGNORE_ON_ENTER`, `CANCELLABLE`. Names unique; `update_tools()` replaces all; `Agent(tools=[...])` merges with decorated methods. Provider tools: Anthropic `ComputerUse`, Gemini, Mistral, OpenAI, SpaceXAI.

Return semantics: stringified → LLM; `None` → silent; `Agent` → handoff. Errors: `ToolError(message)` → LLM; unexpected exceptions → generic error (logged); validation failures → `ToolError` with validator message.

Interruptions: interruptible by default; work not cancelled; finished-after-interruption → call+result in history, no reply; **interrupted handoff never takes effect**; `speech_handle.wait_if_not_interrupted()`/`.interrupted` (Python), `abortSignal` (Node); `disallow_interruptions()`; `ctx.wait_for_playout()`.

HTTP: reuse `utils.http_context.http_session()`; disable interruptions for mutations; `ToolError`; env creds; timeouts.

Toolsets: `Toolset(id, tools)`; `setup()`/`aclose()`; `ToolSearchToolset` / `ToolProxyToolset` (BM25, `KeywordSearchStrategy`, custom).

Async tools: non-blocking on first `await ctx.update(msg)`; `ctx.with_filler(source, delay=0, interval=None, max_steps=None)`; `ctx.foreground()`; cancellation opt-in (`lk_agents_get_running_tasks`, `lk_agents_cancel_task`); `on_duplicate`: `allow` (default), `reject`, `replace`, `confirm`; pending updates dropped on handoff unless `AsyncToolset` in `AgentSession(tools=...)`; templates `update_template`, `duplicate_reject_template`, `duplicate_confirm_template`, `reply_at_tail_template`, `reply_maybe_covered_template`.

MCP (Python): `mcp.MCPToolset(id, mcp_server=MCPServerHTTP|MCPServerStdio)`; auto transport (`/mcp` streamable, `/sse`); `headers`; `allowed_tools` / `filter_tools`; `tool_result_resolver`; `MCPToolOptions(flags, on_duplicate, report_progress)`; `client_session_timeout_seconds`; failed connect logged, doesn't block; agent `tools` replace session tools.

Frontend forwarding: `room.local_participant.perform_rpc(destination_identity, method, payload, response_timeout)`; wrap failure in `ToolError`.

Tool loop design: 5–10 tools; ">10 incorrect selections become more common, and past 20, the model often struggles"; consolidate; actions not endpoints; namespace; descriptions state what/when/when-not; pin valid values in prose; speech-ready returns; semantic IDs; `max_tool_steps` 3; disable parallel tool calls when chaining; self-reporting params (`read_back: bool`); timeouts (hanging tool blocks session close); prefer `update_instructions`/`update_tools` over handoff for config changes. Diagnostics: redundant calls → return shape; invalid args → param descriptions; wrong tool → overlapping descriptions.

## 5. Pipeline nodes and hooks

`on_enter()`, `on_exit()`, `on_user_turn_completed(turn_ctx, new_message)` (raise `StopResponse()` to abort; realtime needs agent-side turn detection; fast pre-response pattern), `on_user_turn_exceeded(ev)` (`transcript`, `accumulated_transcript`, `accumulated_word_count`, `duration`), `stt_node`, `llm_node` (`FlushSentinel`; per-segment playout since Python 1.6.0 / Node 1.4.6), `tts_node`, `realtime_audio_output_node`, `transcription_node` (`TimedString`).

## 6. Notes / warnings (consolidated)

preemptive generation token cost; `max_tool_steps` behavior change 1.4.5; `transcription_timeout` needs VAD+STT, AEC warmup can suppress transcripts; Python-only features list; empty context on new agent/task; `truncate()` semantics; task await sites; `get_job_context()` in tests; `TaskGroup` experimental; no shutdown in `on_task_completed`; early-exit needs `return_exceptions=False`; testing caveats; Node has no prebuilt tasks; realtime model swap `RuntimeError`; tool name uniqueness; interrupted handoff; explicit cancel on interruption (Python); async updates dropped on handoff; cancellation opt-in ("most tools aren't safe to interrupt"); `reply_maybe_covered_template`; filler as user turn; `mcp_servers` deprecated; MCP resolver only on success; agent tools replace session tools; unexpected exceptions masked; tool count guidance; hanging tools block close; `FlushSentinel` change; `on_user_turn_completed` with realtime; `on_user_turn_exceeded` skipped if speaking; `require_confirmation` audio/text; card data never read back; `ringing_timeout` → `ToolError`; supervisor validation.

## 7. Eval relevance

Observable: session events; `session.history` items incl. raw JSON args, handoffs, config updates, `item.extra`; `RunResult` API; `TaskGroupResult`; `CollectedResponse.usage`. Failure modes: wrong tool / invalid args / redundant calls / exceeding `max_tool_steps` / premature completion tools; silent turns; async results never delivered / dropped on handoff; filler misread; half-completed writes on interruption; interrupted handoffs; unannounced switches; context loss on handoff; typed-but-invalid task results; premature shutdown. Quality: correct routing with correct args on real data; confirmation before irreversible actions; no sensitive echo in audio; speech-ready concise returns; result delivery; continuity; correction paths; run-to-run robustness.
