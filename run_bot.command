#!/bin/zsh
# Stop older instances of this bot before starting a fresh one.

set -eu

bot_path="${0:A:h}/main.py"
old_pids="$(pgrep -f "Python.*${bot_path}" || true)"

if [[ -n "${old_pids}" ]]; then
  print -r -- "Stopping existing bot process(es): ${old_pids}"
  kill -TERM ${(f)old_pids} 2>/dev/null || true

  for _ in {1..5}; do
    sleep 1
    old_pids="$(pgrep -f "Python.*${bot_path}" || true)"
    [[ -z "${old_pids}" ]] && break
  done

  if [[ -n "${old_pids}" ]]; then
    print -r -- "Force-stopping unresponsive bot process(es): ${old_pids}"
    kill -KILL ${(f)old_pids} 2>/dev/null || true
  fi
fi

exec /usr/bin/python3 "${bot_path}"
