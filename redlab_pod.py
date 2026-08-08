#!/usr/bin/env python3
"""RedLab pod lifecycle — create / verify / terminate the RunPod GPU pod, by script.

Stdlib only (urllib/json/re). Talks to the RunPod REST API (rest.runpod.io/v1) for
pod CRUD, and the GraphQL API for account balance + GPU-type discovery. The deploy
spec lives in config/cloud.json under "deploy" so the pod is reproducible and
reviewable in git — nothing about the machine is typed by hand at deploy time.

The whole point of this tool is a *guaranteed* budget floor:
  - `up`     creates the pod, waits until the model serves, writes REDLAB_BASE_URL.
  - `status` shows EVERY pod + total $/hr burn + balance; --assert-idle exits 1 if
             anything is running (a scriptable "verify nothing is running" check).
  - `down`   terminates the RedLab pod, then re-lists to PROVE it's gone ($0 idle).

Secrets come from the environment (loaded by redlab-pod.sh from an untracked .env).

Usage:
  ./redlab-pod.sh up [--no-wait] [--wait SECS]
  ./redlab-pod.sh status [--assert-idle]
  ./redlab-pod.sh down [--all] [--yes]
  ./redlab-pod.sh gpus
  ./redlab-pod.sh url
"""
import argparse
import json
import os
import re
import secrets
import sys
import time
import urllib.request
import urllib.error

REST_BASE = "https://rest.runpod.io/v1"
GRAPHQL = "https://api.runpod.io/graphql"

# RunPod's GraphQL host sits behind Cloudflare, which 403s (error 1010) the default
# "Python-urllib/x.y" User-Agent as a known-bot signature. Any non-flagged UA passes.
USER_AGENT = "redlab-cloud/1.0"

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(REPO_DIR, ".env")
CONFIG_PATH = os.path.join(REPO_DIR, "config", "cloud.json")

# Only these keys are forwarded to POST /pods; anything else in the deploy block
# (notes, http_port, etc.) is for humans / this tool, not the API.
CREATE_FIELDS = {
    "name", "imageName", "gpuTypeIds", "gpuCount", "gpuTypePriority",
    "containerDiskInGb", "volumeInGb", "volumeMountPath", "ports", "env",
    "cloudType", "dataCenterIds", "dataCenterPriority", "supportPublicIp",
    "networkVolumeId", "templateId", "dockerStartCmd", "dockerEntrypoint",
    "allowedCudaVersions", "minRAMPerGPU", "minVCPUPerGPU",
}


# ---------------------------------------------------------------- HTTP helpers

def _rest(method, path, api_key, body=None):
    url = f"{REST_BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:600]
        sys.exit(f"redlab-pod: HTTP {e.code} {method} {path} — {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"redlab-pod: cannot reach RunPod REST API — {e.reason}")


def _graphql(api_key, query):
    body = json.dumps({"query": query}).encode()
    req = urllib.request.Request(
        GRAPHQL, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}",
                 "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except (urllib.error.URLError, ValueError):
        return None


def client_balance(api_key):
    data = _graphql(api_key, "query { myself { clientBalance } }")
    try:
        return float(data["data"]["myself"]["clientBalance"])
    except (TypeError, KeyError, ValueError):
        return None


def gpu_types(api_key):
    data = _graphql(api_key, "query { gpuTypes { id displayName memoryInGb } }")
    try:
        return data["data"]["gpuTypes"]
    except (TypeError, KeyError):
        return []


# ---------------------------------------------------------------- pod CRUD

def list_pods(api_key):
    res = _rest("GET", "/pods", api_key)
    if isinstance(res, list):
        return res
    if isinstance(res, dict):
        for k in ("pods", "data", "items"):
            if isinstance(res.get(k), list):
                return res[k]
    return []


def get_pod(api_key, pod_id):
    return _rest("GET", f"/pods/{pod_id}", api_key)


def create_pod(api_key, spec):
    return _rest("POST", "/pods", api_key, spec)


def terminate_pod(api_key, pod_id):
    _rest("DELETE", f"/pods/{pod_id}", api_key)


def _cost(pod):
    for k in ("adjustedCostPerHr", "costPerHr"):
        v = pod.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return 0.0


def _alive(pod):
    """A pod that still costs money (running or stopped) — TERMINATED is gone."""
    return str(pod.get("desiredStatus", "")).upper() != "TERMINATED"


def _running(pod):
    return str(pod.get("desiredStatus", "")).upper() == "RUNNING"


# ---------------------------------------------------------------- config + .env

def load_deploy():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        sys.exit(f"redlab-pod: cannot read {CONFIG_PATH}: {e}")
    deploy = cfg.get("deploy")
    if not isinstance(deploy, dict):
        sys.exit("redlab-pod: no 'deploy' block in config/cloud.json")
    return deploy


def build_spec(deploy):
    spec = {k: v for k, v in deploy.items() if k in CREATE_FIELDS}
    if not spec.get("imageName"):
        sys.exit("redlab-pod: deploy.imageName is required in config/cloud.json")
    if not spec.get("gpuTypeIds"):
        sys.exit("redlab-pod: deploy.gpuTypeIds is required in config/cloud.json")
    return spec


def ensure_inference_key():
    """Return the vLLM bearer key, generating + persisting one if absent.

    The RunPod proxy URL is public HTTPS, so an un-authenticated vLLM server is
    open to anyone who learns the pod id. We close that by requiring a bearer
    token: the key is passed to the pod as VLLM_API_KEY (vLLM then rejects any
    request without `Authorization: Bearer <key>`), and the client reads the same
    value from REDLAB_INFERENCE_KEY. The secret lives ONLY in the untracked .env
    and the pod's runtime env — never in committed config. If .env has no key we
    mint one so auth is on by default with nothing to hand-manage.
    """
    key = os.environ.get("REDLAB_INFERENCE_KEY")
    if not key:
        key = "redlab-" + secrets.token_urlsafe(24)
        update_env({"REDLAB_INFERENCE_KEY": key})
        os.environ["REDLAB_INFERENCE_KEY"] = key
        print("  generated a vLLM API key -> REDLAB_INFERENCE_KEY in .env "
              "(the server now requires it; the client picks it up automatically)")
    return key


def http_port(deploy):
    if deploy.get("http_port"):
        return int(deploy["http_port"])
    for p in deploy.get("ports", []):
        if str(p).endswith("/http"):
            return int(str(p).split("/", 1)[0])
    return 30000


def proxy_base(pod_id, port):
    return f"https://{pod_id}-{port}.proxy.runpod.net"


def update_env(updates):
    """Insert/replace/remove KEY=VALUE lines in .env, preserving everything else.

    A value of None removes the key. Creates .env if absent.
    """
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    seen = set()
    out = []
    for ln in lines:
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)=", ln)
        if m and m.group(1) in updates:
            key = m.group(1)
            seen.add(key)
            if updates[key] is None:
                continue
            out.append(f"{key}={updates[key]}")
        else:
            out.append(ln)
    for key, val in updates.items():
        if key not in seen and val is not None:
            out.append(f"{key}={val}")
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


# ---------------------------------------------------------------- health poll

def _get_json(url, auth_key, timeout=15):
    headers = {"User-Agent": USER_AGENT}
    if auth_key:
        headers["Authorization"] = f"Bearer {auth_key}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def wait_healthy(base_url, auth_key, timeout_s):
    """Poll the pod's /v1/models until it 200s (model loaded) or we time out."""
    url = f"{base_url}/v1/models"
    deadline = time.time() + timeout_s
    start = time.time()
    while time.time() < deadline:
        try:
            status, _ = _get_json(url, auth_key)
            if status == 200:
                return True
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            pass
        el = int(time.time() - start)
        sys.stdout.write(f"\r  loading model… {el:4d}s (first boot pulls ~46GB, be patient)")
        sys.stdout.flush()
        time.sleep(10)
    sys.stdout.write("\n")
    return False


def wait_running(api_key, pod_id, timeout_s=300):
    """Poll pod status until a machine is assigned and it's RUNNING."""
    deadline = time.time() + timeout_s
    start = time.time()
    while time.time() < deadline:
        pod = get_pod(api_key, pod_id)
        if _running(pod):
            sys.stdout.write("\n")
            return pod
        el = int(time.time() - start)
        sys.stdout.write(f"\r  waiting for a machine… {el:4d}s (status={pod.get('desiredStatus')})")
        sys.stdout.flush()
        time.sleep(6)
    sys.stdout.write("\n")
    return None


# ---------------------------------------------------------------- commands

def cmd_gpus(api_key, _args, _deploy):
    types = gpu_types(api_key)
    if not types:
        sys.exit("redlab-pod: could not list GPU types (check RUNPOD_API_KEY).")
    types.sort(key=lambda t: (t.get("memoryInGb") or 0))
    print("GPU type ids (use in config/cloud.json -> deploy.gpuTypeIds):\n")
    for t in types:
        mem = t.get("memoryInGb")
        print(f"  {mem:>4} GB   {t.get('id')}")
    print("\n80GB target for the AWQ 4-bit + 128K KV cache: \"NVIDIA A100 80GB PCIe\".")


def cmd_status(api_key, args, deploy):
    pods = list_pods(api_key)
    alive = [p for p in pods if _alive(p)]
    running = [p for p in pods if _running(p)]
    bal = client_balance(api_key)

    if not alive:
        print("No pods on the account. Nothing is billing.  ✅")
    else:
        print(f"{'STATUS':<10} {'$/hr':>7}  {'GPU':<26} {'NAME':<16} ID")
        for p in alive:
            gpu = ""
            g = p.get("gpu") or {}
            gpu = g.get("displayName") or (p.get("gpuTypeIds") or [""])[0] or ""
            mark = "  <- redlab" if p.get("name") == deploy.get("name") else ""
            print(f"{p.get('desiredStatus',''):<10} {_cost(p):>7.2f}  {gpu[:26]:<26} "
                  f"{str(p.get('name',''))[:16]:<16} {p.get('id','')}{mark}")
        burn = sum(_cost(p) for p in running)
        print(f"\n  RUNNING pods: {len(running)}   burn: ${burn:.2f}/hr")

    if bal is not None:
        print(f"  balance: ${bal:.2f}")

    if args.assert_idle:
        if running:
            print(f"\n[assert-idle] FAIL — {len(running)} pod(s) running.", file=sys.stderr)
            sys.exit(1)
        print("\n[assert-idle] OK — nothing is running.")
    return 0


def cmd_up(api_key, args, deploy):
    port = http_port(deploy)
    pods = list_pods(api_key)

    existing = [p for p in pods if p.get("name") == deploy.get("name") and _alive(p)]
    if existing:
        pod = existing[0]
        print(f"Reusing existing RedLab pod {pod['id']} (status={pod.get('desiredStatus')}).")
        print("If you meant a fresh one, run `./redlab-pod.sh down` first.")
    else:
        others = [p for p in pods if _running(p)]
        if others:
            burn = sum(_cost(p) for p in others)
            print(f"⚠  {len(others)} other pod(s) already running (${burn:.2f}/hr). "
                  "Run `./redlab-pod.sh status` to review.\n")
        bal = client_balance(api_key)
        if bal is not None and bal <= 0.5:
            sys.exit(f"redlab-pod: balance ${bal:.2f} is too low to start a pod. Load credit first.")
        spec = build_spec(deploy)
        # Require bearer auth on the vLLM server so the public proxy URL isn't open.
        auth_key = ensure_inference_key()
        env = dict(spec.get("env") or {})
        env["VLLM_API_KEY"] = auth_key
        # Optional HF token — REQUIRED for gated/private weights, and lifts anonymous
        # download rate limits. Injected from .env only; never stored in committed config.
        hf_token = os.environ.get("REDLAB_HF_TOKEN") or os.environ.get("HF_TOKEN")
        if hf_token:
            env["HF_TOKEN"] = hf_token
            print("  HF token found -> injected as HF_TOKEN (gated/private weights + higher pull limits)")
        spec["env"] = env
        print(f"Creating pod '{spec.get('name')}' on {spec['gpuTypeIds']} "
              f"({spec.get('cloudType','SECURE')})…")
        pod = create_pod(api_key, spec)
        if not pod.get("id"):
            sys.exit(f"redlab-pod: create returned no id: {json.dumps(pod)[:400]}")
        print(f"  created: {pod['id']}")

    pod_id = pod["id"]
    base = proxy_base(pod_id, port)
    # Write .env now so `url` / the client resolve even while it's still booting.
    update_env({"REDLAB_POD_ID": pod_id, "REDLAB_BASE_URL": f"{base}/v1"})
    print(f"  base URL: {base}/v1   (written to .env)")

    if args.no_wait:
        print("\n--no-wait: not blocking. Check `./redlab-pod.sh status` and the pod logs "
              "in the console; then `./redlabLLM.sh --smoke`.")
        return 0

    if not _running(pod):
        print("\nWaiting for the pod to start:")
        pod = wait_running(api_key, pod_id)
        if pod is None:
            print("Pod did not reach RUNNING (no machine?). Check availability with "
                  "`./redlab-pod.sh gpus` or try another data center / GPU.", file=sys.stderr)
            return 1

    auth_key = os.environ.get("REDLAB_INFERENCE_KEY", "")
    print("\nWaiting for the model server (this is the slow part on a cold pod):")
    ok = wait_healthy(base, auth_key, args.wait)
    print()
    if ok:
        print(f"✅ Serving. Next: ./redlabLLM.sh --smoke")
        print(f"   When you're done:  ./redlab-pod.sh down   (terminates → $0 idle)")
        return 0
    print(f"⚠  Not serving yet after {args.wait}s. It may still be loading — watch the pod "
          "logs in the console, then `./redlabLLM.sh --smoke`.", file=sys.stderr)
    print(f"   base URL is already in .env: {base}/v1", file=sys.stderr)
    return 1


def cmd_down(api_key, args, deploy):
    pods = list_pods(api_key)
    if args.all:
        targets = [p for p in pods if _alive(p)]
        scope = "ALL pods"
    else:
        targets = [p for p in pods if p.get("name") == deploy.get("name") and _alive(p)]
        scope = f"pod(s) named '{deploy.get('name')}'"

    if not targets:
        print(f"Nothing to terminate ({scope}).")
    else:
        print(f"About to TERMINATE {len(targets)} {scope}:")
        for p in targets:
            print(f"  {p.get('id')}  {p.get('name','')}  ({p.get('desiredStatus')}, ${_cost(p):.2f}/hr)")
        if not args.yes:
            try:
                if input("Type 'yes' to terminate (deletes their disks): ").strip() != "yes":
                    print("Aborted.")
                    return 1
            except EOFError:
                print("Aborted (no confirmation).")
                return 1
        for p in targets:
            terminate_pod(api_key, p["id"])
            print(f"  terminated {p['id']}")

    # Verify: re-list and prove nothing is running.
    time.sleep(3)
    after = list_pods(api_key)
    still_alive = [p for p in after if _alive(p)]
    still_running = [p for p in after if _running(p)]

    # Only clear .env if OUR pod is actually gone.
    ours_left = [p for p in still_alive if p.get("name") == deploy.get("name")]
    if not ours_left:
        update_env({"REDLAB_BASE_URL": None, "REDLAB_POD_ID": None})

    if not still_alive:
        print("\n✅ Verified: no pods remain. Standing cost is $0.")
        return 0
    print(f"\n⚠  {len(still_running)} pod(s) still running, {len(still_alive)} still alive:")
    for p in still_alive:
        print(f"  {p.get('id')}  {p.get('name','')}  ({p.get('desiredStatus')}, ${_cost(p):.2f}/hr)")
    print("Run `./redlab-pod.sh down --all` to clear everything.", file=sys.stderr)
    return 1


def cmd_url(_api_key, _args, deploy):
    base = os.environ.get("REDLAB_BASE_URL")
    pod_id = os.environ.get("REDLAB_POD_ID")
    if base:
        print(base)
        if pod_id:
            print(f"# pod {pod_id}", file=sys.stderr)
        return 0
    print("No REDLAB_BASE_URL in .env — no pod is wired up. Run `./redlab-pod.sh up`.",
          file=sys.stderr)
    return 1


def main():
    p = argparse.ArgumentParser(description="RedLab pod lifecycle (RunPod REST API).")
    sub = p.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("up", help="create the pod, wait until it serves, write .env")
    up.add_argument("--no-wait", action="store_true", help="create and return; don't block on boot")
    up.add_argument("--wait", type=int, default=1500, help="max secs to wait for the model to serve")

    st = sub.add_parser("status", help="list all pods + burn + balance")
    st.add_argument("--assert-idle", action="store_true", help="exit 1 if any pod is running")

    dn = sub.add_parser("down", help="terminate the RedLab pod, then verify nothing runs")
    dn.add_argument("--all", action="store_true", help="terminate EVERY pod on the account")
    dn.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    sub.add_parser("gpus", help="list available GPU type ids")
    sub.add_parser("url", help="print the current pod's OpenAI base URL")

    args = p.parse_args()
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        sys.exit("redlab-pod: RUNPOD_API_KEY not set (put it in .env).")
    deploy = load_deploy()

    handlers = {
        "up": cmd_up, "status": cmd_status, "down": cmd_down,
        "gpus": cmd_gpus, "url": cmd_url,
    }
    rc = handlers[args.cmd](api_key, args, deploy)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
