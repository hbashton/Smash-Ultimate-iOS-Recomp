#!/bin/sh
# Boot, go through the menus into a 1v1 Smash battle with scripted input, and run for a while.
# Usage: tools/dev/autobattle.sh <out dir> [seconds]   (extra SSBU_* settings come from the environment)
# Frames are captured to <out dir>, the log is <out dir>.log. Timings drift a little between runs;
# the repeated presses absorb that.
out=$1
secs=${2:-95}
rm -rf "$out" && mkdir -p "$out"
SSBU_INPUT="8:A,10:A,13:B,15:A,18:A,22:A,24.5:A,26:A,27.5:A,29:A,30.5:A,32:SUP/0.35,33:A,35:SUP/0.25,36:A,38:SUP/0.2,39:A,41:A,43:PLUS,44.5:PLUS,46:PLUS,52:A,54:A,56:A" \
SSBU_CAPTURE="$out" timeout "$secs" ./build/ssbu.exe dump/modules --status 30 > "$out.log" 2>&1
grep '^\[status\]' "$out.log" | grep GPU | tail -2
grep -A10 'host exception\|stopped during' "$out.log" | head -14
