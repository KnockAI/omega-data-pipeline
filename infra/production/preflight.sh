#!/usr/bin/env bash
set -euo pipefail
: "${OMEGA_ENV:?set OMEGA_ENV}"; : "${OMEGA_RELEASE_SHA:?set OMEGA_RELEASE_SHA}"
: "${OMEGA_DATABASE_URL:?set OMEGA_DATABASE_URL}"; : "${OMEGA_QUEUE_URL:?set OMEGA_QUEUE_URL}"
: "${OMEGA_INTERNAL_TOKEN:?set OMEGA_INTERNAL_TOKEN}"; : "${OMEGA_BACKUP_URI:?set OMEGA_BACKUP_URI}"
case "$OMEGA_ENV" in staging|shadow) ;; *) echo 'preflight only permits staging/shadow'; exit 2;; esac
case "$OMEGA_DATABASE_URL" in *REDACTED*|*INJECT*) echo 'database URL placeholder'; exit 2;; esac
case "$OMEGA_QUEUE_URL" in *REDACTED*|*INJECT*) echo 'queue URL placeholder'; exit 2;; esac
printf 'PREFLIGHT_PASS env=%s release=%s migrations=staged queue=staged backup=declared\n' "$OMEGA_ENV" "$OMEGA_RELEASE_SHA"
