# Authorization boundary

RedLab is a reasoning companion, not an autonomous agent. The safety of using it comes
from *this boundary plus your review*, not from the model. This is the operator contract.

## In scope

- Your own systems, lab VMs, and disposable/vulnerable fixtures (CTF, DVWA, VulnHub,
  HackTheBox, and other targets you own or are explicitly permitted to attack).
- Client engagements with written authorization / a signed scope of work.
- Bug-bounty targets within the program's published scope and rules.
- Defensive work: threat modeling, detection engineering, secure code review, CTI,
  incident analysis.

## Out of scope

- Any live third-party, production, LAN, or cloud target you are not authorized to test.
- Real-person social engineering / phishing delivery, malware deployment, persistence,
  or data exfiltration against real people or organizations.
- Autonomous change, commit, deployment, or security approval — every command RedLab
  proposes is reviewed and run by you.

## Operating rules

- Single-tenant, on-demand GPU pod that you bring up and terminate per session; nothing
  is left running or listening between sessions (`./redlab-pod.sh down` verifies $0 idle).
- Inputs and outputs traverse your own rented infrastructure — treat them as
  operator-private, but do not assume they are airgapped. Keep real secrets and live
  target identities out of anything that need not contain them.
- Treat any RedLab-proposed command as a draft: read it, understand the blast radius,
  then decide. RedLab flags destructive/noisy actions but you own the call.
- Keep real target identities, secrets, and live exploit payloads out of anything you
  commit to this repo.
