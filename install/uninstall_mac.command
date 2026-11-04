#!/bin/zsh
python3 "$(dirname "$0")/install.py" --remove
read -k 1 "?Press any key to close"
