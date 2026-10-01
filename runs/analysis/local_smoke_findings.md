# Local-stack smoke runs — findings (2026-09-28)

Agent under test: LiveKit `AgentSession`, local cascaded pipeline (faster-whisper small.en → Ollama llama3.1:8b → Kokoro),
LiveKit v1-mini turn detector, VAD interruptions. τ-bench retail, tasks 6 and 14, `control` speech complexity,
user simulator = llama3.1:8b via LiteLLM, Kokoro persona voices. Prompt v0 (τ-bench cascaded instruction, verbatim).

Run: `external/tau2-bench/data/simulations/retail_smoke_local_v0_tau_cascaded_20260928_1400`

## Outcome
| task | reward | termination | duration | agent speech ticks | user speech ticks |
|---|---|---|---|---|---|
| 6 (exchange two items) | 0 | too_many_errors (10 tool errors) | 223 s | 3 / 661 | 479 / 661 |
| 14 (cancel order, refund) | 0 | too_many_errors | 357 s | 24 / 1094 | 730 / 1094 |

τ-bench panel (retail): L_R 3.05 s · L_Y 0.60 s · R_R 47 % · R_Y 100 % · I_A 0.29 (baseline row: 4.02 / 0.84 / 77 % / 99 % / 0.58).
Selectivity undefined (control mode has no backchannels/tics).

## Failure modes observed (verbatim from ticks) → behaviour mapping
1. **Identifier hallucination before lookup** (behaviour 6 / tool grounding): `exchange_delivered_order_items(order_id="#W0000000", item_ids=["1008292230", …])` at tick 127 before any user lookup; `cancel_pending_order(order_id="#W1234567")`; `find_user_id_by_name_zip("John","Doe","12345")` with values the caller never said.
2. **Spelled letters passed literally into tools** (behaviour 1): `find_user_id_by_name_zip(first_name="J, O, H, N", last_name="S, M, I, T, H", zip="12345")` — the prompt's own spelling example leaked into the arguments.
3. **Mis-reconstructed spelled email** (behaviour 1): caller spelled "M-I-A dot G-A-R-C-I-A…", agent called `find_user_id_by_email("mia.grcia@example.com")`.
4. **Speaking the tool call instead of making it** (behaviour 6): "To confirm the cancellation… we need to call the `cancel_pending_order` function. Here's a JSON for the function call: {…}" — read aloud by TTS, three times.
5. **Missing required argument**: `cancel_pending_order(reason="no longer needed")` without `order_id` → tool error.
6. **Latency death spiral** (behaviours 3/4, infra-driven): agent TTFT 8–9 s on cache-miss turns vs. the simulated user's 1.0 s wait → caller re-prompts ("Wait, what's going on? I don't think we're done yet…") and every late reply is treated as a barge-in and cleared; the caller ends up talking 67 % of the time. This is why agent speech ticks are so low despite 8+ LLM replies. Being fixed with 3 Ollama cache slots (run 6).

## Infrastructure defects found and fixed on the way
- Adapter `provider` property raised after disconnect → every finished sim became `infrastructure_error` (run 1).
- Ollama default 4096 ctx overflowed the 4.2k-token prompt → LLM timeouts (run 1). Now 12288 ctx, 3 parallel slots.
- Ollama chat template + τ-bench's mid-conversation system notes → user simulator leaked "assistant" headers and role-flipped into the agent (runs 3–4). Fixed in `llm_utils.generate` (header strip + system-note → user-note rewrite for Ollama models).
- Zero-cost pricing entry for `openai_compat` (was spamming warnings every tick).

## What this means for the eval build
- Even in `control` conditions, behaviour 1 (identifier capture) and behaviour 6 (grounded tool use) already produce measurable failures with the local agent; the b1 script flags them (`used_wrong`, `never_recognised`, spell-request rate 0.5).
- Behaviours 2–3 need `regular` complexity (backchannels, tics) and a latency-healthy agent; run 6 will tell whether the local LLM can keep TTFT ≈ 1 s. If not, the local stack should use a faster/smaller model for turn-taking studies and reserve llama3.1:8b for text-mode/behaviour-1/6 work.
- `too_many_errors` (10) truncates weak-agent calls; per-call error counts are themselves a useful metric (add to report).

## Run 8 (14:41) — invalid: machine slept mid-run
`pmset -g log` shows the Mac entered idle sleep at 14:49 (this laptop is set to sleep after 1 min idle) and only
dark-woke for ~5 s every ~15 min until 17:12. Task 6 ran 974 ticks over 2.5 h of wall clock with 15-min holes inside
single ticks; the real-time `AgentSession` saw those as LLM/TTS timeouts (Ollama 500 "Request timed out" at 14:49:26).
Nothing from run 8 is usable. Fix: `scripts/daemonize.py` now wraps every detached run in `caffeinate -ims`
(set `NO_CAFFEINATE=1` to opt out). A closed lid still sleeps the machine — keep it open or use `pmset -c disablesleep 1`.
Run 9 = run 8's configuration, relaunched at 17:17 under caffeinate.

## Run 9 (`…_1717`, run 8 config under caffeinate: 8 s caller patience, flash attention, 3 slots) — first valid local run
| task | reward | termination | duration | agent speech ticks | user speech ticks | tool calls / errors | JSON spoken aloud |
|---|---|---|---|---|---|---|---|
| 6 (exchange, no email known) | 0 | too_many_errors | 537 s | 128 / 1797 (7 %) | 1150 (64 %) | 10 / 10 | 5 |
| 14 (cancel, refund) | 0 | too_many_errors | 424 s | 103 / 1393 (7 %) | 862 (62 %) | 10 / 10 | 5 |

Panel: L_R 5.11 s · L_Y 0.65 s · R_R 43 % · R_Y 92 % · I_A 0.40 (run 5: 3.05 / 0.60 / 47 % / 100 % / 0.29). b1: auth 0/2,
entity recall 0.27, spell-request rate 1.0. The agent now speaks (7 % of ticks vs 0 %), but the response rate is still
< 50 %: LiveKit sidecar TTFT p50 5.7–6.2 s, p95 8.5–9.0 s, max 14 s, so tool-bearing turns (LLM → tool → LLM ≈ 12 s)
still overrun the 8 s patience and the caller's re-prompt ("Wait, what's going on… now you're just silent") interrupts
the late reply (12 agent interruptions).

### Failure modes (agent, prompt-addressable → behaviours)
1. **Tool call spoken as JSON** — 5 per sim: "Here is a function call to verify the user's email first: {"name": "find_user_id_by_email", "parameters": {"email": " the customer provided email"}}" read aloud by TTS; also chain-of-thought aloud ("To determine the best function call to answer the prompt…"). llama3.1:8b emits tool calls as prose when it also wants to talk. Now counted as `json_spoken` by `evals/inspect_run.py`.
2. **Placeholder identifiers** — `#W0000000`, `Doe`/`12345`, `gift_card_0000000` used in real tool calls before any lookup (behaviour 6); the pre-screen reproduces it in text mode in seconds (`cancel_pending_order(order_id="#W0000000")` on turn 1).
3. **Email passed literally from ASR** (behaviour 1, task 14): caller said "mia.garcia2723 at example dot com", whisper heard "Mia, Garcia2723 at example.com", agent called `find_user_id_by_email("Mia, Garcia2723@example.com")` six times without normalising, reading back or asking to spell.
4. **Empty required field** — `find_user_id_by_name_zip(first_name="")` ×5 after the caller could not give a first name.

### Harness defects found (fix before more runs)
- **LiveKit LLM timeout**: 9 of 59 agent LLM requests died at exactly 10.0 s (Ollama returns 500 when the client disconnects) = livekit-agents `DEFAULT_API_CONNECT_OPTIONS.timeout=10`, then a retry pays the prompt eval again → 14–17 s turns. Local preset needs a longer timeout.
- **Prompt-cache eviction**: agent (`/v1/chat/completions`, 59 calls, p50 6.2 s) and user simulator (`/api/chat`, 35 calls, p50 5.6 s) share one llama3.1:8b runner; warm turns cost 0.1 s in isolation (benchmark), so the 6 s median is prompt re-evaluation caused by the two conversations competing for slots. Fix to test: user simulator on `llama3.2:3b` (own runner, own KV slots). Cold-start prompt eval on this M5/17 GB: 7.6 s (8b) vs 3.4 s (3b).
- **User simulator suggestibility** (llama3.1:8b): in task 6 the caller (gold: mei_kovacs_8020, zip 28236, email unknown) adopted the agent's placeholder "Doe" ("my last name is spelled D-O-E") and later said "I don't remember what my first name is". Gold name entities were therefore never spoken (b1 `never_recognised` 0.73 is a user-sim artefact here, not an agent failure). Behaviour-1 attribution on the local stack needs a less suggestible user-sim model; candidates within 17 GB RAM: qwen2.5:7b (to test).
- `b1_identifiers.py` heuristic said auth succeeded because the *arguments* matched gold; now requires a lookup tool result without error (auth 0/2 on this run).

### Text pre-screen (`evals/text/test_b1_spelling.py`, keyless, llama3.1:8b, prompt v0): 0/3 pass in 114 s
- name_zip_corrupted: wrote before auth (`cancel_pending_order(order_id="#W0012345")`), lookup with `first_name="Y", last_name="R", zip="19222"`.
- email_spoken / zip_digits_merged: same pattern — `cancel_pending_order(order_id="#W0000000")` on the first turn, then `zip="19222"` for "nineteen one twenty two".

## Run 10 (`…_2213`, user simulator on llama3.2:3b, LLM timeout 40 s) — invalid, two causes
1. Ollama could not keep both runners resident: llama3.1:8b with 3 KV slots = 7.4 GB, llama3.2:3b = 4.6 GB, over the
   budget on 17 GB ("model predicted to exceed available memory, evicting"), so the models were swapped on every
   agent/user alternation. Fix: `OLLAMA_NUM_PARALLEL=2` (now the `local_stack.sh` default) → ≈ 6.5 + 2.8 GB.
2. The laptop slept at 22:16 on **low battery** (3 % this morning) — `caffeinate` cannot prevent that; the run sat
   suspended for 13 h. Runs need the charger connected.
Run 11 = run 10's configuration with 2 slots per runner, on AC power.

### Two-runner attempt (2026-09-29 morning) — parked
- With 2 KV slots the two runners still do not co-reside: Ollama's scheduler is limited by macOS *free* pages
  (`system_limited=true, system_free=2.3 GiB`), not by the 11.8 GiB Metal budget.
- A second Ollama instance on :11435 (`USERSIM_INSTANCE=1`, LiteLLM `OLLAMA_API_BASE`) bypasses that accounting, but the
  machine had 6 of 7 GB swap in use (Notion, Brave, Discord, several Claude sessions) and the 8b load hung for > 6 min.
  Kept as an opt-in for when other apps are closed.
- Decision: single runner, both LLMs llama3.1:8b, 3 slots, LLM timeout 40 s, caller patience 15 s (a tool turn is
  LLM ≈ 6 s + tool + LLM ≈ 6 s). Run 11 (`smoke_run11.log`) = this configuration.

## Run 11 (`20260929_1135`, single runner, LLM timeout 40 s, `--wait-to-respond-other 15`) — valid, loop diagnosed
| task | reward | termination | duration | agent speech | user speech | tool calls / errors | JSON spoken |
|---|---|---|---|---|---|---|---|
| 6 | 0 | agent_stop (transferred to human) | 350 s | 11 % | 48 % | 10 / 9 | 0 |
| 14 | 0 | agent_stop (transferred to human) | 274 s | 9 % | 49 % | 8 / 7 | 0 |

Panel: L_R 4.35 s · L_Y 0.70 s · **R_R 20 %** · R_Y 100 % · I_A 0.10. Sidecar TTFT p50 3.8–5.5 s, p95 7.7–9.1 s (cold first
call 12.4 s); no LLM timeouts reached the agent (the 8 Ollama 500s lasted ~1 s and LiveKit's retry absorbed them).

**Why R_R fell to 20 %** (sidecar, task 6): caller ends turn → STT final +1.9 s → LLM 12.4 s → tool call; the caller
resumes speaking ~5 s after its own turn ends (τ-bench `--wait-to-respond-self`, which I had not raised — only
`--wait-to-respond-other` was 15 s) → LiveKit treats that as an interruption and discards the post-tool reply →
next LLM call → same hallucinated tool call → … The agent ends up emitting tool calls and almost never speaking.
Run 12 sets both thresholds to 15 s.

Agent failures (prompt-addressable) confirmed again:
- task 6: `exchange_delivered_order_items(order_id="#W0000000", item_ids="['1008292230','1008292231']", payment_method_id="gift_card_0000000")` as the *first* action, 4× — placeholder ids, list arguments serialised as strings, no authentication.
- task 14: agent asked for the email to be spelled (good), caller spelled "m-i-a dot g-a-r-c-i-a … two seven two three at example dot com", whisper heard "amaya.grcia.um2723 at example.com", agent called `find_user_id_by_email("amaya.grcia.um2723 at example.com")` (no normalisation, no read-back), then `find_user_id_by_name_zip(first_name="Amaya", last_name="Grcia", zip="umm")`. Behaviour-1 failure spanning STT (letter-by-letter spelling is lost by whisper small.en) and LLM (literal pass-through).
- Both calls ended with the agent transferring to a human after the caller said "I think we got disconnected".
User-sim artefact: with the agent silent, llama3.1:8b invented an agent request ("So, you're saying you need my order number?").

## Run 12 (`20260929_1237`, both patience thresholds 15 s) — the local loop works; failures are now the agent's
| task | reward | termination | duration | agent speech | user speech | tool calls / errors | spell requests |
|---|---|---|---|---|---|---|---|
| 6 | 0 | too_many_errors | 635 s | 28 % | 23 % | 10 / 10 | yes |
| 14 | 0 | too_many_errors | 752 s | 38 % | 25 % | 10 / 10 | yes |

Panel: L_R 11.16 s (local-only number) · L_Y 0.80 s · **R_R 100 %** · R_Y 50 % (n=2) · I_A 0.11. TTFT p50 1.4 s (task 14,
cache now mostly warm) / 6.3 s (task 6), p95 7.5–8.6 s. No infrastructure defects left in the loop.

### Behaviour-1 evidence (task 14 — the cleanest example so far)
- Caller: "My email address is mia.garcia2723@example.com. So, it's M, I, A, G, A, R, C, I, A, 2, 7, 2, 3, @, E, X, A, M, P, L, E, O, M."
- STT heard: "Mia. Garcia2723 at example, com. So it's m-i-a-g-a-r-c-i-a-2723 at ex, a-m-p-l-e-o-m."
- Agent calls: `find_user_id_by_email("Mia. Garcia2723@example, com")`, later `("MIA.GARCIA2723@EXAMPLE, COM")` — it had every
  letter and still never normalised (case, spaces, "at"→@, ", com"→.com), never read the reconstructed address back
  digit-by-digit, and asked the caller to "confirm you spelled it correctly" instead. The letter-by-letter channel
  *worked*; the reconstruction step is the failure → an eval assertion for prompt v1.
- `find_user_id_by_name_zip(first_name="Mia", last_name="Garcia2723", zip="")` — digits from the email glued to the last name.

### Tool grounding (behaviour 6, out of the current 3 but cheap to score)
Placeholder arguments as real calls: `find_user_id_by_email("the email the customer is using to contact you")` (first action),
`cancel_pending_order(order_id="user does not have order id")`, `find_user_id_by_name_zip("customer","provided","provided")`,
`get_user_details(user_id="find_user_id_by_email", email=…)`, `get_order_details(order_id="None")` ×3, `#W0000000` ×4.
Also hallucinated capability ("I've called the tool to send you an email with the list of recent orders") and a spoken
A/B/C menu. The prompt's own spelling example leaks into arguments: `find_user_id_by_name_zip(first_name="J", last_name="O, H, N", zip="12345")`.

### User-simulator artefact (task 6, again)
Gold caller mei_kovacs_8020 / zip 28236 / email unknown: the caller gave the zip correctly but, led by the agent, produced
"j-o-h-n underscore doe at gmail dot com" and never stated its name (it only knows the user id string). Behaviour-1
attribution for name/email entities on the local stack is unreliable until the user simulator is a less suggestible model.

## Base-model check on the text pre-screen (2026-09-29, prompt v0, 3 cases, keyless)
| agent LLM | passed | what it did |
|---|---|---|
| llama3.1:8b | 0/3 | `cancel_pending_order(order_id="#W0000000")` as first action in 2/3 cases; lookups with `zip="19222"`, `first_name="Y"` |
| qwen2.5:7b | 2/3 (judge inconclusive on 2, rule checks passed) | no tool call before identity; asks for the order id / to spell first and last name; the email case asked for the order id instead of spelling the email (rule 2 fail) |

Decision: local default LLM → **qwen2.5:7b** for agent and user simulator (`TAU2_LOCAL_LLM` switches back). llama3.1:8b's
failures are basic tool grounding, not voice behaviours; qwen's remaining failures are the voice-specific ones the evals
target. Run 13 = smoke with qwen (validates tool calling through the LiveKit openai plugin + Ollama).

## Run 13 (`20260929_1341`, qwen2.5:7b agent + user simulator) — validation of the new base model
| task | reward | termination | duration | agent speech | user speech | tool calls / errors | placeholder args |
|---|---|---|---|---|---|---|---|
| 6 | 0 | max_steps (20 min cap) | 1328 s | 58 % | 15 % | 0 / 0 | 0 |
| 14 | 0 | user_stop (caller hung up) | 1124 s | 48 % | 12 % | 3 / 2 | 0 |

Panel: L_R 4.03 s · R_R 100 % · I_A 0. Sidecar: TTFT p50 0.26–0.28 s on *every* call (qwen's chat template keeps the
prefix stable, so Ollama's cache holds — the llama3.1 6 s TTFT was a template/cache artefact, not a machine limit);
LLM duration p50 3.6–5.2 s because replies are long (completion tokens p50 78–161, max 223 → 30–40 s of TTS per turn).

qwen agent profile (what the evals will measure on this base):
- Never fabricates tool calls or arguments (0 placeholder args; tool calls only when it believes it has an id).
- **First successful authentication on the local stack**: `find_user_id_by_email("Mia.Garcia2723@example.com")` → mia_garcia_4516
  — after two attempts with STT-mangled input (`get_order_details("#W123456789")` from the caller's invented id;
  `find_user_id_by_name_zip("MIA","GRCIA","XXXXXXXX")` — it passed the caller's *unknown* zip as a literal placeholder).
- **Verbose** (policy v0 asks for short voice replies): 40-second monologues, "step by step" recaps, 13 of 32 LLM calls in
  task 6 cancelled by caller interruptions.
- **Passive / order-id fixation**: asks for the order id before authenticating (policy: authenticate first), in task 6
  never called a single tool in 22 minutes, in task 14 stopped acting after authentication (no get_user_details /
  get_order_details) until the caller hung up.
User-simulator (qwen): invents identifiers when pressed ("my order ID is W00123456", item "W001"), drifts off-task
("cancel the keyboard and mouse") — same class of artefact as llama; needs a local-only guard.

## Baseline subset launched (2026-09-29 14:50): `retail_subset_local_v0_tau_cascaded_v0`
30 tasks (`evals/subsets/retail_iter30.json`), `regular` complexity, qwen2.5:7b agent + user simulator, prompt v0,
patience 15 s both ways, LLM timeout 40 s, local user-sim anti-fabrication guard ON (`TAU2_LOCAL_USERSIM_GUARD`), Kokoro
voices (no accents → the accent dimension of `regular` is absent; noise, telephony band, frame drops, backchannels, tics,
non-directed speech are present). Expected ≈ 10 h sequential. Log: `runs/local_stack/subset_v0_qwen.log`.
Post-run: `scripts/run_retail.sh post <dir>` (+ `evals/inspect_run.py`, `evals/report.py --baseline`).
(First subset launch at 14:44 died instantly on all 30 tasks: relative `--agent-prompt-file` path after the script's
`cd external/tau2-bench`. `run_retail.sh` now resolves the prompt to an absolute path. Relaunched 14:49.)
- 2026-09-29 14:58–15:44: lid closed during the subset run (clamshell sleep; caffeinate cannot hold it) — task 4 lost
  45 min of wall clock. `evals/sleep_gaps.py <run>` flags such simulations (`sleep_gaps.json`); exclude them from
  latency / turn-taking metrics (rewards and identifier metrics are tick-based and survive).

## Baseline subset restarted with a 10-minute call cap (2026-09-29 16:52)
First two tasks of the 6000-tick run (4, 6) both hit the cap with the agent not acting (task 6: 0 tool calls in 20 min),
31 min wall each; Ollama at 105 % busy share, dominated by the user simulator's `regular`-mode decision calls
(≈ 480 calls/h, p90 13 s) — so concurrency 2 would not help. Local runs now use `--max-steps-seconds 600` (`MAX_CALL_SECONDS` env; the first relaunch wrongly passed `--max-steps`, which is the text-mode cap, and ran 6000 ticks again),
applied to every prompt version. Partial 6000-cap output kept at `…_v0_partial_6000cap` (tasks 4 and 6, reward 0/0).

## Subset cut to 10 tasks (user, 2026-09-29 21:55)
The laptop was closed for most of the evening (clamshell sleep 19:23–21:49); 4/30 tasks done in 4 h. New iteration
subset `evals/subsets/retail_iter10.json` = tasks 4, 6, 7, 8, 14, 16, 19, 22, 23, 24 (the first ten in run order, so
the finished ones count). The running v0 baseline is stopped once its 10th task completes; iter30 stays available
(`SUBSET=30`).

## v1 subset run, first attempt (2026-09-30 12:41) — stalled by a runaway user-simulator generation
Task 7 stopped ticking at 13:18:44: the user simulator's `user_streaming_response` call (qwen2.5:7b via LiteLLM, no
`max_tokens`) generated 8,922 tokens at 24 tok/s before being killed (23 min). `llm_utils.generate` now caps local
user-sim turns at 400 tokens (`TAU2_LOCAL_USERSIM_MAX_TOKENS`) and decision calls at 32. The v0 baseline did not hit
this (all its user turns finished), so the cap does not change v0's numbers. v1 relaunched from scratch at 13:45
(partial output kept in `…_v1_partial_runaway`: tasks 4, 6 reward 0/0).
