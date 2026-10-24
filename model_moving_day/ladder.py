"""The size ladder: per call site try Nano -> Lightning -> Super -> Ultra and keep the smallest that passes.

A cell is (call site x rung x K runs of the repo's own covering tests). The pass rule is fixed in the manifest
before the census: the cell's pass rate must not be worse than the baseline's minus a named margin, over at
least K valid runs (sandbox or network failures are infrastructure errors, not runs). Every cell is kept,
including failures and the "no model passed, the old model stays" outcome.
"""
import math
from dataclasses import dataclass, field

Z95 = 1.959963984540054
NANO, LIGHT = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", "nvidia/Nemotron-3_5-Lightning"
SUPER, ULTRA = "nvidia/nemotron-3-super-120b-a12b", "nvidia/Nemotron-3-Ultra-550b-a55b"
RUNGS = [("Nano", NANO), ("Lightning", LIGHT), ("Super", SUPER), ("Ultra", ULTRA)]
# Decision parameters; census/MANIFEST.json records them before the first ladder job and the ladder refuses to run
# when they differ. Baseline 1.0 is the strictest choice (assumed, not measured: see the manifest).
K, MARGIN, BASELINE = 5, 0.2, 1.0
MAX_INFRA_RETRIES = 2  # re-runs of the same rung for runs lost to infrastructure; then the site's status is "infra"


def is_infra_status(code):
    """Proxy-logged statuses that say nothing about the model: budget (402), rate limit (429), any 5xx (502 from
    upstream, 500 from the proxy itself, 501 for an endpoint the proxy does not route) and None (timeout or a
    connection that failed after retries)."""
    code = str(code)
    return code in ("402", "429", "None") or (code.isdigit() and 500 <= int(code) <= 599)


def required_passes(k, baseline, margin):
    return math.ceil((baseline - margin) * k - 1e-9)


def wilson(k, n, z=Z95):
    """Wilson score interval for k successes out of n."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def rule_of_three(n):
    """Approximate 95 % upper bound on the failure rate after 0 failures in n runs."""
    return 1.0 if n <= 0 else min(1.0, 3.0 / n)


@dataclass
class RunOutcome:
    passed: bool | None            # None = infrastructure error, not counted as a run
    failure: str = ""              # first failing test and message, or the infra error
    diagnosis: str | None = None   # e.g. "empty_content_length" from the call log
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost: float = 0.0
    seconds: float = 0.0
    tests: dict = field(default_factory=dict)  # test node id -> passed


@dataclass
class Cell:
    site: str
    rung: str
    runs: list
    params: dict = field(default_factory=dict)

    @property
    def valid(self):
        return [r for r in self.runs if r.passed is not None]

    @property
    def n(self):
        return len(self.valid)

    @property
    def k(self):
        return sum(1 for r in self.valid if r.passed)

    @property
    def infra(self):
        return len(self.runs) - self.n

    @property
    def rate(self):
        return self.k / self.n if self.n else float("nan")

    def interval(self):
        return wilson(self.k, self.n)

    def tokens_per_call(self):
        calls = sum(r.calls for r in self.runs)
        if not calls:
            return float("nan"), float("nan")
        return (sum(r.prompt_tokens for r in self.runs) / calls, sum(r.completion_tokens for r in self.runs) / calls)

    @property
    def cost(self):
        return sum(r.cost for r in self.runs)


def usd_per_1000_calls(tokens_in, tokens_out, price):
    return 1000 * (tokens_in * price[0] + tokens_out * price[1]) / 1e6


def cell_passes(cell, baseline, margin, k):
    return cell.n >= k and cell.rate >= baseline - margin - 1e-12


def decided_red(cell, baseline, margin, k):
    """True when the valid runs already hold more failures than a passing cell of K runs may have."""
    return cell.n - cell.k > k - required_passes(k, baseline, margin)


@dataclass
class Fix:
    kind: str
    params: dict
    why: str
    by: str = "rule"  # "rule" or the model that proposed it
    expect: tuple = ()  # request changes ("kind:key") that prove the fix reached the request


@dataclass
class LadderResult:
    site: str
    cells: list
    chosen: str | None
    fix: Fix | None
    reason: str


def climb(site, rungs, run_cell, k, baseline, margin, fixer=None):
    """run_cell(site, rung, k, params) -> list[RunOutcome]; fixer(cell) -> Fix | None."""
    cells = []
    for rung in rungs:
        cell = Cell(site, rung, run_cell(site, rung, k, {}))
        cells.append(cell)
        if cell_passes(cell, baseline, margin, k):
            return LadderResult(site, cells, rung, None, f"{rung} passed {cell.k}/{cell.n}")
        fix = fixer(cell) if fixer else None
        if fix:
            fixed = Cell(site, rung, run_cell(site, rung, k, fix.params), dict(fix.params))
            cells.append(fixed)
            if cell_passes(fixed, baseline, margin, k):
                return LadderResult(site, cells, rung, fix, f"{rung} passed {fixed.k}/{fixed.n} after fix {fix.kind}")
    return LadderResult(site, cells, None, None, "no model passed, the old model stays")
