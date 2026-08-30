#!/bin/bash
# Stops every running instance of this bot (main.py), from any checkout or worktree.
cd "$(dirname "$0")"

pids=$(pgrep -f '[m]ain\.py')

if [ -z "$pids" ]; then
    echo "No running bot instances found."
else
    echo "Stopping bot PIDs: $pids"
    kill $pids
    sleep 1
    remaining=$(pgrep -f '[m]ain\.py')
    if [ -n "$remaining" ]; then
        echo "Still running, force killing: $remaining"
        kill -9 $remaining
    fi
    echo "Done."
fi

osascript -e 'tell application "Terminal" to close (every window whose name contains "stop_bot.command")' >/dev/null 2>&1 &
