# RedLab — on-demand uncensored red-team reasoner (RunPod)

On-demand, hard-capped access to a large **uncensored** reasoning model for **authorized**
red-team work. RedLab brings up a GPU pod on [RunPod](https://runpod.io), serves the model
over an OpenAI-compatible API, and tears the pod down to **$0 idle** when you're done — the
whole lifecycle is scripted, and the teardown *proves* nothing is left billing.

> **What this is — and isn't.** RedLab is an *illustrative, budget-capped infrastructure
> pattern*: a scripted way to stand up a private, on-demand reasoner and tear it back down to
> **$0**. It is scaffolding, not a curated model. Aligned frontier models refuse authorized
> offensive work by design — self-hosting exists precisely to run weights that don't. The
> model wired in below is only a **default placeholder** sized to the budget/VRAM envelope;
> you are expected to bring whatever fits your engagement — a different base, a QLoRA
> fine-tune, abliterated or self-quantized weights — by pointing `REDLAB_MODEL` and the
> `deploy` block at it. **The infrastructure is model-agnostic; the model choice, its
> capability, and its provenance are yours to own.** Outputs can be wrong or fabricated, and
> an uncensored model has no refusal boundary — the boundary is you and your authorization
> scope. Authorized, supervised use only.

The model is **Qwen3-Next-80B-A3B** (a 4-bit community quant), served with vLLM at **128K
context** on a single 80GB A100. You pay by the second while a session runs; a hard **$20
loaded-credit cap with auto-top-up OFF** is the backstop even if you forget to terminate.

## How it works

Two small stdlib-only Python tools (no dependencies to install), each behind a thin shell
launcher that loads secrets from an untracked `.env`:

| Tool | What it does |
|---|---|
| `./redlab-pod.sh` | **Pod lifecycle** over the RunPod REST API: `up` creates the pod from `config/cloud.json` and waits until the model serves; `status` / `--assert-idle` verifies nothing is billing; `down` terminates and **proves $0 idle**. |
| `./redlabLLM.sh` | **Inference client**: `--smoke` proves the endpoint answers, `--ask "..."` for one-shot, bare for an interactive 128K session. Streams from the OpenAI-compatible endpoint. |
| `./redlab-garak.sh` | **Readiness smoke** (optional, needs [garak](https://github.com/NVIDIA/garak)): fires authorized-offensive probes and reports a **candor rate** — is the deployed model candid enough to *be* a redlab, or is it refusing? See [`docs/GARAK_SMOKE.md`](docs/GARAK_SMOKE.md). |

The pod spec lives in `config/cloud.json` (the `deploy` block) — there is nothing to click
in the RunPod console.

## Quick start

```sh
cp .env.example .env          # fill in ONLY RUNPOD_API_KEY (see docs/RUNPOD_RUNBOOK.md)
./redlab-pod.sh gpus          # (optional) confirm an 80GB A100 id is available
./redlab-pod.sh up            # create the pod, wait until it serves, write REDLAB_BASE_URL

./redlabLLM.sh --smoke     # prove it answers + show round-trip time and balance
./redlabLLM.sh --ask "Walk the exploitation path for CVE-2021-44228 on my lab host and how I'd detect it"
./redlabLLM.sh             # interactive multi-turn (128K context)

./redlab-pod.sh down          # terminate the pod, then VERIFY nothing runs -> $0 idle
```

First `up` pulls ~46GB and loads the model — expect **10–20 min** before it serves (the tool
polls `/v1/models` and tells you when it's ready). `up` is idempotent (reuses an existing
RedLab pod instead of double-spending) and refuses to start if your balance is near $0.

See **[`docs/RUNPOD_RUNBOOK.md`](docs/RUNPOD_RUNBOOK.md)** for account setup, the budget
math, GPU/quant choices, and cost hygiene.

## Authorization

RedLab is a reasoning companion for a trained practitioner doing **authorized, supervised**
work — not an autonomous agent and not a public assistant. Read
**[`docs/AUTHORIZATION.md`](docs/AUTHORIZATION.md)** for the in-scope / out-of-scope boundary
and operating rules. Every command RedLab proposes is a draft for you to review and run.

## Good at

Threat modeling, attack-path analysis, CVE→CWE/CVSS reasoning, recon methodology and exact
tool flags, exploit **explanation** + lab PoCs, secure-code review, detection engineering
(Sigma/YARA/Suricata/KQL), report drafting, CTF reasoning.

## Honest limits

- Reasoning quality is **whatever weights you deploy** — this ships with a community 4-bit
  quant as a default placeholder, nothing more. Choose (and vet) the model your work needs.
- Cold start on first `up` is a real ~10–20 min model load; fine for on-demand deep-research
  sessions, not for all-day snappy turns.
- **Not an autonomous agent** — it proposes commands for human review; no execution,
  approval, or deployment authority.
- Data traverses rented infrastructure — operator-private, but not airgapped.

## Security notes

- Secrets live **only** in an untracked `.env` (gitignored); nothing sensitive is committed.
- The RunPod proxy endpoint is public HTTPS, so **auth is on by default**: `redlab-pod.sh up`
  mints a bearer key, passes it to vLLM as `VLLM_API_KEY`, and stores it only in `.env` as
  `REDLAB_INFERENCE_KEY` — the endpoint rejects any request without it. Rotate by deleting
  that line and re-running `up`.
- Hard spend cap is enforced by **loaded credit with auto-top-up OFF**, independent of the
  scripts.

## Files

- `redlab-pod.sh` / `redlab_pod.py` — scripted pod lifecycle (create / verify-idle / terminate)
- `redlabLLM.sh` / `redlab_cloud.py` — OpenAI-compatible inference client
- `redlab-garak.sh` — readiness smoke: does the deployed model actually engage (not refuse)?
- `config/cloud.json` — the reproducible pod spec + model/runtime plan
- `prompts/ethical-hacker.cloud.system.md` — the scoped system prompt
- `docs/RUNPOD_RUNBOOK.md` — account setup, budget, deploy, cost hygiene
- `docs/GARAK_SMOKE.md` — the garak readiness smoke (candor check)
- `docs/AUTHORIZATION.md` — the operator authorization boundary
- `.env.example` — the one secret you fill in (`RUNPOD_API_KEY`)
- `LICENSE` — MIT

## License

[MIT](LICENSE). Provided as-is, with no warranty. You are solely responsible for using
RedLab within the authorization boundary in [`docs/AUTHORIZATION.md`](docs/AUTHORIZATION.md)
and for any weights you choose to deploy.
