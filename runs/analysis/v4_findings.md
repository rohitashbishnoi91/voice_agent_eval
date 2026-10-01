# v4 `v4_saying_is_not_doing` (compact rewrite of v0 + one 170-word block) — 10 tasks, `regular`, local (2026-10-01)

Run: `retail_subset_local_v4_saying_is_not_doing_v4` (12:21 → 15:17; one 13-min low-battery gap inside task 6).

| metric | v0 | v1 | v3 | v4 |
|---|---|---|---|---|
| pass^1 | 0/10 | 0/10 | 0/10 | 0/10 |
| tool calls (errors) | 4 (2) | 13 (13) | 10 (9) | 7 (7) |
| lookups / authentications | 1 / 0 | 3 / 0 | 8 / 0 | 6 / 0 |
| unbacked claims | 2 | 6 | 9 | 8 |
| b1 heard-not-used / wrong-STT / spellReq | 0.11 / 0.11 / 0.70 | 0.11 / 0.16 / 0.60 | 0.32 / 0.16 / 0.70 | 0.26 / 0.16 / **0.90** |
| S_BC / S_VT / S_ND | 0.32 / 0.39 / 0.72 | 0.26 / 0.47 / 0.71 | 0.22 / 0.56 / 0.85 | 0.21 / 0.81 / 0.68 |
| R_R / R_Y / I_A / false-int resumes | 89 % / 0.90 / 0.13 / 16 | 87 % / 0.93 / 0.15 / 9 | 76 % / 0.78 / 0.06 / 2 | 68 % / 0.74 / 0.09 / **35** |

## What v4 changed and what it did not
- The compact rewrite did not reduce narration in voice (unbacked claims 8 vs 9) and raised the spell-request rate to
  0.90: with "only if the lookup fails do you ask them to spell", the agent now runs lookup → fail → spell → lookup on
  STT-garbled values, which is the intended loop, but the values stay wrong because the STT stage is the bottleneck:
  - `find_user_id_by_name_zip("May", "Kovacs", "28236")` — everything right except "Mei" heard as "May" (tasks 7, 8, 19 all
    have "Mei"; whisper small.en never produces it). `b1`: `used_wrong_misheard`.
  - `find_user_id_by_name_zip("Mir", "Garcia", "94107")` — same.
  - task 14: the agent asked for the email letter by letter; the caller backchanneled "mm-hmm" during the pause; whisper
    rendered it "Amen"; the agent concatenated the backchannel transcripts into `find_user_id_by_email("Amenhome")`,
    then "Amenemblem". Behaviour 2 feeding behaviour 1: a selectivity failure that becomes an identifier failure.
- False-interruption resumes jumped to 35: the agent's new short "Take your time." turns are themselves cut by the
  caller's next backchannel and resumed — more, shorter agent turns = more VAD-level interruptions (S_BC 0.21).
- Barge-in metrics drift down with every version (R_Y 0.90 → 0.74, R_R 89 % → 68 %) for the same reason: the agent
  speaks less and later, so τ-bench's windows find fewer responses/yields. This is the cost side of brevity on a stack
  where interruption is VAD-only.

## Conclusions for the write-up
1. Prompt changes moved the LLM-side behaviours (reply length, re-asks after backchannels, reaching the lookup step,
   authenticate-first ordering) and are measurable with the evals built here; they did not move pass^1 or authentication
   on this stack, because the remaining failures are in STT (`Mei`→`May`, spelled letters) and in VAD-level interruption.
2. The evals attribute each failure to its stage (`used_wrong_misheard` vs `used_wrong_heard_ok`, LiveKit
   `agent_false_interruption` vs LLM re-asks, `unbacked_claims`), which is what makes the "prompt vs. config vs. model"
   decision possible — the main deliverable.
3. Next levers, in order: STT (Deepgram nova-3 or whisper-large), interruption config (`local-bc` ablation), and a
   base model that follows tool-use instructions (gpt-4.1 preset) — all one preset switch away.
