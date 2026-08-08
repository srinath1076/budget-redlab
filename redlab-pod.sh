#!/bin/bash
# RedLab pod lifecycle — bring the RunPod GPU pod up, verify it, and tear it down,
# entirely by script. The deploy spec lives in config/cloud.json ("deploy" block);
# this wrapper just loads secrets from the untracked .env and runs the manager.
#
# The budget guarantee lives here: `down` terminates AND proves nothing is running,
# and `status --assert-idle` exits non-zero if anything is still billing.
#
# Setup once: fill .env (copy from .env.example) with RUNPOD_API_KEY. Then:
#   ./redlab-pod.sh up            # create pod, wait until it serves, write REDLAB_BASE_URL
#   ./redlab-pod.sh status        # list every pod + $/hr burn + balance
#   ./redlab-pod.sh status --assert-idle   # exit 1 if anything is running (for cron/CI)
#   ./redlab-pod.sh down          # terminate the RedLab pod, then verify $0 idle
#   ./redlab-pod.sh down --all    # panic button: terminate EVERY pod on the account
#   ./redlab-pod.sh gpus          # list GPU type ids to put in config/cloud.json
#   ./redlab-pod.sh url           # print the current pod's OpenAI base URL
#
# Typical session:  ./redlab-pod.sh up  &&  ./redlabLLM.sh  ;  ./redlab-pod.sh down
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ENV_FILE="$SCRIPT_DIR/.env"

[ -f "$ENV_FILE" ] || { echo "Missing $ENV_FILE — copy .env.example to .env and fill in RUNPOD_API_KEY (see docs/RUNPOD_RUNBOOK.md)." >&2; exit 1; }
# Load .env (untracked) into the environment. KEY=VALUE lines; comments/blank ignored.
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

[ -n "${RUNPOD_API_KEY:-}" ] || { echo "RUNPOD_API_KEY not set in .env" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }

exec python3 "$SCRIPT_DIR/redlab_pod.py" "$@"
