#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "${1:-}" == "--execute" ]]; then
  shift
  if [[ $# -lt 3 ]]; then
    echo 'Usage: training/launch.sh --execute FAMILY PHASE UNIQUE_RUN_ID [TIMEOUT_SECONDS] [PARENT_RUN_ID]' >&2
    exit 2
  fi
  exec modal run training/modal_app.py --family "$1" --phase "$2" --run-id "$3" --timeout-seconds "${4:-900}" --parent-run "${5:-}"
fi
if [[ "${1:-}" == "--dry-run" ]]; then shift; fi
exec python3 training/plan.py "${1:-nanochat}" "${2:-pilot}" "${3:-rhysbot-dry-run}" "${@:4}"
