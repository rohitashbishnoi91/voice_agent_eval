#!/usr/bin/env bash
# Canonical τ-bench retail invocations for the LiveKit AgentSession cascaded agent.
#
# Usage:
#   scripts/run_retail.sh smoke   [prompt_file]          # 2 tasks, control conditions, verbose audio
#   scripts/run_retail.sh compare [prompt_file]          # 5 tasks: built-in livekit provider vs livekit_session (regular)
#   scripts/run_retail.sh subset  [prompt_file] [name]   # 30-task iteration subset, regular
#   scripts/run_retail.sh full    [prompt_file] [name]   # all 114 retail tasks, regular, 1 trial
#   scripts/run_retail.sh ablate  [prompt_file] [name]   # subset under control + control_accents (behaviour-1 attribution)
#   scripts/run_retail.sh post    <run_dir>              # interaction metrics + review + per-task summary for a finished run
#
# Env: STACK=local|paid (default local; run `scripts/local_stack.sh up` first for local),
#      PRESET (session preset; local default "local", paid default "cascaded-session"),
#      CONCURRENCY, USER_LLM / REVIEW_MODEL (LiteLLM strings), TAU2_VOICE_SYNTHESIS_PROVIDER=kokoro|edge|elevenlabs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAU="$ROOT/external/tau2-bench"
# STACK=local (default) → fully local: Ollama LLM, local whisper/Kokoro servers, Kokoro user voices.
# STACK=paid  → leaderboard-comparable: gpt-4.1 + Deepgram + ElevenLabs personas (needs keys).
STACK="${STACK:-local}"
if [[ "$STACK" == "local" ]]; then
  PRESET="${PRESET:-local}"
  CONCURRENCY="${CONCURRENCY:-1}"
  REVIEW_MODEL="${REVIEW_MODEL:-ollama_chat/${TAU2_LOCAL_LLM:-qwen2.5:7b}}"
  if [[ -n "${USERSIM_OLLAMA:-}" ]]; then
    # Second Ollama instance for the user simulator (local_stack.sh USERSIM_INSTANCE=1); LiteLLM reads
    # OLLAMA_API_BASE. The agent keeps :11434 via the session preset's base_url.
    export OLLAMA_API_BASE="$USERSIM_OLLAMA"
    USER_LLM="${USER_LLM:-ollama_chat/llama3.2:3b}"
  else
    USER_LLM="${USER_LLM:-ollama_chat/${TAU2_LOCAL_LLM:-qwen2.5:7b}}"   # same model/runner as the agent (one loaded model)
  fi
  export TAU2_VOICE_SYNTHESIS_PROVIDER="${TAU2_VOICE_SYNTHESIS_PROVIDER:-kokoro}"
  export TAU2_VOICE_DECISION_MODEL="${TAU2_VOICE_DECISION_MODEL:-$USER_LLM}"
  export TAU2_REVIEW_MODEL="$REVIEW_MODEL"
  # Local LLM turns cost ~6 s TTFT (prompt-cache misses on this laptop) and a tool turn
  # (LLM → tool → LLM) ≈ 12 s; the benchmark's 1.0 s caller patience would turn every reply
  # into a barge-in, so the local stack raises it. LOCAL-ONLY DEVIATION: turn-taking
  # metrics (L_R, R_R, I_A, behaviours 2-3) from local runs are not leaderboard-comparable.
  USER_WAIT="${USER_WAIT:-15.0}"
  # Call cap 600 s of call (3000 ticks) instead of τ-bench's 1200 s: local calls run at ~0.3 s/tick
  # and a local agent that has not acted in 10 min never does. LOCAL-ONLY DEVIATION, same for every
  # prompt version so subset numbers stay comparable with each other. (audio-native uses
  # --max-steps-seconds; --max-steps is the text-mode message cap.)
  MAX_CALL_SECONDS="${MAX_CALL_SECONDS:-600}"
  # Both patience thresholds: "other" = after the agent stops, "self" = after the caller's own
  # turn when the agent stays silent (the one that interrupted in-flight local LLM calls in run 11).
  EXTRA_RUN_ARGS="${EXTRA_RUN_ARGS:---user-llm $USER_LLM --hallucination-retries 0 --wait-to-respond-other $USER_WAIT --wait-to-respond-self $USER_WAIT --max-steps-seconds $MAX_CALL_SECONDS}"
else
  PRESET="${PRESET:-cascaded-session}"
  CONCURRENCY="${CONCURRENCY:-4}"
  REVIEW_MODEL="${REVIEW_MODEL:-claude-opus-4-5}"
  EXTRA_RUN_ARGS="${EXTRA_RUN_ARGS:-}"
fi
# Iteration subset: 10 tasks by default (SUBSET=30 for the original 30-task list).
SUBSET_FILE="$ROOT/evals/subsets/retail_iter${SUBSET:-10}.json"

mode="${1:-}"; prompt="${2:-$ROOT/agent/prompts/v0_tau_cascaded.md}"; name="${3:-}"
[[ -n "$mode" ]] || { sed -n '2,14p' "$0"; exit 1; }
# the prompt path must survive the cd into tau2-bench below
[[ "$mode" == post ]] || { [[ -f "$prompt" ]] || { echo "prompt file not found: $prompt" >&2; exit 1; }; prompt="$(cd "$(dirname "$prompt")" && pwd)/$(basename "$prompt")"; }

cd "$TAU"
run() {  # run <save_to> <complexity> [extra tau2 args...]
  local save_to="$1" complexity="$2"; shift 2
  echo ">>> tau2 run retail provider=livekit_session preset=$PRESET complexity=$complexity prompt=$prompt save_to=$save_to"
  uv run tau2 run --domain retail --audio-native \
    --audio-native-provider livekit_session --cascaded-config "$PRESET" \
    --agent-prompt-file "$prompt" \
    --speech-complexity "$complexity" --num-trials 1 \
    --max-concurrency "$CONCURRENCY" --verbose-logs \
    $EXTRA_RUN_ARGS --save-to "$save_to" "$@"
}
subset_ids() { python3 -c "import json;print(' '.join(json.load(open('$SUBSET_FILE'))['task_ids']))"; }
stamp() { date +%Y%m%d_%H%M; }
pname() { basename "$prompt" .md; }

case "$mode" in
  smoke)
    run "retail_smoke_${PRESET}_$(pname)_$(stamp)" control --task-ids 6 14 --audio-debug ;;
  compare)
    ids="6 14 22 33 79"
    echo ">>> built-in livekit provider (plugin-level cascade), same 5 tasks"
    uv run tau2 run --domain retail --audio-native --audio-native-provider livekit --cascaded-config default \
      --speech-complexity regular --num-trials 1 --max-concurrency "$CONCURRENCY" --verbose-logs \
      $EXTRA_RUN_ARGS --task-ids $ids --save-to "retail_compare_builtin_$(stamp)"
    run "retail_compare_session_${PRESET}_$(stamp)" regular --task-ids $ids ;;
  subset)
    run "retail_subset_${PRESET}_$(pname)_${name:-$(stamp)}" regular --task-ids $(subset_ids) ;;
  full)
    run "retail_full_${PRESET}_$(pname)_${name:-$(stamp)}" regular ;;
  ablate)
    for c in control control_accents; do
      run "retail_subset_${c}_${PRESET}_$(pname)_${name:-$(stamp)}" "$c" --task-ids $(subset_ids)
    done ;;
  post)
    dir="$2"; [[ -d "$dir" ]] || dir="$TAU/data/simulations/$2"
    echo ">>> post-processing $dir"
    uv run tau2 submit interaction-metrics "$dir" --output "$dir/interaction_metrics.json" || true
    env -u OLLAMA_API_BASE uv run tau2 review "$dir" --mode full --review-model "$REVIEW_MODEL" --output "$dir/review.json" || true   # reviewer on the agent instance
    (cd "$ROOT" && uv run --project "$TAU" python evals/report.py "$dir") ;;
  *) echo "unknown mode: $mode"; exit 1 ;;
esac
