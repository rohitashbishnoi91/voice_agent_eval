# v0 baseline — local stack, 10-task retail subset, `regular` complexity (2026-09-29/30)

Run: `external/tau2-bench/data/simulations/retail_subset_local_v0_tau_cascaded_v0`
Agent: LiveKit `AgentSession` cascade — faster-whisper small.en → Ollama qwen2.5:7b → Kokoro; v1-mini turn detector,
VAD interruptions (no adaptive-interruption model locally). Prompt v0 = τ-bench `CASCADED_MODEL_INSTRUCTION` verbatim.
User simulator: qwen2.5:7b + local anti-fabrication guard, Kokoro persona voices (no accents). Caller patience 15 s,
call cap 600 s. Sleep-contaminated sims (latency metrics only): tasks 6, 14, 23, 24 (`sleep_gaps.json`).

## Headline
| metric | v0 local | leaderboard cascaded baseline (retail) |
|---|---|---|
| pass^1 | **0 / 10** (all `max_steps`) | 28.9 |
| tool calls per sim | 0.4 (4 in 10 calls), 0 write actions, 0 authentications | — |
| L_R / L_Y | 4.74 s / 0.77 s (local-only) | 4.02 / 0.84 |
| R_R / R_Y / I_A | 89 % / 90 % / 0.13 | 77 % / 99 % / 0.58 |
| S_BC / S_VT / S_ND | **0.32** / 0.39 / 0.72 | 0.57 / 0.50 / 0.52 |
| b1 auth / recall / spellReq | 0.00 / 0.00 / 0.70 | (leaderboard run: auth 0.56) |
| agent speech share | 22–42 % of ticks (median reply 70–110 tokens ≈ 30 s) | — |
| LiveKit false interruptions (resumed) | 16 | — |

## Failure modes (task ids; tick timestamps in `runs/analysis/subset_v0_timeline.txt`)

### F1 — Backchannels treated as directed speech (behaviour 2) — every call
Caller backchannels ("uh-huh", "mm-hmm") arrive through whisper small.en as "Amen.", "Okay.", "and mem him", "and then
her", "I'll be right with you"; the v1-mini turn detector closes the turn on them and the agent *answers* each one:
task 7 — "I'm sorry, I didn't catch that. Could you please clarify what you meant by 'Amen'?" then re-asks its previous
question in full. S_BC 0.32 (the agent yields to 68 % of backchannels), 511 backchannel events in 10 calls; the agent's
long replies (≈ 30 s) maximise exposure. Two mechanisms, both visible in the sidecar: (a) VAD-level interruption →
`agent_false_interruption` + resume (16×), (b) transcript-level → LLM reply to the backchannel (the dominant one).
Prompt-addressable part: (b) and reply length. Config part: (a) needs adaptive interruption (LiveKit Cloud) or a
longer `min_interruption_duration`.

### F2 — Asks for order/item ids instead of authenticating (policy) — 9/10 calls
"Could you please provide me with your order ID and the item IDs…", then "spell out the order ID and the item IDs …
one by one" (the v0 spelling instruction applied to ids the caller does not know). The policy requires email or
name+zip authentication first; the caller in most subset tasks does not know ids (`unknown_info`). Net effect: b1 never
starts — gold identifiers were never spoken in 79 % of entity slots (`neverRec`) because the agent never asked for them.

### F3 — Verbosity / recap loops
Median reply 70–110 tokens, max 223; "Let's break this down step by step" recaps after every backchannel; 7–17 LLM
generations per call cancelled by caller speech (tasks 7, 16). The 10-minute call ends without a single lookup.

### F4 — Identifier reconstruction (behaviour 1) — seen in smoke runs, not reachable here
Runs 12/13: caller spells the email correctly, agent calls `find_user_id_by_email("Mia. Garcia2723@example, com")`
(no normalisation, no read-back), `find_user_id_by_name_zip(zip="XXXXXXXX")`. `b1` attributes these as
`used_wrong_heard_ok`. Not observable in this baseline because F1–F3 stop the call before authentication.

### Not failing
No fabricated tool calls, no placeholder arguments, no JSON spoken (all qwen improvements over llama3.1:8b);
barge-in handling itself is fine (R_Y 0.90, L_Y 0.77 s, I_A 0.13).

## Prompt hypotheses (one per version)
- **v1 `v1_backchannel_concise.md`** (F1 + F3): short acknowledgements are the caller listening, not a turn — never
  answer them or ask what they meant; keep every reply to one or two short sentences with one question.
- **v2 `v2_identifier_readback.md`** (F4): rebuild spelled identifiers, read the rebuilt value back, confirm before lookup.
- **v3 (to draft)** (F2): authenticate first via email or name+zip; never ask for order/item ids the caller doesn't know;
  look them up after authentication.
Evaluate each on the same 10 tasks; keep a version only if pass^1 does not drop and its target metric improves
(v1: S_BC ↑, cancelled generations ↓, tool calls ↑; v2: b1 wrongLLM ↓, auth ↑; v3: auth ↑, F2 count ↓).

## Text pre-screens (2026-09-30, qwen2.5:7b, keyless, seconds per case)
| pre-screen | v0 | v1 (first draft) | v2 |
|---|---|---|---|
| b2 backchannel (5 cases: uh-huh, mm-hmm, Amen., Okay., and mem him) | 2/5 — 40–68-word replies, re-asks in full, "spell out the order ID" | 3/5 — replies 15–35 words, still re-asks after "uh-huh"/"Okay." | — |
| b1 spelling (3 cases) | 0/3 on llama; qwen v0 2/3 | — | 2/3 — miss: `find_user_id_by_email("yusuf dot rossi at example dot com")` passed literally despite the rebuild rule |
Lesson: in text mode (and in the cascade) a user turn always yields a reply, so "do not reply" is not followable; v1 was
reworded to name the minimal acknowledgement ("Take your time.") and an explicit ~25-word cap → **v1 revised: 5/5**
("Go ahead.", "Take your time."; the garbled cases still produce one short question). v1 subset run launched 13:0x.
