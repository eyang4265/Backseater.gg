#!/bin/zsh

# Always run from the folder containing this launcher.
cd "${0:A:h}"

if [[ ! -f ".venv/bin/activate" ]]; then
  echo "Could not find the project's virtual environment at .venv/bin/activate."
  echo "Press any key to close."
  read -k 1
  exit 1
fi

source .venv/bin/activate
echo "Starting Discord bot with:"
echo "  $PWD/.venv/bin/python"
"$PWD/.venv/bin/python" main.py

status=$?
echo
echo "Bot stopped with exit code $status. Press any key to close."
read -k 1
exit $status
