#!/usr/bin/env bash
# Install OMEGA pipeline cron jobs on VPS.
# Run once as the deploy user (not root).
#
# Assumes:
#   - Pipeline deployed to $PIPELINE_DIR (default: ~/omega-data-pipeline)
#   - Python venv at $PIPELINE_DIR/.venv with requirements installed
#   - /etc/omega-pipeline.env contains DATABASE_URL and TARGET_STATE_FIPS
#
set -euo pipefail

PIPELINE_DIR="${PIPELINE_DIR:-$HOME/omega-data-pipeline}"
REFRESH="$PIPELINE_DIR/cron/refresh.sh"
PYTHON="$PIPELINE_DIR/.venv/bin/python"
LOG_DIR="$PIPELINE_DIR/logs"

if [[ ! -f "$REFRESH" ]]; then
    echo "ERROR: $REFRESH not found. Is the pipeline deployed?" >&2
    exit 1
fi

chmod +x "$REFRESH"
mkdir -p "$LOG_DIR"

# Patch python path into run.py's shebang call (cron has no venv activated)
INVOKE="$PIPELINE_DIR/.venv/bin/python $PIPELINE_DIR/run.py"

# Build crontab additions (idempotent via marker line)
MARKER="# OMEGA-PIPELINE-CRON"

CRON_BLOCK="
$MARKER
# BLS LAUS — monthly, 1st of month at 03:00 UTC
0 3 1 * *   $REFRESH monthly   >> $LOG_DIR/cron-monthly.log 2>&1

# Socrata permits + NOAA — weekly, Sunday 02:00 UTC
0 2 * * 0   $REFRESH weekly    >> $LOG_DIR/cron-weekly.log 2>&1

# FEC contributions — quarterly (Jan/Apr/Jul/Oct 15th at 04:00 UTC)
0 4 15 1,4,7,10 *  $REFRESH quarterly >> $LOG_DIR/cron-quarterly.log 2>&1

# ACS + CBP + SVI + CDC — annual, January 20th at 05:00 UTC
# (ACS Dec release is final; CBP is October; running in Jan catches both)
0 5 20 1 *  $REFRESH annual    >> $LOG_DIR/cron-annual.log 2>&1
$MARKER-END
"

CURRENT_CRON="$(crontab -l 2>/dev/null || echo '')"

if echo "$CURRENT_CRON" | grep -q "$MARKER"; then
    echo "OMEGA cron entries already present. Remove manually to reinstall:"
    echo "  crontab -e   # delete lines between $MARKER and $MARKER-END"
    exit 0
fi

(echo "$CURRENT_CRON"; echo "$CRON_BLOCK") | crontab -

echo "Installed OMEGA cron jobs:"
crontab -l | grep -A 20 "$MARKER"
echo ""
echo "Test with:"
echo "  $REFRESH weekly"
echo "  $REFRESH monthly"
