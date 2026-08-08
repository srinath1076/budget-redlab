# RedLab Cloud — RunPod runbook

On-demand, hard-capped ($20/month) access to a large uncensored reasoner for **authorized**
red-team work. An A100 pod you bring **up and down by script** (`./redlab-pod.sh up` /
`down`): you pay by the second while it runs, and terminate to $0 idle between sessions —
the teardown *proves* nothing is left billing.

> **What the $20 actually buys you.** A private, on-demand GPU you fully control — the freedom
> to run whatever weights your engagement needs (base, QLoRA, abliterated, self-quantized),
> without the refusal boundary an aligned frontier model enforces on authorized offensive work.
> This runbook uses a community 4-bit quant as a stand-in default; swap in your own model at any
> time. You are renting *capability you own*, not a smarter brain — spend accordingly.

---

## 1. The budget triangle (why the choices below are what they are)

You cannot have {capable 80B} + {$20 cap} + {50 always-on hours} at once. We resolve it by
**never running always-on**: `./redlab-pod.sh up` for a session, `./redlab-pod.sh down` to
terminate to $0 the moment you're done.

| GPU (Community) | VRAM | $/hr | Note |
|---|---|---|---|
| **A100 80GB** | 80GB | ~$1.39 (pod) / ~$2.72 (serverless) | Target. 4-bit AWQ (~45.9GB) + ~34GB free for a 128K KV cache. |
| 2× A40 48GB | 96GB | ~$0.88 / ~$2.44 | Alternative, `TP_SIZE=2`. Same VRAM budget, more config. |
| 1× A40 48GB | 48GB | ~$0.44 | **Do NOT use** — 45.9GB weights ≈ 96% of the card; won't hold real context and may not load. |

> **Quant vs GPU — get this right or it OOMs.** FP8 = ~80GB (needs 75GB+ VRAM, don't use it).
> 4-bit AWQ = ~45.9GB. That nearly fills a single 48GB card, leaving no room for the KV cache —
> so for any usable context (and certainly **128K**, the whole point of the cloud tier) you need
> **80GB total**: one A100 80GB (simplest) or 2× A40 with tensor parallel. Qwen3-Next's hybrid
> attention keeps the KV cache small (~¼ of layers are full-attention), so 128K fits the 80GB
> headroom comfortably.

**Standing cost:** a ~60GB network volume to hold the model ≈ **$4–7/month**, billed even when
the worker is at zero. Budget ~$13–15/month for actual compute.

**Rough compute you get for that:** an A40 forward pass on a 3B-active MoE is fast; a typical
multi-turn session is minutes of real GPU-seconds. Realistic: **dozens of sessions/month**
well under $20, *as long as workers scale to zero between them.*

---

## 2. The model — and the one real obstacle

You chose an **uncensored 80B** — but the stock model may already *be* uncensored enough.
In refusal testing, **stock Qwen3-Next-80B-A3B cooperated ~83% on BashInjector**
(command-injection / tool-abuse) prompts, while Claude Opus 4.6 and Gemini 3.1 Pro refused
~100%. So for authorized offensive work, **abliteration is likely unnecessary** — deploy the
stock AWQ and only revisit if a real task is actually refused.

The catch that only matters *if* you ever do need the abliterated weights:

- `cyankiwi/Qwen3-Next-80B-A3B-Instruct-AWQ-4bit` (~45.9GB) fits the A40 but is **safety-tuned**
  (and a *community* quant — vet it).
- `huihui-ai/Huihui-Qwen3-Next-80B-A3B-Thinking-abliterated` is uncensored but only **BF16
  (~160GB)** — does not fit a 48GB (or 80GB) card without its own 4-bit AWQ.
- **Uncensored ∩ fits-A40 is not a ready download** — but per the BashInjector evidence you
  likely won't need it.

**Recommended sequence (de-risks money before quantization):**

1. **Stand up the plumbing with the AWQ** (`cyankiwi/Qwen3-Next-80B-A3B-Instruct-AWQ-4bit`).
   Prove the endpoint, the `$20` cap, cold-start time, and `redlabLLM.sh` all work. The
   scoped system prompt makes this model fairly candid for authorized work already — it may
   be enough.
2. **If it refuses real authorized tasks,** get the abliterated weights in an A40-sized quant:
   - **Check first** for a community AWQ of `Huihui-Qwen3-Next-80B-A3B-Thinking-abliterated`
     (new quants appear constantly). If one exists and its provenance checks out, just point
     `REDLAB_MODEL` at it.
   - **Else quantize it yourself, once:** rent an A100-80GB pod for ~1hr (~$1.40), AWQ-4bit
     the BF16 checkpoint, push the ~46GB result to *your own private HF repo*. One-time
     ~$2–4; the weights are then reusable forever. Record the source repo + revision + your
     output repo + sha256 in `config/cloud.json`.

Whatever you deploy, set `REDLAB_MODEL` in `.env` to its HF id — no code change needed.

---

## 3. Account setup (do this once)

1. **Create the account** at runpod.io and verify email.
2. **Hard-cap the spend — this is the real ceiling:**
   - Billing → **load exactly $20** of credit.
   - Billing → **turn OFF auto top-up / auto-recharge.** When the balance hits $0, RunPod
     stops your workers. No auto-refill = no surprise bill.
   - (Optional) set a spend alert at ~$15.
3. **API key:** Settings → API Keys → **+ Create** (read/write). Copy it into `.env`.

## 4. Deploy by script (the whole lifecycle is `./redlab-pod.sh`)

There is **nothing to click in the console.** The pod spec lives in `config/cloud.json`
(the `deploy` block) and is applied over the RunPod REST API by `redlab_pod.py`. This is
the reproducible, budget-safe path: every session is create → use → terminate, and the
teardown *proves* nothing is left billing.

```sh
cp .env.example .env                 # fill in ONLY RUNPOD_API_KEY
./redlab-pod.sh gpus                 # (optional) confirm the A100 80GB id is available
./redlab-pod.sh up                   # create the A100 pod, wait until the model serves,
                                     #   and auto-write REDLAB_BASE_URL into .env
```

`up` is idempotent: if a `redlab-cloud` pod already exists it reuses it instead of
double-spending. First boot pulls ~46GB and loads the model — expect **10–20 min** before
it serves (the tool polls `/v1/models` and tells you when it's ready). It also warns if any
*other* pod is already running, and refuses to start if your balance is near $0.

What the `deploy` block pins (edit there, never in the console):
- **GPU:** `NVIDIA A100 80GB PCIe` — 80GB is required for the 4-bit AWQ (~45.9GB) **plus** a
  128K KV cache. A single 48GB card cannot hold both (see §1).
- **Serving:** `vllm/vllm-openai:latest` with explicit args — model
  `cyankiwi/Qwen3-Next-80B-A3B-Instruct-AWQ-4bit`, `--max-model-len 131072`,
  `--tensor-parallel-size 1`, `--trust-remote-code`, port 30000. **No `--quantization` flag:**
  this checkpoint is compressed-tensors (not `awq_marlin`) despite its name; vLLM auto-detects
  it from the model config. Forcing `--quantization awq_marlin` fails validation.
- **Disk:** 120GB container disk, **0** persistent volume — everything lives on the container
  disk that is deleted on terminate, so idle cost is a hard $0.

> **If `up` never reaches "serving":** watch the pod logs in the console. The usual causes are
> the community AWQ needing a newer vLLM (bump/pin `deploy.imageName`), A100-80GB community
> capacity being tight (add a fallback id to `deploy.gpuTypeIds`, e.g. the SXM variant from
> `./redlab-pod.sh gpus`), or — if you pointed `REDLAB_MODEL` at **gated/private weights** — a
> missing token (`401` in the logs): set `REDLAB_HF_TOKEN` in `.env` and re-run. A public model
> only logs a benign "unauthenticated requests to the HF Hub" *warning* and still downloads; set
> the token anyway to avoid rate-limit throttling on the ~46GB pull. Fix and re-run `./redlab-pod.sh up`.

## 5. Use it, then tear it down

```sh
./redlabLLM.sh --smoke     # expect REDLAB_CLOUD_OK + round-trip time + balance delta
./redlabLLM.sh --ask "Walk the exploitation path for CVE-2021-44228 on my lab host and how I'd detect it"
./redlabLLM.sh             # interactive multi-turn (128K context)

./redlab-pod.sh status        # every pod on the account + $/hr burn + balance
./redlab-pod.sh down          # terminate the RedLab pod, then VERIFY nothing runs → $0 idle
```

> **Auth is on by default.** On the first `./redlab-pod.sh up`, the tool mints a bearer
> key, writes it to `.env` as `REDLAB_INFERENCE_KEY`, and passes it to the pod as
> `VLLM_API_KEY` — so the public RunPod proxy URL rejects any request without it. The
> client reads the same key automatically; you never type it. To rotate, delete the
> `REDLAB_INFERENCE_KEY` line from `.env` and run `up` again (a fresh pod gets a fresh key).

`down` re-lists after terminating and prints "Verified: no pods remain" (or names whatever is
left). It also clears `REDLAB_BASE_URL` from `.env` so the client can't point at a dead pod.

## 6. Cost hygiene (keep it under $20)

**Setup: A100 PCIe pod ($1.39/hr), created and terminated per session by `redlab-pod.sh`.**

- **`./redlab-pod.sh down` when done — it Terminates, never Stops.** A *stopped* pod's disk
  bills at **$0.20/GB/mo** (double the running rate); an 80GB stopped pod ≈ **$16/mo doing
  nothing**. Terminate deletes the disk → **$0 standing cost**. The only price is
  re-downloading the ~46GB model (~10–15 min, ~$0.35 of runtime) next `up`.
- **Verify nothing is running** anytime — this is the whole reason it's scripted:
  ```sh
  ./redlab-pod.sh status                # human-readable: pods + burn + balance
  ./redlab-pod.sh status --assert-idle  # exits 1 if ANY pod is running (use in cron/CI)
  ./redlab-pod.sh down --all            # panic button: terminate EVERY pod, then verify
  ```
- **Storage strategy, by usage frequency:**
  | Strategy | Idle cost | Session start | Use when |
  |---|---|---|---|
  | Terminate when done (default) | $0 | ~15 min re-download | weeks-apart use |
  | Network volume (caches weights) | ~$4/mo (60GB × $0.07) | none | weekly+ use |
  | Stop (keep pod disk) | ~$16/mo (80GB × $0.20) | none | never on a $20 budget |
- `./redlabLLM.sh --balance` (or `./redlab-pod.sh status`) before/after to watch credit.
- The account can't overspend the $20 you loaded as long as auto top-up stays OFF — that is
  the hard backstop even if you forget to terminate.

---

## Provenance to record before first real use (per this project's discipline)

In `config/cloud.json`: deployed HF repo id + revision (commit), quant format, sha256 of the
served weights if self-quantized, GPU type, endpoint region, and the date — know exactly
what you're running.
