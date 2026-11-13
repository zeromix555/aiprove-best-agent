"""Code-task builder: write + test a solution, locally, before any repo exists.

code-golf-expr: the agent tries candidates smallest-first against its own
test suite (public cases + the tricky --/++ cases) and keeps the smallest
that passes everything. A pure eval() (21 bytes) fails the --/++ cases
because JS parses them as increment/decrement; collapsing them to '+' first
(51 bytes) passes all 40.

fastest-primes: the agent writes an odd-only bitset sieve in C, compiles it
with cc -O2, verifies pi(N) against known values (10->4, 100->25, 1000->168,
100000->9592, 1000000->78498, 5000000->348513) and measures latency.
"""

from __future__ import annotations

import os
import subprocess

GOLF_CASES = [
    ("1+2*3", 7), ("(2+3)*4", 20), ("10/4", 2.5), ("2-3", -1),
    ("3.5*2", 7), ("((1+2))", 3), ("0.1+0.2", 0.30000000000000004),
    ("10-2-3", 5), ("2*3*4", 24), ("1/3", 1 / 3),
    ("2--3", 5), ("2++3", 5), ("--5", 5), ("++5", 5), ("2---3", -1),
    ("5+-3", 2), ("5-+3", 2), ("(1+2)*(3+4)", 21), ("100/10/2", 5),
    ("2.5+2.5", 5), ("0", 0), ("-5", -5), ("+5", 5), ("3*-2", -6),
    ("1e3", 1000), ("2.5*4", 10), ("7%3", 1), ("2**3", 8),
    ("(8-3)/5", 1), ("1.5*1.5", 2.25), ("100-99", 1), ("0.5+0.25", 0.75),
    ("((2))", 2), ("9/3/3", 1), ("4*(2+1)", 12), ("10-(2+3)", 5),
    ("2- -3", 5), ("3 + + 4", 7), ("1.2e2", 120), ("5/2", 2.5),
]

GOLF_CANDIDATES = [
    "exports.evalExpr=s=>eval(s)",
    "exports.evalExpr=s=>eval(s.replace(/--|\\+\\+/g,'+'))",
]

PRIMES_C = r"""#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(void){
  long n; if(scanf("%ld",&n)!=1) return 1;
  if(n<2){ printf("0\n"); return 0; }
  long m=(n-1)/2; char *c=calloc(m+1,1);
  for(long i=3;i*i<=n;i+=2) if(!c[i/2])
    for(long j=i*i;j<=n;j+=2*i) c[j/2]=1;
  long cnt=1; for(long i=3;i<=n;i+=2) if(!c[i/2]) cnt++;
  printf("%ld\n",cnt); return 0;
}
"""

PRIMES_CASES = [(10, 4), (100, 25), (1000, 168), (100000, 9592), (1000000, 78498), (5000000, 348513)]


def _run(cmd: list[str], cwd: str, inp: str | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, input=inp)


def build_golf(workdir: str) -> dict:
    test_js = "const {evalExpr}=require('./index.js');\nconst cases=%s;\nlet pass=0,fail=0;\nfor(const [s,exp] of cases){let got;try{got=evalExpr(s)}catch(e){got='ERR:'+e.message} if(Object.is(got,exp)||got===exp){pass++}else{fail++;console.log('FAIL',s,'got',got,'exp',exp)}}\nconsole.log(pass+' pass, '+fail+' fail');\nprocess.exit(fail?1:0);\n" % (
        "[" + ",".join(f"[{s!r},{v!r}]" for s, v in GOLF_CASES) + "]"
    )
    with open(os.path.join(workdir, "test.js"), "w") as fh:
        fh.write(test_js)
    for cand in GOLF_CANDIDATES:
        with open(os.path.join(workdir, "index.js"), "w") as fh:
            fh.write(cand + "\n")
        p = _run(["node", "test.js"], workdir)
        if p.returncode == 0:
            nbytes = len(cand.encode())
            return {
                "summary": f"evalExpr {nbytes} bytes, {len(GOLF_CASES)} cases all pass",
                "bytes": nbytes,
            }
    raise RuntimeError("no golf candidate passed the test suite")


def build_primes(workdir: str) -> dict:
    with open(os.path.join(workdir, "primes.c"), "w") as fh:
        fh.write(PRIMES_C)
    p = _run(["cc", "-O2", "-o", "primes", "primes.c"], workdir)
    if p.returncode != 0:
        raise RuntimeError(f"cc failed: {p.stderr[:300]}")
    for n, expected in PRIMES_CASES:
        p = _run(["./primes"], workdir, inp=f"{n}\n")
        if p.returncode != 0 or p.stdout.strip() != str(expected):
            raise RuntimeError(f"primes({n}) = {p.stdout.strip()!r}, expected {expected}")
    import time

    t0 = time.time()
    for _ in range(3):
        _run(["./primes"], workdir, inp="5000000\n")
    ms = (time.time() - t0) / 3 * 1000
    return {"summary": f"C sieve, pi(5M)=348513 correct, ~{ms:.0f} ms for N=5M"}


def build_and_test(slug: str, workdir: str) -> dict:
    if slug == "code-golf-expr":
        return build_golf(workdir)
    if slug == "fastest-primes":
        return build_primes(workdir)
    raise RuntimeError(f"no code-task builder for {slug}")
