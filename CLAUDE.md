# Development instruction

Before running `main.py`, stop the currently running Discord bot with `Ctrl+C`.
Run `main.py` anytime you change a file, always through the project's virtual
environment: `.venv/bin/python main.py`. Do not use the system `python3`,
because project dependencies such as `py-cord` are installed in `.venv`.

When you add a new public slash command, also add it to the `/commands` command directory with a description and usage instructions. Keep owner-only commands out of that directory.

For any new command that requires a player, default to the account linked to the invoking Discord user when no player is supplied. For commands that require a match, default to that account's most recent match. Player-oriented commands should provide optional `server`, `summoner`, and `user` options so callers can select another account when needed.
