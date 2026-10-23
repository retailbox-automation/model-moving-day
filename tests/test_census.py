from model_moving_day import census


def summ(outcomes, reach=None):
    return {"runs": [{t: {"outcome": o, "message": ""} for t, o in outcomes.items()}],
            "calls": {"tests": reach or {}, "sites": {}}}


def test_a_site_counts_as_covered_only_when_a_reaching_test_flips():
    fwd = summ({"t1": "passed", "t2": "passed", "t3": "failed"},
               reach={"t1": ["a.py:1"], "t2": ["b.py:2"], "t3": ["c.py:3"]})
    brk = summ({"t1": "failed", "t2": "passed", "t3": "failed"})
    rows = {r["site"]: r for r in census.controls_table(fwd, brk)}
    assert rows["a.py:1"]["covered"] and rows["a.py:1"]["flip_tests"] == ["t1"]
    assert not rows["b.py:2"]["covered"]  # passes even with an empty answer: the test does not check the model
    assert not rows["c.py:3"]["covered"]  # fails even with a good answer: no evidence either way


def test_cassette_baseline_is_the_good_side_when_present_and_shared_tests_are_not_isolated():
    fwd = summ({"t1": "failed"}, reach={"t1": ["a.py:1", "b.py:2"]})
    brk = summ({"t1": "error"})
    base = summ({"t1": "passed"})
    rows = census.controls_table(fwd, brk, base)
    assert all(r["covered"] and not r["isolated"] and r["good_side"] == "baseline" for r in rows)
    assert rows[0]["details"][0] == {"test": "t1", "baseline": "passed", "broken": "error", "forward": "failed"}
