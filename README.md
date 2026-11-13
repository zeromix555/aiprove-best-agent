# Best-autonomous-agent — a fully autonomous multi-bounty AiProve agent

No human in the loop after launch. It reads `aiprove.fun/skill.md`, generates
its **own** fresh keypair, signs in via the SIWE flow (challenge →
`personal_sign` → verify), lists open bounties, and **completes** as many as
it can on its own — then verifies everything via `/api/me/submissions`.

## What it completes

| Handler | Bounties | How |
| --- | --- | --- |
| Forecasts (`prices.py`) | btc-tomorrow, forecast-eth-24h, forecast-btc-7d, spy-close-1dollar, forecast-gold-tomorrow, forecast-nvda-3d, signal-eth-pnl-24h | Yahoo Finance public chart API (Binance is geo-blocked from here) + a documented momentum + mean-reversion model. No randomness. |
| Bug hunt (`bughunt.py`) | bug-hunt-utils | Fetches `utils.js` from the spec repo, executes it in node against a reference implementation, submits up to 5 distinct verified bugs. |
| Code tasks (`code_tasks.py`) | code-golf-expr, fastest-primes | Writes a solution, tests it locally (40 golf cases; pi(N) up to 5M), creates a public repo via `gh`, submits repo@commit. Best effort — only runs if the forecast + bug handlers all succeed. |

Skipped (not autonomously completable): all PosterJudge content bounties,
X bounties, refer-most-agents, top5-pons-24h (needs Pons market-cap data),
eth-pnl-fixed (needs the fixed dataset + a backtested strategy),
best-autonomous-agent itself (this agent's own bounty) and
sample-agent-skillmd (the single-loop demo).

## Run

```bash
python3 agent.py                        # full autonomous run
python3 agent.py --skip-code            # forecasts + bug hunt only
```

Requirements: python3 + curl + node + git + gh. No pip, no npm, no
third-party deps. HTTP goes through curl (the arena rejects Python's
default urllib user-agent with 403); verify POST needs `--max-time 60`.

## Identity

The agent generates a fresh EVM keypair at launch and stores it in
`agent-key.json` (mode 600). The key is **never printed, never logged,
never committed** — see `.gitignore`. The transcript (`run-transcript.jsonl`)
records every step: skill read, auth, bounty list, each enter, each submit,
and the final `/api/me/submissions` verification.

## Live run

Run at 2026-11-14 (see `run-transcript.jsonl`): the agent completed
**10 bounties** with a fresh keypair — 7 forecasts, bug-hunt-utils (5
verified bugs), code-golf-expr and fastest-primes — all `provenance_ok`,
verified via `/api/me/submissions`.
