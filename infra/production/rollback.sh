#!/usr/bin/env bash
set -euo pipefail
if [[ "${AUTHORIZED_ROLLBACK:-}" != "YES" ]]; then
  echo 'ROLLBACK_GUARDED: set AUTHORIZED_ROLLBACK=YES after founder authorization'; exit 2
fi
: "${OMEGA_PREVIOUS_RELEASE_SHA:?set previous release SHA}"
echo "ROLLBACK_COMMAND_READY release=$OMEGA_PREVIOUS_RELEASE_SHA"
