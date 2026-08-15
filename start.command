#!/bin/zsh

# Always run from the folder containing this launcher.
cd "${0:A:h}"

echo "Starting Discord bot with:"
echo "  /usr/bin/python3"
/usr/bin/python3 main.py

status=$?
echo
echo "Bot stopped with exit code $status. Press any key to close."
read -k 1
exit $status
