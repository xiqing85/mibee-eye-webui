#!/usr/bin/env bash
# Verify the shared frontend copies in the three device repos match the
# mibee-webui source of truth. Exit 1 with a diff summary on drift.
set -euo pipefail
cd "$(dirname "$0")/.."
fail=0
check() {
  local name=$1 dir=$2
  if [ ! -d "$dir" ]; then
    echo "SKIP $name ($dir missing)"
    return
  fi
  if diff -r -q static/ "$dir" > /dev/null 2>&1; then
    echo "OK   $name"
  else
    echo "DRIFT $name:"
    diff -r -q static/ "$dir" | sed 's/^/      /'
    fail=1
  fi
}
check mibee-eye-rs ../mibee-eye-rs/static
check mibee-eye-go ../mibee-eye-go/internal/web/static
check notebook-cam     ../notebook-cam/crates/web/static
exit $fail
