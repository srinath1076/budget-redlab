#!/usr/bin/env python3
"""RedLab cloud client — talks to the operator's private RunPod vLLM endpoint (pod or serverless).

Stdlib only (urllib/json) so there is nothing to pip-install. The endpoint is
OpenAI-compatible; we stream chat completions and keep multi-turn history in memory.
Secrets come from the environment (loaded by redlabLLM.sh from an untracked .env),
never from the command line or the repo.
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

RUNPOD_API_BASE = "https://api.runpod.ai/v2"
RUNPOD_GRAPHQL = "https://api.runpod.io/graphql"

# RunPod's Cloudflare edge 403s (error 1010) the default "Python-urllib" User-Agent.
# Any non-flagged UA passes; set it on every request.
USER_AGENT = "redlab-cloud/1.0"


def _env(name, default=None, required=False):
    val = os.environ.get(name, default)
    if required and not val:
        sys.exit(f"redlab-cloud: missing required env var {name} (set it in .env)")
    return val


def client_balance(api_key):
    """Return the account's remaining credit in USD (float), or None if unavailable."""
    body = json.dumps({"query": "query { myself { clientBalance } }"}).encode()
    req = urllib.request.Request(
        RUNPOD_GRAPHQL,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}",
                 "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
        return float(data["data"]["myself"]["clientBalance"])
    except (urllib.error.URLError, KeyError, TypeError, ValueError):
        return None


def stream_chat(base_url, auth_key, model, messages, sampling, max_tokens, timeout):
    """POST to the OpenAI-compatible endpoint and yield text chunks as they stream."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
        **sampling,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {auth_key}",
                 "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                return
            try:
                obj = json.loads(data)
                delta = obj["choices"][0]["delta"].get("content")
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
            if delta:
                yield delta


def _system_message(sys_path):
    with open(sys_path, "r", encoding="utf-8") as f:
        return {"role": "system", "content": f.read().strip()}


def _run_turn(args, sampling, messages):
    """Stream one assistant turn to stdout; return the full assistant text."""
    parts = []
    try:
        for chunk in stream_chat(
            args.base_url, args.auth_key, args.model, messages,
            sampling, args.max_tokens, args.timeout,
        ):
            parts.append(chunk)
            sys.stdout.write(chunk)
            sys.stdout.flush()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        sys.exit(f"\nredlab-cloud: HTTP {e.code} from endpoint — {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"\nredlab-cloud: could not reach endpoint — {e.reason}")
    sys.stdout.write("\n")
    return "".join(parts)


def main():
    p = argparse.ArgumentParser(description="RedLab cloud client (RunPod serverless).")
    p.add_argument("--ask", metavar="TEXT", help="one-shot question, then exit")
    p.add_argument("--smoke", action="store_true", help="prove the endpoint answers + show cold-start time and balance")
    p.add_argument("--balance", action="store_true", help="print remaining RunPod credit and exit")
    p.add_argument("--max-tokens", type=int, default=int(_env("REDLAB_MAX_TOKENS", "4096")))
    p.add_argument("--timeout", type=int, default=int(_env("REDLAB_TIMEOUT", "600")),
                   help="per-request timeout secs (first call after idle pays cold start)")
    args = p.parse_args()

    # Account API key — used for the balance/cost check (works for both pods and serverless).
    args.api_key = _env("RUNPOD_API_KEY", required=True)

    # Balance only needs the account key — answer it before requiring an inference endpoint,
    # so you can check credit with no pod up.
    if args.balance:
        bal = client_balance(args.api_key)
        print(f"RunPod balance: ${bal:.2f}" if bal is not None else "RunPod balance: unavailable")
        return

    # Inference endpoint: either a serverless endpoint id (we build the RunPod URL) or an
    # explicit base URL up to /v1 (e.g. a pod at https://<pod-id>-30000.proxy.runpod.net/v1).
    base_url = _env("REDLAB_BASE_URL")
    endpoint_id = _env("RUNPOD_ENDPOINT_ID")
    if base_url:
        args.base_url = base_url.rstrip("/")
    elif endpoint_id:
        args.base_url = f"{RUNPOD_API_BASE}/{endpoint_id}/openai/v1"
    else:
        sys.exit("redlab-cloud: set RUNPOD_ENDPOINT_ID (serverless) or REDLAB_BASE_URL (pod) in .env")

    # Auth for the inference call: a pod requires the bearer key `redlab-pod.sh up` set as
    # VLLM_API_KEY (mirrored here as REDLAB_INFERENCE_KEY); serverless uses the account key.
    # Defaults to the account key, which is harmless if the endpoint doesn't check it.
    args.auth_key = _env("REDLAB_INFERENCE_KEY", default=args.api_key)
    args.model = _env("REDLAB_MODEL", required=True)
    sys_path = _env("REDLAB_SYSTEM_PROMPT", required=True)

    sampling = {
        "temperature": float(_env("REDLAB_TEMP", "0.6")),
        "top_p": float(_env("REDLAB_TOP_P", "0.95")),
    }

    if args.smoke:
        bal = client_balance(args.api_key)
        print(f"[smoke] balance before: ${bal:.2f}" if bal is not None else "[smoke] balance: unavailable")
        print(f"[smoke] model={args.model}  endpoint={args.base_url}")
        print("[smoke] sending marker (first call after idle pays the cold start)...")
        t0 = time.time()
        msgs = [{"role": "user", "content": "Reply with exactly: REDLAB_CLOUD_OK"}]
        out = _run_turn(args, {"temperature": 0.0, "top_p": 1.0}, msgs)
        dt = time.time() - t0
        ok = "REDLAB_CLOUD_OK" in out
        print(f"[smoke] round-trip: {dt:.1f}s   result: {'OK' if ok else 'UNEXPECTED'}")
        bal2 = client_balance(args.api_key)
        if bal is not None and bal2 is not None:
            print(f"[smoke] balance after: ${bal2:.2f}  (this call cost ~${bal - bal2:.4f})")
        print("[smoke] reminder: the worker scales to zero on its own after the idle timeout.")
        sys.exit(0 if ok else 1)

    messages = [_system_message(sys_path)]

    if args.ask:
        messages.append({"role": "user", "content": args.ask})
        _run_turn(args, sampling, messages)
        return

    # Interactive REPL
    bal = client_balance(args.api_key)
    banner = f"  (balance ${bal:.2f})" if bal is not None else ""
    print(f"RedLab cloud — {args.model}{banner}")
    print("Authorized-red-team companion. Ctrl-D or 'exit' to quit; first reply pays cold start.\n")
    while True:
        try:
            user = input("you> ").strip()
        except EOFError:
            print()
            break
        if user in ("exit", "quit"):
            break
        if not user:
            continue
        messages.append({"role": "user", "content": user})
        sys.stdout.write("redlab> ")
        sys.stdout.flush()
        reply = _run_turn(args, sampling, messages)
        messages.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()
