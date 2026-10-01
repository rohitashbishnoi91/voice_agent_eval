# v1 `v1_backchannel_concise` vs v0 — 10-task subset, `regular`, local stack (2026-09-30)

Run: `retail_subset_local_v1_backchannel_concise_v1` (13:43–16:08, no sleep gaps). Same harness as v0 except the
user-sim token cap added after the runaway generation (does not affect v0's numbers).

| metric | v0 | v1 | Δ | reads as |
|---|---|---|---|---|
| pass^1 | 0/10 | 0/10 | 0 | no regression, no gain |
| agent completion tokens p50 / p90 | 74 / 116 | **41 / 55** | −45 % | brevity instruction followed |
| LLM generations cancelled by caller speech | 58 | **26** | −55 % | less exposure to interruption |
| LiveKit false interruptions (resumed) | 16 | **9** | −44 % | |
| tool calls (all sims) | 4 | 13 | +9 | more action, but 12/13 errored |
| τ-bench S_BC / S_VT / S_ND | 0.32 / 0.39 / 0.72 | 0.26 / 0.47 / 0.71 | −0.06 / +0.08 / −0.01 | **S_BC not improved** |
| R_R / R_Y / L_Y / I_A | 0.89 / 0.90 / 0.77 / 0.13 | 0.87 / 0.93 / 0.84 / 0.15 | ≈ | unchanged |
| b1 auth / spellReq | 0 / 0.70 | 0 / 0.60 | | still never authenticates |
| short (≤3-word) user turns answered by a new agent message | 379/403 (94 %) | 342/376 (91 %) | −3 pt | prompt barely moves this |

## What the numbers say
1. **Two mechanisms, only one prompt-addressable.** τ-bench's S_BC scores whether the agent keeps *talking* through a
   backchannel. On this stack the yield happens at the audio level: VAD-mode interruption stops TTS as soon as the
   caller's "mm-hmm" crosses `min_interruption_duration` (0.5 s), before any transcript exists. The prompt can only
   change what the LLM says *after* the turn detector has already ended the turn — which it did (shorter, fewer
   re-asks, half the cancelled generations) — and that is invisible to S_BC. Whisper also renders backchannels as
   real words ("Go, sir.", "I have.", "Amen."), so the v1-mini turn detector closes the turn and LiveKit always
   generates a reply; "Take your time." is still a reply.
   → Next experiment is a **config ablation**, as planned: `min_interruption_words` ≥ 2 and `min_interruption_duration`
   1.0 s (LiveKit `TurnHandlingOptions`), same v1 prompt, same 10 tasks. It bounds how much of S_BC is reachable at all.
2. **Brevity is a keeper.** Reply length halved with no loss anywhere; keep the section in later versions.
3. **The extra tool calls are not agent hallucinations.** Task 22: the *caller* (qwen user simulator) said "my user ID
   is 12345" and the agent, after confirming twice, called `modify_user_address(user_id="12345")` → "User not found";
   task 4: eight `get_product_details("6086499569")` retries. The local guard does not stop the user simulator from
   inventing a user id when the scenario gives it none. Agent-side lesson (for v3): a user id is never something the
   caller can supply — authenticate via email or name+zip first, then act.
4. **Authentication remains the wall** (b1 auth 0/10 in both): the agent still opens with "provide your order ID".
   → v3 = v1 + authenticate-first.

## Decision
v1 kept as the base for v3 (brevity), not as a behaviour-2 fix. Behaviour-2 next step = config ablation `local-bc`.
