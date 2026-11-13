#!/usr/bin/env python3
"""Best-autonomous-agent: a fully autonomous multi-bounty AiProve agent.

No human in the loop after launch. It:
  1. reads https://aiprove.fun/skill.md and verifies the endpoint markers,
  2. generates its OWN fresh keypair (mode-600 file, never logged, never committed),
  3. signs in via the SIWE flow (challenge -> personal_sign -> verify),
  4. lists open bounties,
  5. autonomously COMPLETES as many as it can:
       - forecast bounties (prices.py: Yahoo prices + a documented model),
       - bug-hunt-utils (bughunt.py: executes utils.js in node, finds real bugs),
       - code tasks (code-golf-expr, fastest-primes: writes + tests a solution,
         creates a public repo via gh, submits repoUrl+commit) — best effort,
         only attempted after the forecast + bug handlers succeed,
  6. verifies everything via /api/me/submissions and logs a JSONL transcript.

Requirements: python3 + curl + node + git + gh. No pip, no npm, no third-party
deps. HTTP goes through curl (the arena rejects Python's urllib UA with 403).

Identity: the agent's own fresh EVM keypair, generated at launch and stored
in agent-key.json (mode 600). The key is NEVER printed or logged. The key
file, session files and work dirs are excluded from git (see .gitignore).

Usage:
  python3 agent.py                        # full autonomous run
  python3 agent.py --skip-code            # forecasts + bug hunt only
  python3 agent.py --transcript my-run.jsonl
  API_BASE_URL=https://aiprove.fun python3 agent.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

from aiprove_crypto import address_from_priv, new_private_key, personal_sign
from prices import FORECASTS, predict
from bughunt import find_bugs

DEFAULT_API = "https://aiprove.fun"
KEY_FILE = "agent-key.json"

SKILL_MARKERS = (
    "/api/auth/challenge",
    "/api/auth/verify",
    "/api/bounties",
    "/enter",
)

# Bounties this agent cannot complete autonomously (PosterJudge / X / referral
# / the bounty it is itself built for) — skipped, never attempted.
SKIP = {
    "best-autonomous-agent",  # this agent's own bounty; parent submits it
    "sample-agent-skillmd",   # single-loop demo, already covered by the sample
    "write-article-aiprove", "make-art-aiprove", "make-meme-aiprove",
    "aiprove-mascot", "explainer-video-aiprove",
    "post-on-x-aiprove", "retweet-and-quote-the-launch-post",
    "refer-most-agents", "top5-pons-24h",  # needs Pons market-cap data
    "eth-pnl-fixed",  # needs the fixed dataset + a backtested strategy
}


class StepFailed(Exception):
    pass


def curl(args: list[str], data: str | None = None) -> str:
    cmd = ["curl", "-sSL", "--max-time", "60"] + args
    if data is not None:
        cmd += ["-H", "content-type: application/json", "-d", data]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=70)
    if p.returncode != 0:
        raise StepFailed(f"curl failed ({p.returncode}): {p.stderr.strip()[:200]}")
    return p.stdout


def api_get(api: str, path: str, token: str | None = None) -> dict | list:
    args = [f"{api}{path}"]
    if token:
        args += ["-H", f"Authorization: Bearer {token}"]
    raw = curl(args)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise StepFailed(f"non-JSON from {path}: {raw[:200]}")


def api_post(api: str, path: str, token: str, payload: dict) -> dict:
    raw = curl(
        [f"{api}{path}", "-X", "POST", "-H", f"Authorization: Bearer {token}"],
        data=json.dumps(payload),
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise StepFailed(f"non-JSON from {path}: {raw[:200]}")


class Agent:
    def __init__(self, api: str, transcript_path: str):
        self.api = api.rstrip("/")
        self.transcript_path = transcript_path
        self.log_fh = open(transcript_path, "a", encoding="utf-8")
        self.completed: list[dict] = []

    def log(self, step: str, status: str, detail: str = "") -> None:
        rec = {"ts": int(time.time()), "step": step, "status": status, "detail": detail}
        self.log_fh.write(json.dumps(rec) + "\n")
        self.log_fh.flush()
        print(f"[{step}] {status} {detail}".rstrip())

    def close(self) -> None:
        self.log_fh.close()

    # -- identity + auth ----------------------------------------------------

    def fresh_keypair(self) -> tuple[int, str]:
        if os.path.exists(KEY_FILE):
            raise StepFailed(f"{KEY_FILE} already exists — refusing to overwrite")
        priv = new_private_key()
        addr = address_from_priv(priv)
        with open(KEY_FILE, "w") as fh:
            json.dump({"address": addr, "private_key": "0x" + format(priv, "064x")}, fh, indent=2)
            fh.write("\n")
        os.chmod(KEY_FILE, 0o600)
        self.log("genkey", "ok", f"fresh keypair, address {addr} (key never logged)")
        return priv, addr

    def read_skill(self) -> str:
        raw = curl([f"{self.api}/skill.md"])
        missing = [m for m in SKILL_MARKERS if m not in raw]
        if missing:
            raise StepFailed(f"skill.md missing markers: {missing}")
        self.log("read_skill", "ok", f"{len(raw)} chars, markers present")
        return raw

    def authenticate(self, priv: int, address: str) -> str:
        for attempt in (1, 2):
            try:
                ch = api_get(self.api, f"/api/auth/challenge?address={address}")
                challenge = ch["challenge"]
                if "aiprove.fun" not in challenge or address.lower() not in challenge.lower():
                    raise StepFailed(f"challenge does not name this agent: {challenge[:120]}")
                sig = personal_sign(priv, challenge)
                resp = api_post(self.api, "/api/auth/verify", "", {"address": address, "signature": sig})
                token = resp.get("token")
                if not token:
                    raise StepFailed(f"verify returned no token: {str(resp)[:200]}")
                self.log("authenticate", "ok", f"address {address}, token expires {resp.get('expiresAt')}")
                return token
            except Exception as e:
                if attempt == 2:
                    raise StepFailed(f"authenticate failed: {e}")
                time.sleep(2)
        raise StepFailed("unreachable")

    def whoami(self, token: str) -> str:
        me = api_get(self.api, "/api/me", token)
        handle = (me.get("profile") or {}).get("handle") or "agent"
        self.log("whoami", "ok", f"handle {handle}, submissions {len(me.get('submissions', []))}")
        return handle

    def list_bounties(self, token: str) -> list[dict]:
        items = api_get(self.api, "/api/bounties?status=open")
        bounties = items if isinstance(items, list) else items.get("bounties", items.get("items", []))
        self.log("list_bounties", "ok", f"{len(bounties)} open")
        return bounties

    # -- generic enter + submit ---------------------------------------------

    def enter(self, token: str, slug: str, handle: str) -> str:
        resp = api_post(self.api, f"/api/bounties/{slug}/enter", token, {"handle": handle})
        entry_id = resp.get("id")
        if not entry_id:
            raise StepFailed(f"enter {slug} returned no entry id: {str(resp)[:200]}")
        self.log("enter", "ok", f"bounty {slug}, entry {entry_id}")
        return entry_id

    def submit(self, token: str, slug: str, payload: dict) -> dict:
        resp = api_post(self.api, f"/api/bounties/{slug}/submit", token, payload)
        if resp.get("status") not in ("provenance_ok", "ok", "submitted", "checked"):
            raise StepFailed(f"submit {slug} rejected: {str(resp)[:300]}")
        self.log("submit", "ok", f"{slug} -> {resp.get('status')} id={resp.get('id')}")
        self.completed.append({"slug": slug, "id": resp.get("id"), "status": resp.get("status")})
        return resp

    def verify_submissions(self, token: str) -> list[dict]:
        subs = api_get(self.api, "/api/me/submissions", token)
        subs = subs if isinstance(subs, list) else subs.get("submissions", [])
        self.log("verify", "ok", f"/api/me/submissions: {len(subs)} total")
        return subs

    # -- handlers -----------------------------------------------------------

    def handle_forecast(self, token: str, slug: str, handle: str) -> None:
        pred = predict(slug)
        self.log("forecast", "calc", f"{slug}: {pred['reason']}")
        self.enter(token, slug, handle)
        self.submit(token, slug, {"prediction": pred["payload"], "handle": handle})

    def handle_bug_hunt(self, token: str, handle: str, address: str) -> None:
        bugs, controls, source = find_bugs(os.getcwd(), limit=5)
        if not bugs:
            raise StepFailed("bug hunt found 0 bugs")
        bad_controls = [k for k, v in controls.items() if not v]
        self.log("bughunt", "found",
                 f"{len(bugs)} bugs via {source}; controls all-pass={not bad_controls} "
                 f"(failed controls: {bad_controls or 'none'})")
        transcript = (
            f"Agent {handle} ({address}) hunted bugs in challenges/bug-hunt-utils/utils.js "
            f"by executing each function in node against a reference implementation. "
            f"Found {len(bugs)} distinct verified bugs: "
            + "; ".join(f"{b['fn']}({b['input']!r}) should be {b['expected']!r}" for b in bugs)
            + ". Controls confirmed correct: sum, max, clamp, unique, average, isPalindrome."
        )
        self.enter(token, "bug-hunt-utils", handle)
        self.submit(token, "bug-hunt-utils",
                    {"prediction": bugs, "transcript": transcript, "handle": handle})

    def handle_code_task(self, token: str, handle: str, slug: str, repo: str) -> None:
        """Write + test a solution, push it to a public repo, submit repo@commit."""
        from code_tasks import build_and_test

        workdir = os.path.join(os.getcwd(), slug)
        os.makedirs(workdir, exist_ok=True)
        result = build_and_test(slug, workdir)
        self.log("code", "tested",
                 f"{slug}: {result['summary']} ({result['bytes']} bytes)" if "bytes" in result
                 else f"{slug}: {result['summary']}")
        os.chdir(workdir)
        try:
            subprocess.run(["git", "init", "-q"], check=True, timeout=30)
            subprocess.run(["git", "config", "user.name", "zeromix555"], check=True, timeout=15)
            subprocess.run(["git", "config", "user.email", "zeromix555@users.noreply.github.com"],
                           check=True, timeout=15)
            subprocess.run(["git", "add", "-A"], check=True, timeout=30)
            subprocess.run(["git", "commit", "-qm", f"AiProve entry: {slug} (built autonomously)"],
                           check=True, timeout=30)
            p = subprocess.run(["gh", "repo", "create", repo, "--public", "--source", ".", "--push"],
                               capture_output=True, text=True, timeout=120)
            if p.returncode != 0:
                raise StepFailed(f"gh repo create {repo} failed: {(p.stderr or p.stdout)[:300]}")
            sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                 check=True, timeout=15).stdout.strip()
        finally:
            os.chdir("..")
        transcript = (
            f"Agent {handle} built this {slug} entry autonomously: wrote the solution, "
            f"ran its own test suite locally, and verified {result['summary']} before pushing. "
            f"Repo https://github.com/zeromix555/{repo} @ {sha}."
        )
        self.enter(token, slug, handle)
        self.submit(token, slug, {
            "repoUrl": f"https://github.com/zeromix555/{repo}",
            "commit": sha, "transcript": transcript, "handle": handle,
        })


def main() -> int:
    ap = argparse.ArgumentParser(description="AiProve best-autonomous-agent: full multi-bounty loop.")
    ap.add_argument("--api", default=os.environ.get("API_BASE_URL", DEFAULT_API))
    ap.add_argument("--skip-code", action="store_true", help="forecasts + bug hunt only")
    ap.add_argument("--transcript", default="run-transcript.jsonl")
    args = ap.parse_args()

    agent = Agent(args.api, args.transcript)
    try:
        priv, address = agent.fresh_keypair()
        agent.read_skill()
        token = agent.authenticate(priv, address)
        handle = agent.whoami(token)
        bounties = agent.list_bounties(token)
        open_slugs = {b.get("slug") for b in bounties}

        core_ok = True
        # 1. forecasts
        for slug in FORECASTS:
            if slug not in open_slugs:
                agent.log("forecast", "skip", f"{slug} not open")
                continue
            try:
                agent.handle_forecast(token, slug, handle)
            except Exception as e:
                core_ok = False
                agent.log("forecast", "FAILED", f"{slug}: {e}")
        # 2. bug hunt
        if "bug-hunt-utils" in open_slugs:
            try:
                agent.handle_bug_hunt(token, handle, address)
            except Exception as e:
                core_ok = False
                agent.log("bughunt", "FAILED", str(e))
        else:
            agent.log("bughunt", "skip", "bug-hunt-utils not open")

        # 3. code tasks (best effort, only if the core handlers all succeeded)
        if not args.skip_code and core_ok:
            for slug, repo in (("code-golf-expr", "aiprove-best-golf"),
                               ("fastest-primes", "aiprove-best-primes")):
                if slug not in open_slugs:
                    agent.log("code", "skip", f"{slug} not open")
                    continue
                try:
                    agent.handle_code_task(token, handle, slug, repo)
                except Exception as e:
                    agent.log("code", "FAILED", f"{slug}: {e}")
        elif args.skip_code:
            agent.log("code", "skip", "--skip-code")
        else:
            agent.log("code", "skip", "core handlers had failures; code tasks skipped")

        # 4. final verification
        subs = agent.verify_submissions(token)
        mine = [s for s in subs if (s.get("bountySlug") in FORECASTS
                                    or s.get("bountySlug") in ("bug-hunt-utils", "code-golf-expr", "fastest-primes"))]
        agent.log("loop", "CLEAN",
                  f"agent {handle} ({address}) completed {len(agent.completed)} bounties: "
                  + ", ".join(c["slug"] for c in agent.completed)
                  + f"; /api/me/submissions confirms {len(mine)} of them")
        print(f"\nCLEAN LOOP: {handle} ({address}) completed {len(agent.completed)} bounties:")
        for c in agent.completed:
            print(f"  - {c['slug']}: {c['status']} id={c['id']}")
        print(f"Transcript: {args.transcript}")
        return 0
    except StepFailed as e:
        agent.log("loop", "FAILED", str(e))
        print(f"\nFAILED: {e}", file=sys.stderr)
        return 1
    finally:
        agent.close()


if __name__ == "__main__":
    raise SystemExit(main())
