"""Autonomous bug hunting for the bug-hunt-utils bounty.

Fetches the challenge utils.js (GitHub raw, falling back to a local copy),
executes it in node against a reference implementation, and returns the
distinct verified bugs it finds. The agent never trusts a bug it has not
executed: every candidate is produced by running the buggy function and
comparing against the correct result.

Reference implementations (the correct behaviour):
  - factorial(n)   = n!  (loop multiply 1..n)
  - countVowels(s) = count of [aeiouAEIOU]
  - reverse(s)     = code-point-aware reverse (Array.from, so emoji surrogate
                     pairs stay intact)
  - titleCase(s)   = capitalise the first letter of each space-separated word
                     (word-based reference; the apostrophe case is a
                     candidate, not a certainty)

Only the factorial / countVowels / reverse families are treated as verified;
titleCase reports are included only if the harness confirms them, and are
listed after the verified families.
"""

from __future__ import annotations

import json
import os
import subprocess

SPEC_RAW = (
    "https://raw.githubusercontent.com/AIProveRH/aiprove/main"
    "/challenges/bug-hunt-utils/utils.js"
)
LOCAL_FALLBACK = os.path.join(os.path.dirname(__file__), "utils.js")

HARNESS_JS = r"""
const fs = require("fs");
const u = require(process.argv[2]);

// --- references (correct behaviour) ---
function refFactorial(n){ let r=1; for(let i=2;i<=n;i++) r*=i; return r; }
function refCountVowels(s){ return (s.match(/[aeiouAEIOU]/g)||[]).length; }
function refReverse(s){ return Array.from(s).reverse().join(""); }
function refTitleCase(s){ return s.replace(/(^|\s)\w/g, c=>c.toUpperCase()); }

const bugs = [];
function check(fn, input, expected, actual){
  if (JSON.stringify(actual) !== JSON.stringify(expected))
    bugs.push({fn, input, expected});
}

// factorial 0..18
for (let n=0; n<=18; n++) check("factorial", n, refFactorial(n), u.factorial(n));
// countVowels on a spread of strings
for (const s of ["AEIOU","HELLO","BANANA","aeiou","Hello World","AEIOUaeiou","xyz","AaEeIiOoUu"])
  check("countVowels", s, refCountVowels(s), u.countVowels(s));
// reverse, including emoji surrogate pairs
for (const s of ["\u{1F600}a","abc","hello","\u{1F600}\u{1F603}","a\u{1F600}b"])
  check("reverse", s, refReverse(s), u.reverse(s));
// titleCase (candidate family)
for (const s of ["don't","hello world","it's a test","o'clock"])
  check("titleCase", s, refTitleCase(s), u.titleCase(s));

// controls: must NOT be reported
const controls = {
  sum: u.sum([1,2,3]) === 6,
  max: u.max([1,5,2]) === 5,
  clamp: u.clamp(5,0,3) === 3,
  unique: JSON.stringify(u.unique([1,1,2])) === "[1,2]",
  average: u.average([2,4]) === 3,
  isPalindrome: u.isPalindrome("aba") === true && u.isPalindrome("ab") === false,
};
console.log(JSON.stringify({bugs, controls}));
"""

VERIFIED_FIRST = ("factorial", "countVowels", "reverse")


def fetch_utils(workdir: str) -> str:
    """Download utils.js from the spec repo; fall back to the local copy."""
    dest = os.path.join(workdir, "utils.js")
    p = subprocess.run(
        ["curl", "-sSL", "--max-time", "30", SPEC_RAW, "-o", dest],
        capture_output=True, text=True, timeout=40,
    )
    if p.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 100:
        return dest, "github-raw"
    if os.path.exists(LOCAL_FALLBACK):
        import shutil

        shutil.copyfile(LOCAL_FALLBACK, dest)
        return dest, "local-fallback"
    raise RuntimeError("could not obtain utils.js from spec or local copy")


def run_harness(utils_path: str, workdir: str) -> dict:
    harness = os.path.join(workdir, "harness.js")
    with open(harness, "w") as fh:
        fh.write(HARNESS_JS)
    p = subprocess.run(
        ["node", harness, utils_path], capture_output=True, text=True, timeout=60,
        cwd=workdir,
    )
    if p.returncode != 0:
        raise RuntimeError(f"node harness failed: {p.stderr[:300]}")
    return json.loads(p.stdout)


def find_bugs(workdir: str, limit: int = 5) -> tuple[list[dict], dict, str]:
    """Return (bugs, controls, source) — bugs ordered verified-first, deduped."""
    utils_path, source = fetch_utils(workdir)
    result = run_harness(utils_path, workdir)
    bugs = result["bugs"]
    # group by family, then round-robin across families so the submission
    # shows breadth (factorial, countVowels, reverse) instead of 5 of one kind
    by_fn: dict[str, list[dict]] = {}
    for b in bugs:
        by_fn.setdefault(b["fn"], []).append(b)
    order = [fn for fn in list(VERIFIED_FIRST) + ["titleCase"] if fn in by_fn]
    seen: set[tuple[str, object]] = set()
    ordered: list[dict] = []
    idx = 0
    while len(ordered) < limit:
        progressed = False
        for fn in order:
            group = by_fn[fn]
            if idx < len(group):
                b = group[idx]
                key = (b["fn"], b["input"])
                if key not in seen:
                    seen.add(key)
                    ordered.append(b)
                    progressed = True
                    if len(ordered) >= limit:
                        break
        if not progressed:
            break
        idx += 1
    return ordered[:limit], result["controls"], source
