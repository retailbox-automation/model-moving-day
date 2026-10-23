import math

import pytest

from model_moving_day import ladder as ld

RUNGS = ["nano", "lightning", "super", "ultra"]


def test_wilson_interval_known_values():
    lo, hi = ld.wilson(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3) and hi == pytest.approx(0.9433, abs=1e-3)
    lo, hi = ld.wilson(0, 10)
    assert lo == 0 and hi == pytest.approx(0.2775, abs=1e-3)
    assert ld.wilson(0, 0) == (0.0, 1.0)


def test_rule_of_three():
    assert ld.rule_of_three(30) == pytest.approx(0.1)
    assert ld.rule_of_three(0) == 1.0


def out(passed, **kw):
    return ld.RunOutcome(passed=passed, **kw)


def test_cell_pass_rule_is_rate_not_worse_than_baseline_minus_margin():
    c = ld.Cell("s", "lightning", [out(True)] * 4 + [out(False)])
    assert c.rate == 0.8 and ld.cell_passes(c, baseline=1.0, margin=0.2, k=5)
    c = ld.Cell("s", "lightning", [out(True)] * 3 + [out(False)] * 2)
    assert not ld.cell_passes(c, baseline=1.0, margin=0.2, k=5)


def test_infra_errors_do_not_count_as_runs_and_too_few_valid_runs_cannot_pass():
    c = ld.Cell("s", "nano", [out(True), out(True), out(None, failure="sandbox timeout")])
    assert c.n == 2 and c.infra == 1
    assert not ld.cell_passes(c, baseline=1.0, margin=0.2, k=3)


class FakeRunner:
    """Scripted outcomes per (rung, params-key) so the ladder logic is tested without any model."""

    def __init__(self, table):
        self.table, self.calls = table, []

    def __call__(self, site, rung, k, params):
        key = (rung, tuple(sorted((params or {}).items())))
        self.calls.append(key)
        return [out(p, diagnosis=d) for p, d in self.table[key]][:k]


def test_climb_stops_at_the_first_passing_rung():
    r = FakeRunner({("nano", ()): [(False, None)] * 3, ("lightning", ()): [(True, None)] * 3})
    res = ld.climb("site", RUNGS, r, k=3, baseline=1.0, margin=0.2)
    assert res.chosen == "lightning" and [c.rung for c in res.cells] == ["nano", "lightning"]
    assert len(r.calls) == 2  # super and ultra never run


def test_climb_records_no_model_passed():
    r = FakeRunner({(rung, ()): [(False, None)] * 3 for rung in RUNGS})
    res = ld.climb("site", RUNGS, r, k=3, baseline=1.0, margin=0.2)
    assert res.chosen is None and len(res.cells) == 4 and res.reason == "no model passed, the old model stays"


def test_fixer_turns_a_red_cell_green_and_both_cells_are_published():
    fix = {"chat_template_kwargs.enable_thinking": False}
    r = FakeRunner({("nano", ()): [(False, "empty_content_length")] * 3,
                    ("nano", tuple(fix.items())): [(True, None)] * 3})

    def fixer(cell):
        if any(o.diagnosis == "empty_content_length" for o in cell.runs):
            return ld.Fix("reasoning_off", fix, "reasoning used the whole max_tokens budget")
        return None

    res = ld.climb("site", RUNGS, r, k=3, baseline=1.0, margin=0.2, fixer=fixer)
    assert res.chosen == "nano" and res.fix.kind == "reasoning_off"
    assert [(c.rung, bool(c.params)) for c in res.cells] == [("nano", False), ("nano", True)]


def test_cell_cost_per_1000_calls_from_measured_tokens():
    runs = [out(True, calls=2, prompt_tokens=4000, completion_tokens=600, cost=0.0)]
    c = ld.Cell("s", "lightning", runs)
    per_call_in, per_call_out = c.tokens_per_call()
    assert (per_call_in, per_call_out) == (2000, 300)
    assert ld.usd_per_1000_calls(per_call_in, per_call_out, (0.06, 0.24)) == pytest.approx(0.192)
    assert math.isnan(ld.Cell("s", "x", [out(True)]).tokens_per_call()[0])
