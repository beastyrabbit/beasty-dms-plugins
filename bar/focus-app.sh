#!/bin/bash
# Focus a niri window by app_id (case-insensitive)
APP="$1"
WID=$(niri msg -j windows | jq -r --arg a "$APP" '.[] | select(.app_id | ascii_downcase == $a) | .id' | head -1)
[ -n "$WID" ] && niri msg action focus-window --id "$WID"
