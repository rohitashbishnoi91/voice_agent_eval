#!/usr/bin/env bash
# Start / stop / check the fully local inference stack used by PRESET=local:
#   Ollama  (agent + user-sim LLM, http://localhost:11434/v1)  model: $OLLAMA_MODEL (default qwen2.5:7b; TAU2_LOCAL_LLM=llama3.1:8b to switch), 3 slots, 12k ctx
#   Ollama  (OPTIONAL, USERSIM_INSTANCE=1: user-simulator LLM on http://127.0.0.1:11435, $USERSIM_MODEL, 2 slots, 8k ctx)
#           A second instance keeps the agent's prompt cache untouched by the user simulator, but two resident runners
#           (≈ 6 + 3 GB) only work on this 16 GB Mac with other apps closed (2026-09-29: 6 GB swap in use → model load
#           hung). Default = single instance, both LLMs llama3.1:8b, 3 KV slots (agent + user-sim + decision model).
#   STT     (faster-whisper, http://localhost:8000/v1)
#   TTS     (Kokoro ONNX,   http://localhost:8880/v1)
# Usage: scripts/local_stack.sh up | down | status | logs
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAU="$ROOT/external/tau2-bench"
LOG="$ROOT/runs/local_stack"; mkdir -p "$LOG"
OLLAMA_MODEL="${OLLAMA_MODEL:-${TAU2_LOCAL_LLM:-qwen2.5:7b}}"
USERSIM_MODEL="${USERSIM_MODEL:-llama3.2:3b}"
USERSIM_PORT="${USERSIM_PORT:-11435}"
STT_MODEL="${LOCAL_STT_MODEL:-small.en}"

health() { curl -s --max-time 3 "$1" >/dev/null 2>&1; }

case "${1:-status}" in
  up)
    if ! health localhost:11434/api/version; then
      echo ">>> starting ollama serve (context ${OLLAMA_CONTEXT_LENGTH:-12288})"
      # single-instance default: 3 slots (agent, user-sim, decision model); with USERSIM_INSTANCE=1 the agent needs 1
      OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-12288}" OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-$([[ "${USERSIM_INSTANCE:-0}" == 1 ]] && echo 1 || echo 3)}" OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 python3 "$ROOT/scripts/daemonize.py" "$LOG/ollama.log" ollama serve; sleep 2
    fi
    ollama list | grep -q "^${OLLAMA_MODEL%%:*}" || { echo ">>> pulling $OLLAMA_MODEL"; ollama pull "$OLLAMA_MODEL"; }
    if [[ "${USERSIM_INSTANCE:-0}" == 1 ]] && ! health "localhost:$USERSIM_PORT/api/version"; then
      echo ">>> starting user-simulator ollama on :$USERSIM_PORT ($USERSIM_MODEL)"
      OLLAMA_HOST="127.0.0.1:$USERSIM_PORT" OLLAMA_CONTEXT_LENGTH="${USERSIM_CONTEXT_LENGTH:-8192}" OLLAMA_NUM_PARALLEL=2 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 python3 "$ROOT/scripts/daemonize.py" "$LOG/ollama_usersim.log" ollama serve; sleep 2
    fi
    [[ "${USERSIM_INSTANCE:-0}" == 1 ]] && { ollama list | grep -q "^${USERSIM_MODEL}" || { echo ">>> pulling $USERSIM_MODEL"; ollama pull "$USERSIM_MODEL"; }; }
    if ! health localhost:8000/health; then
      echo ">>> starting local STT ($STT_MODEL)"
      DAEMON_CWD="$TAU" python3 "$ROOT/scripts/daemonize.py" "$LOG/stt.log" "$TAU/.venv/bin/python" "$ROOT/scripts/local_stt_server.py" --model "$STT_MODEL" --port 8000
    fi
    if ! health localhost:8880/health; then
      echo ">>> starting local TTS (Kokoro)"
      DAEMON_CWD="$TAU" python3 "$ROOT/scripts/daemonize.py" "$LOG/tts.log" "$TAU/.venv/bin/python" "$ROOT/scripts/local_tts_server.py" --port 8880
    fi
    for i in $(seq 1 120); do
      if health localhost:8000/health && health localhost:8880/health && health localhost:11434/api/version && { [[ "${USERSIM_INSTANCE:-0}" != 1 ]] || health "localhost:$USERSIM_PORT/api/version"; }; then
        echo "local stack ready"; exit 0
      fi
      sleep 2
    done
    echo "timed out waiting for services; see $LOG/*.log"; exit 1 ;;
  down)
    pkill -f local_stt_server.py || true; pkill -f local_tts_server.py || true
    echo "stopped STT/TTS servers (both ollama instances left running; 'pkill ollama' stops them)" ;;
  status)
    for s in "ollama-agent localhost:11434/api/version" "ollama-usersim localhost:$USERSIM_PORT/api/version" "stt localhost:8000/health" "tts localhost:8880/health"; do
      set -- $s; if health "$2"; then echo "$1: up"; else echo "$1: down"; fi
    done ;;
  logs) tail -n 20 "$LOG"/*.log ;;
  *) echo "usage: $0 up|down|status|logs"; exit 1 ;;
esac
