# v3 `v3_authenticate_first` (= v1 + authenticate first + act in the same turn) — 10 tasks, `regular`, local (2026-10-01)

Run: `retail_subset_local_v3_authenticate_first_v3` (16:50 → 11:32 next day; the laptop slept overnight inside task 23,
flagged in `sleep_gaps.json`; rewards and identifier metrics unaffected).

| metric | v0 | v1 | v3 | reads as |
|---|---|---|---|---|
| pass^1 | 0/10 | 0/10 | 0/10 | |
| lookups (`find_user_id_*`) | 1 | 3 | **8** | the agent now reaches the authentication step |
| authentications | 0 | 0 | 0 | every lookup argument was wrong (see below) |
| b1 heard-but-not-used / wrong-STT / wrong-LLM | 0.11 / 0.11 / 0 | 0.11 / 0.16 / 0 | **0.32** / 0.16 / 0 | gold values are now being *said* by the caller and *heard*; the agent asks to spell instead of using them |
| unbacked claims ("let me check…" with no call) | 2 | 6 | **9** | the "act now" wording did not stop narration; it may have encouraged announcing |
| tool calls (errors) | 4 (2) | 13 (13) | 10 (9) | |
| S_BC / S_VT / S_ND | 0.32 / 0.39 / 0.72 | 0.26 / 0.47 / 0.71 | 0.22 / 0.56 / 0.85 | S_BC keeps sliding as the agent talks less (22 % of ticks) and is interrupted earlier |
| R_R / R_Y / I_A | 89 % / 0.90 / 0.13 | 87 % / 0.93 / 0.15 | 76 % / 0.78 / 0.06 | fewer, shorter replies → lower response and yield rates |

## What the lookups show (behaviour 1, now observable in voice)
- `find_user_id_by_name_zip("May", "Davis", "80217")` ×2 — caller said "Mei"; whisper small.en hears "May" (STT) → `used_wrong_misheard`.
- `find_user_id_by_name_zip("S.O.F.I.", "", "98193")`, `("SOS", "I", "98193")` — the caller spelled "S-O-F-I-A"; the agent passed
  the raw spelled transcript as the first name (LLM reconstruction, the v2 protocol's target).
- `find_user_id_by_name_zip("Sophia", "Hernandez", "193")` — zip truncated by a turn split.
- Caller said "Mei Kovacs, and my zip code is 28236" clearly (task 8) → agent: "could you spell out your zip code letter by
  letter?" ×3, then "Let me check your account now" with no call. The v0 spell-everything rule + narration.
- `modify_user_address(user_id="jason_garcia_729")` ×3 — user id supplied by the *user simulator* (invented), agent used it
  without a lookup (v3 says a user id can never come from the caller; not followed).

## Decision
v3 is the first version where behaviour 1 is exercised end to end in voice, so it stays as the base. Two defects to fix
next, both already drafted: **v4** (saying is not doing: name the tools, no claims without a returned call, spell only when
garbled or after a failed lookup) and the **v2 read-back protocol** (rebuild spelled values) to be layered on top. S_BC on
this stack is a VAD-interruption property (see v1 findings) → config ablation, not prompts.
