#!/bin/bash
# Removes Capella Fabric session directories older than MAXAGE_HOURS.
# Run via cron (see capella-sessions-cron).

SESSIONS_DIR="/tmp/capella_fabric"
MAXAGE_HOURS=4
LOG="/var/log/capella-fabric/session-cleanup.log"

if [ ! -d "$SESSIONS_DIR" ]; then
    exit 0
fi

REMOVED=0
while IFS= read -r -d '' dir; do
    rm -rf "$dir"
    echo "$(date -u +%FT%TZ) removed $(basename "$dir")" >> "$LOG"
    REMOVED=$((REMOVED + 1))
done < <(find "$SESSIONS_DIR" -maxdepth 1 -mindepth 1 -type d -mmin "+$((MAXAGE_HOURS * 60))" -print0)

if [ "$REMOVED" -gt 0 ]; then
    echo "$(date -u +%FT%TZ) cleanup: removed $REMOVED session(s)" >> "$LOG"
fi
