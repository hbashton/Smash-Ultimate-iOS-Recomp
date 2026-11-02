#!/usr/bin/env bash
# Unlocks every fighter in this folder's save (save/), keeping a copy of it first in
# save_backups/. Run it with the game closed. See docs/DLC-AND-UNLOCKS.md.
here="$(dirname "$(readlink -f "$0")")"
cd "${SSBU_GAME_DIR:-$here}" || exit 1
python3 tools/unlock_all.py "$@"
