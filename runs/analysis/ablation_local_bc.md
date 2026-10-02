# Config ablation `local-bc` — v1 prompt, `min_interruption_words=2`, `min_interruption_duration=1.0` (2026-10-01)

Run: `retail_subset_local-bc_v1_backchannel_concise_v1bc` (16:08 → 19:12, no sleep gaps). Same prompt, tasks, stack and
harness as the v1 run; only LiveKit's `TurnHandlingOptions` interruption thresholds differ (default 0 words / 0.5 s).

| metric | v1 (default interruption) | v1 + local-bc | reads as |
|---|---|---|---|
| S_BC backchannel selectivity | 0.26 | **0.97** | the agent talks through "mm-hmm" |
| S_ND non-directed selectivity | 0.71 | **0.89** | and through side-talk |
| S_VT vocal-tic selectivity | 0.47 | 0.45 | tics still cut it (they are longer than 1 s) |
| R_Y yield rate (genuine interruptions) | 0.93 | **0.05** | it now talks over real barge-ins |
| L_Y / I_A | 0.84 s / 0.15 | 0.92 s / 0.22 | |
| R_R / L_R | 87 % / 3.91 s | 82 % / 4.17 s | |
| LiveKit false-interruption pause/resume | 9 | **284** | every short caller sound pauses TTS for a beat, then resumes |
| agent speech share | 31 % | 44 % (up to 57 %) | |
| tool calls / auth / pass^1 | 13 / 0 / 0 | 3 / 0 / 0 | fewer lookups: the caller gets fewer openings |
| unbacked claims | 6 | 48 (41 in one looping call, task 24) | |

## Reading
- **Behaviour 2 is configuration on this stack, not prompt.** Prompts moved S_BC 0.32 → 0.26 (nothing); two threshold
  values moved it 0.26 → 0.97. The v1 prompt's real contribution was the LLM side (reply length, no re-asks), which this
  ablation keeps.
- **Behaviour 2 and behaviour 3 trade against each other** when interruption is decided by VAD + a word/duration gate:
  the gate that ignores "mm-hmm" also ignores "wait, stop" for its first second. τ-bench's panel is built to expose
  exactly this; the leaderboard's cascaded baseline sits at S_BC 0.57 / R_Y 0.99 by using Deepgram endpointing with
  semantics the gate lacks. LiveKit's *adaptive* interruption model (needs aligned-transcript STT, cloud) is the
  component designed to get both; it is one preset switch away (`cascaded-session`) once keys exist.
- **The 284 pause/resume events are the cost of `resume_false_interruption`**: audio-level stop, 1 s wait, resume — the
  caller hears a stutter on every backchannel. A middle setting (1 word / 0.7 s) or disabling resume should be the next
  ablation; the eval already measures both sides.
