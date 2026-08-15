#!/usr/bin/env bash
# OMEGA Data Pipeline — VPS refresh script
# Invoked by cron on cadence per signal. No Anthropic calls anywhere.
#
# Required env (set in /etc/omega-pipeline.env or systemd unit):
#   DATABASE_URL      — Postgres connection string
#   TARGET_STATE_FIPS — comma-separated FIPS codes, e.g. "26,12"
#
# Optional:
#   BLS_API_KEY       — free BLS registration key (lifts 500→50k req/day)
#   CENSUS_API_KEY    — free Census API key
#   SOCRATA_APP_TOKEN — Socrata app token (lifts rate limits)
#   FEC_CYCLE         — FEC election cycle year, e.g. "2026"
#
set -euo pipefail

PIPELINE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$PIPELINE_DIR/logs"
mkdir -p "$LOG_DIR"

ENV_FILE="/etc/omega-pipeline.env"
if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$ENV_FILE"
fi

CMD="python $PIPELINE_DIR/run.py"
TS="$(date +%Y%m%d-%H%M%S)"
SIGNAL="${1:-all}"
LOG="$LOG_DIR/${SIGNAL}-${TS}.log"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting signal: $SIGNAL" | tee "$LOG"

cd "$PIPELINE_DIR"

case "$SIGNAL" in
    monthly)
        # BLS LAUS — new data ~4 weeks after reference month
        $CMD bls-laus 2>&1 | tee -a "$LOG"
        ;;
    quarterly)
        # FEC bulk contributions — released per election cycle quarter
        $CMD fec 2>&1 | tee -a "$LOG"
        ;;
    annual)
        # ACS 5-year + CBP + SVI — released in fall/winter
        $CMD acs 2>&1 | tee -a "$LOG"
        $CMD cbp 2>&1 | tee -a "$LOG"
        $CMD svi 2>&1 | tee -a "$LOG"
        $CMD cdc-places 2>&1 | tee -a "$LOG"
        ;;
    weekly)
        # Building permits via Socrata — cities update weekly or daily
        $CMD permits 2>&1 | tee -a "$LOG"
        # NOAA climate observations — GSOM lags 1 month
        $CMD noaa 2>&1 | tee -a "$LOG"
        ;;
    static)
        # One-time / rarely changing — run manually or on schema deploy
        $CMD schema 2>&1 | tee -a "$LOG"
        $CMD geonames 2>&1 | tee -a "$LOG"
        $CMD tiger 2>&1 | tee -a "$LOG"
        ;;
    all)
        $CMD all 2>&1 | tee -a "$LOG"
        ;;
    *)
        echo "Unknown signal: $SIGNAL" >&2
        echo "Valid: monthly, quarterly, annual, weekly, static, all" >&2
        exit 1
        ;;
esac

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Done: $SIGNAL" | tee -a "$LOG"

# Prune logs older than 90 days
find "$LOG_DIR" -name "*.log" -mtime +90 -delete 2>/dev/null || true
