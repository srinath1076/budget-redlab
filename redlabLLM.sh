#!/bin/bash
# RedLab Cloud — on-demand authorized-red-team companion on a private RunPod endpoint.
#
# A large uncensored reasoner without a refusal boundary, on hardware you rent by the
# second and hard-cap at $20/month. Nothing here downloads weights or binds a local port;
# it talks to YOUR RunPod vLLM endpoint, which is torn down to $0 when you're done.
#
# Setup once: see docs/RUNPOD_RUNBOOK.md, then fill in .env (copy from .env.example).
#
# Usage:
#   ./redlabLLM.sh                  # interactive chat (Ctrl-D to exit)
#   ./redlabLLM.sh --ask "..."      # one-shot answer
#   ./redlabLLM.sh --smoke          # prove endpoint answers + show cold-start + cost
#   ./redlabLLM.sh --balance        # print remaining RunPod credit
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ENV_FILE="$SCRIPT_DIR/.env"

[ -f "$ENV_FILE" ] || { echo "Missing $ENV_FILE — copy .env.example to .env and fill it in (see docs/RUNPOD_RUNBOOK.md)." >&2; exit 1; }
# Load .env (untracked) into the environment. Lines are KEY=VALUE; comments/blank lines ignored.
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

# Default the model + system prompt here so .env only needs the secrets.
export REDLAB_MODEL="${REDLAB_MODEL:-cyankiwi/Qwen3-Next-80B-A3B-Instruct-AWQ-4bit}"
export REDLAB_SYSTEM_PROMPT="${REDLAB_SYSTEM_PROMPT:-$SCRIPT_DIR/prompts/ethical-hacker.cloud.system.md}"

[ -n "${RUNPOD_API_KEY:-}" ] || { echo "RUNPOD_API_KEY not set in .env" >&2; exit 1; }
command -v python3 >/dev/null    || { echo "python3 is required" >&2; exit 1; }

# --balance only needs the account key (no pod). Everything else needs an inference endpoint.
case " $* " in
  *" --balance "*) : ;;
  *)
    [ -n "${RUNPOD_ENDPOINT_ID:-}${REDLAB_BASE_URL:-}" ] || { echo "Set RUNPOD_ENDPOINT_ID (serverless) or REDLAB_BASE_URL (pod) in .env — or run ./redlab-pod.sh up" >&2; exit 1; }
    [ -f "$REDLAB_SYSTEM_PROMPT" ] || { echo "System prompt not found: $REDLAB_SYSTEM_PROMPT" >&2; exit 1; }
    ;;
esac

exec python3 "$SCRIPT_DIR/redlab_cloud.py" "$@"
