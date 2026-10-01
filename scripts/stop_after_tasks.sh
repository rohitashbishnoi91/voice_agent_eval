#!/usr/bin/env bash
# Stop a running tau2 subset once N simulations are complete (used to cut a run short).
# Usage: scripts/stop_after_tasks.sh <run log> <N>
L="$1"; N="$2"
while true; do
  if grep -qE "Status: $N/[0-9]+ complete" "$L"; then
    echo "$(date +%F_%T) $N tasks complete; stopping run"
    for p in $(pgrep -f "tau2 run --domain retail"); do kill -TERM -- -"$p" 2>/dev/null || kill -TERM "$p"; done
    sleep 8; pkill -9 -f "tau2 run --domain retail" 2>/dev/null; echo "left: $(pgrep -f 'tau2 run' | wc -l)"; exit 0
  fi
  pgrep -f "tau2 run --domain retail" >/dev/null || { echo "$(date +%F_%T) run exited on its own"; exit 0; }
  sleep 30
done
