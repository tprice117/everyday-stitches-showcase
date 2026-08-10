#!/usr/bin/env bash
#
# monthly_sync.sh — run the WooCommerce import from cron on the EC2 box.
#
# Wraps `python -m tools.woo_pull --commit` with the things cron needs and the
# tool itself doesn't do: absolute paths (cron has almost no environment), a
# rolling log, a lock so two runs can never overlap, and a clear exit status.
#
# Install (on EC2):
#   crontab -e
#   0 3 1 * * bash /home/ubuntu/ESAppProject/sales-sync/tools/monthly_sync.sh
#
# Overridable via the environment:
#   WOO_SYNC_LOG              log file (default: <app>/logs/woo_sync.log)
#   WOO_SYNC_LOOKBACK_MONTHS  how far back to pull (default: 2)
#   WOO_SYNC_MAX_LOG_BYTES    rotate the log past this size (default: 1 MB)

set -uo pipefail

SYNC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # .../sales-sync
APP_DIR="$(dirname "$SYNC_DIR")"                              # .../ESAppProject
PY="$APP_DIR/venv/bin/python"

LOG="${WOO_SYNC_LOG:-$APP_DIR/logs/woo_sync.log}"
LOOKBACK_MONTHS="${WOO_SYNC_LOOKBACK_MONTHS:-2}"
MAX_LOG_BYTES="${WOO_SYNC_MAX_LOG_BYTES:-1048576}"

mkdir -p "$(dirname "$LOG")"

# Single-run rotation: keep one previous log, start fresh past the size cap.
# A monthly job barely grows this, so one generation is plenty.
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt "$MAX_LOG_BYTES" ]; then
    mv -f "$LOG" "$LOG.1"
fi

exec >> "$LOG" 2>&1

# Never let two imports run at once (a manual run while cron fires, say).
# Non-blocking: if one is already going, log and leave rather than queue up.
LOCK="$APP_DIR/.woo_sync.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
    echo "$(date -Is)  SKIPPED — another woo_sync is already running"
    exit 0
fi

if [ ! -x "$PY" ]; then
    echo "$(date -Is)  FAILED — no venv python at $PY"
    exit 1
fi

# Re-pull a couple of months each time rather than just the last one. The upsert
# is keyed on (source, external_id), so overlapping windows update in place
# instead of duplicating — and the overlap catches orders edited after the month
# they were placed, which a tight window would miss.
SINCE="$(date -d "${LOOKBACK_MONTHS} months ago" +%Y-%m-01)T00:00:00"

echo "===== $(date -Is)  woo_sync START (since=$SINCE) ====="
cd "$SYNC_DIR" || { echo "FAILED — cannot cd to $SYNC_DIR"; exit 1; }

# Unbuffered, so stdout and stderr land in the log in the order they happened.
# Buffered stdout otherwise flushes at exit, printing a traceback *above* the
# output that preceded it — which reads very confusingly after the fact.
PYTHONUNBUFFERED=1 "$PY" -m tools.woo_pull --since "$SINCE" --commit
status=$?

if [ "$status" -eq 0 ]; then
    echo "===== $(date -Is)  woo_sync OK ====="
else
    # Non-zero leaves a mail/exit trail for cron, and nothing was written —
    # woo_pull rolls back its whole transaction on failure.
    echo "===== $(date -Is)  woo_sync FAILED (exit $status) — nothing committed ====="
fi
exit "$status"
