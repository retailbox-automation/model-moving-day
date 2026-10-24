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


# --- ladder engine (phase B) ----------------------------------------------------------------------------------
import json

import pytest

from model_moving_day import agent, ladder, manifest as mf, sandbox as sb, tf_client as tf

SITE, T1 = "pkg/a.py:3", "tests/test_a.py::test_one"


def job_summary(outcomes, errors_by_run=None, codes=None, changes=None, tests_reach=None, **extra):
    """A job summary as the in-sandbox job writes it; outcomes: one test outcome per run."""
    runs = [{T1: {"outcome": o, "message": "" if o == "passed" else "AssertionError: wrong"}} for o in outcomes]
    site = {"calls": 2 * len(outcomes), "prompt_tokens": 1000, "completion_tokens": 100, "reasoning_tokens": 0, "cost": 0.001,
            "errors_by_run": errors_by_run or {}, "changes": changes or {"model:model": 2 * len(outcomes)}, "diagnoses": {}}
    return {"runs": runs, "exit_codes": codes or [0] * len(outcomes), "freeze_before": [], "freeze_after": [],
            "calls": {"tests": tests_reach or {T1: [SITE]}, "sites": {SITE: site}, "untraced": {}}, "envelope_spent": 0.001,
            **extra}


def test_site_cells_infra_rules():
    k = 3
    assert [o.passed for o in census.site_cells(None, SITE, [T1], k)] == [None] * 3          # sandbox run lost
    assert [o.passed for o in census.site_cells({"error": "RuntimeError: x"}, SITE, [T1], k)] == [None] * 3
    s = job_summary(["passed", "failed", "failed"], codes=[0, 1, "timeout"])
    assert [o.passed for o in census.site_cells(s, SITE, [T1], k)] == [True, False, None]     # timeout is infra
    s = job_summary(["failed", "failed", "passed"], errors_by_run={"1": {"402": 1}, "2": {"502": 3}})
    outs = census.site_cells(s, SITE, [T1], k)
    assert [o.passed for o in outs] == [False, None, True]  # 402 on a failing run -> infra; a clean pass stays a pass
    assert "402" in outs[1].failure
    s = job_summary(["failed", "failed", "failed"], errors_by_run={"?": {"None": 1}, "0": {"400": 2}})
    assert [o.passed for o in census.site_cells(s, SITE, [T1], k)] == [None] * 3              # unattributed infra: all
    s = job_summary(["failed"], errors_by_run={}, tests_reach={T1: [SITE, "pkg/b.py:9"]})
    s["calls"]["sites"]["pkg/b.py:9"] = {"errors_by_run": {"0": {"500": 1}}}
    assert census.site_cells(s, SITE, [T1], 1)[0].passed is None  # infra at another site the failing test reaches
    s = job_summary(["passed"])
    del s["runs"][0][T1]
    assert census.site_cells(s, SITE, [T1], 1)[0].passed is None  # flip test missing from the report


class FakeRunJob:
    """Scripted jobs: script(model, routes, k, call_index) -> summary (or raises)."""

    def __init__(self, script):
        self.script, self.calls = script, []

    def __call__(self, box, image, t, routes, k, cap_usd, ledger, **kw):
        self.calls.append({"model": routes["default"].get("model"), "routes": routes, "k": k, "kw": kw,
                           "tests": [a for a in t.pytest_args if "::" in a]})
        out = self.script(routes["default"].get("model"), routes, k, len(self.calls) - 1)

        class R:
            cost, elapsed_s, wall_s, exit_code = 0.01, 1.0, 2.0, 0
        return R(), out


ENTRY = {"github": "o/r", "commit": "c" * 40, "install": [], "node_args": ["-q"], "env": {}, "image": {"uuid": "img", "tag": "t"},
         "flip_tests": {SITE: [T1]}, "frozen": {"sha256": "f", "hashes": {"tests/test_a.py": "0" * 64}, "extra_paths": []}}


def make_ladder(tmp_path, script, k=3, brain=None, kind="decision", rungs=None):
    rj = FakeRunJob(script)
    run = census.Ladder("t", ENTRY, mf.rules(), k, rungs or census.RUNGS, kind, tmp_path, rj, brain=brain,
                        manifest={"sha256": "m"})
    return run, rj


def test_a_lost_sandbox_run_is_infra_re_run_twice_and_never_climbs(tmp_path):
    """MF1: summary=None used to be read as 'Nano failed' and moved the site up a rung."""
    run, rj = make_ladder(tmp_path, lambda model, routes, k, i: None)
    doc = run.run()
    site = doc["sites"][SITE]
    assert site["status"] == "infra" and site["chosen"] is None
    assert {c["model"] for c in rj.calls} == {ladder.NANO} and len(rj.calls) == 1 + ladder.MAX_INFRA_RETRIES
    assert site["cells"][0]["status"] == "infra" and site["cells"][0]["infra_runs"] == 9


def test_a_402_at_the_site_is_infra_not_a_red_cell(tmp_path):
    run, rj = make_ladder(tmp_path, lambda m, r, k, i: job_summary(["failed"] * k, errors_by_run={str(j): {"402": 1} for j in range(k)}))
    doc = run.run()
    assert doc["sites"][SITE]["status"] == "infra" and {c["model"] for c in rj.calls} == {ladder.NANO}


def test_only_the_missing_runs_are_re_run_and_the_cell_keeps_its_infra_count(tmp_path):
    def script(model, routes, k, i):
        if i == 0:
            return job_summary(["passed", "passed", "failed"], codes=[0, 0, "timeout"])
        return job_summary(["passed"] * k)
    run, rj = make_ladder(tmp_path, script)
    doc = run.run()
    cell = doc["sites"][SITE]["cells"][0]
    assert [c["k"] for c in rj.calls[:2]] == [3, 1] and cell["passed_runs"] == 3 and cell["infra_runs"] == 1
    assert doc["sites"][SITE]["chosen"] == "Nano"


def test_a_cell_that_already_failed_too_often_is_not_re_run_for_infra(tmp_path):
    run, rj = make_ladder(tmp_path, lambda m, r, k, i: job_summary(["failed", "failed", "passed"], codes=[1, 1, "timeout"])
                          if m == ladder.NANO else job_summary(["passed"] * k), k=3)
    doc = run.run()
    assert [c["model"] for c in rj.calls][:2] == [ladder.NANO, ladder.LIGHT]  # no infra re-run at Nano
    assert doc["sites"][SITE]["chosen"] == "Lightning"


def test_every_job_and_cell_is_checkpointed_and_a_crash_keeps_the_paid_cells(tmp_path):
    """MF3: a SandboxRunLimit on the second rung must not lose the first rung's cells."""
    seen_on_disk = []

    def script(model, routes, k, i):
        if model == ladder.LIGHT:
            seen_on_disk.append(json.loads(run.path.read_text()))  # what a crash right now would leave behind
            raise sb.SandboxRunLimit("40 sandbox runs logged, limit 40")
        return job_summary(["failed"] * k)
    run, rj = make_ladder(tmp_path, script)
    with pytest.raises(sb.SandboxRunLimit):
        run.run()
    assert [c["rung"] for c in seen_on_disk[0]["sites"][SITE]["cells"]] == ["Nano"] and len(seen_on_disk[0]["jobs"]) == 1
    doc = json.loads(run.path.read_text())
    assert doc["status"] == "aborted" and "SandboxRunLimit" in doc["error"]
    assert [c["rung"] for c in doc["sites"][SITE]["cells"]] == ["Nano"] and doc["sites"][SITE]["status"] == "stopped"
    events = [json.loads(x)["event"] for x in run.log_path.read_text().splitlines()]
    assert events[:4] == ["start", "job", "cell", "end"] or events[:3] == ["start", "job", "cell"]
    assert events[-1] == "end"


def test_each_ladder_run_has_its_own_file_and_the_log_is_append_only(tmp_path):
    a, _ = make_ladder(tmp_path, lambda m, r, k, i: job_summary(["passed"] * k))
    a.run()
    b, _ = make_ladder(tmp_path, lambda m, r, k, i: job_summary(["failed"] * k))
    b.run()
    files = sorted(tmp_path.glob("ladder-*.json"))
    assert len(files) == 2 and a.path != b.path and a.path.exists()
    runs = {json.loads(x)["run_id"] for x in (tmp_path / "ladder-log.jsonl").read_text().splitlines()}
    assert runs == {a.run_id, b.run_id}


def test_frozen_hashes_go_to_every_job_and_a_changed_check_stops_the_ladder(tmp_path):
    """MF6: frozen_hashes were never passed, so the in-sandbox check compared nothing."""
    run, rj = make_ladder(tmp_path, lambda m, r, k, i: job_summary(["passed"] * k, freeze_after=["changed tests/test_a.py"]))
    with pytest.raises(census.IntegrityError):
        run.run()
    kw = rj.calls[0]["kw"]
    assert kw["frozen_hashes"] == ENTRY["frozen"]["hashes"] and kw["require_frozen"] is True


def test_untraced_calls_stop_the_ladder(tmp_path):
    def script(m, r, k, i):
        s = job_summary(["passed"] * k)
        s["calls"]["untraced"] = {"forward": 2}
        return s
    run, _ = make_ladder(tmp_path, script)
    with pytest.raises(census.IntegrityError, match="not tagged"):
        run.run()


class ScriptedBrain:
    model = agent.SUPER

    def __init__(self, fix):
        self.fix, self.asked = fix, []

    def diagnose(self, evidence):
        self.asked.append(evidence)
        return self.fix


def test_a_fix_that_did_not_change_the_request_does_not_count_and_the_site_climbs(tmp_path):
    """MF4: a green re-run whose requests carry no 'raised:max_tokens' must not be credited to raise_max_tokens."""
    fix = ladder.Fix("raise_max_tokens", {"min_max_tokens": 8000}, "budget", by="m", expect=("raised:max_tokens",))

    def script(model, routes, k, i):
        if model == ladder.NANO and not routes["sites"]:
            s = job_summary(["failed"] * k)
            s["calls"]["sites"][SITE]["diagnoses"] = {"empty_content_length": 2}
            return s
        return job_summary(["passed"] * k)  # passes, but the changes show only the model switch
    run, rj = make_ladder(tmp_path, script, brain=ScriptedBrain(fix))
    doc = run.run()
    nano_fix = doc["sites"][SITE]["cells"][1]
    assert nano_fix["fix"]["applied"] is False and nano_fix["passes"] is False and nano_fix["status"] == "fix not applied"
    assert doc["sites"][SITE]["chosen"] == "Lightning"


def test_an_applied_fix_moves_the_site_and_the_route_carries_its_params(tmp_path):
    fix = ladder.Fix("reasoning_off", {"reasoning": "off"}, "newline", by="m", expect=("reasoning:enable_thinking",))

    def script(model, routes, k, i):
        if not routes["sites"]:
            s = job_summary(["failed"] * k)
            s["calls"]["sites"][SITE]["diagnoses"] = {"leading_whitespace": 2}
            return s
        assert routes["sites"][SITE]["reasoning"] == "off"
        return job_summary(["passed"] * k, changes={"model:model": 2, "reasoning:enable_thinking": 2 * k})
    run, _ = make_ladder(tmp_path, script, brain=ScriptedBrain(fix))
    doc = run.run()
    assert doc["sites"][SITE]["chosen"] == "Nano" and doc["sites"][SITE]["fix"]["applied"] is True


def test_a_failed_diagnosis_call_is_infra_not_a_reason_to_climb(tmp_path):
    class Down:
        model = agent.SUPER

        def diagnose(self, ev):
            raise tf.TFError(None, "RemoteDisconnected")

    def script(model, routes, k, i):
        s = job_summary(["failed"] * k)
        s["calls"]["sites"][SITE]["diagnoses"] = {"empty_content": 1}
        return s
    run, rj = make_ladder(tmp_path, script, brain=Down())
    doc = run.run()
    assert doc["sites"][SITE]["status"] == "infra" and {c["model"] for c in rj.calls} == {ladder.NANO}


def test_the_fallback_is_measured_on_the_next_rung_with_its_own_cell(tmp_path):
    """M7: the helper may fall back only to a fallback cell that was measured and passed."""
    run, rj = make_ladder(tmp_path, lambda m, r, k, i: job_summary(["passed"] * k) if m != ladder.LIGHT or i > 1
                          else job_summary(["failed"] * k))
    doc = run.run()
    site = doc["sites"][SITE]
    assert site["chosen"] == "Nano" and site["fallback"] == {"rung": "Lightning", "model": ladder.LIGHT, "passes": False,
                                                              "status": "fail"}
    assert site["cells"][-1]["role"] == "fallback"


def test_a_smoke_run_decides_nothing_and_measures_no_fallback(tmp_path):
    run, rj = make_ladder(tmp_path, lambda m, r, k, i: job_summary(["failed"] * k), k=1, kind="smoke",
                          rungs=[r for r in census.RUNGS if r[0] == "Super"])
    doc = run.run()
    assert doc["kind"] == "smoke" and doc["sites"][SITE]["status"] == "not decided" and len(rj.calls) == 1


def test_k_comes_from_the_manifest_not_the_command_line(monkeypatch):
    """MF7: there is no --k any more; K=5 and margin 0.2 are the manifest's."""
    import sys
    monkeypatch.setattr(sys, "argv", ["census", "ladder", "instructor", "--k", "2"])
    with pytest.raises(SystemExit):
        census.main()
    assert (ladder.K, ladder.MARGIN, ladder.required_passes(ladder.K, ladder.BASELINE, ladder.MARGIN)) == (5, 0.2, 4)


def test_the_ladder_refuses_to_start_when_the_manifest_does_not_match(monkeypatch):
    """MF3: k, margin, rungs, commit, models or flip tests differing from the manifest stop the run before any job."""
    monkeypatch.setattr(mf, "load", lambda path=mf.PATH: ({"targets": {"instructor": {}}}, "sha"))
    monkeypatch.setattr(mf, "verify", lambda man, name, clones: ["rule k differs"])
    monkeypatch.setattr(census.sb, "Sandbox", lambda *a, **k: pytest.fail("no sandbox may start"))
    with pytest.raises(mf.ManifestMismatch, match="rule k differs"):
        census.cmd_ladder("instructor")


def test_the_ladder_refuses_an_unpushed_manifest_and_an_undeclared_smoke(monkeypatch):
    monkeypatch.setattr(mf, "load", lambda path=mf.PATH: ({"targets": {"instructor": {}}, "smoke": [], "rules": {}}, "sha"))
    monkeypatch.setattr(mf, "verify", lambda man, name, clones: [])
    monkeypatch.setattr(census.sb, "Sandbox", lambda *a, **k: pytest.fail("no sandbox may start"))

    def not_pushed():
        raise mf.ManifestMismatch(["manifest commit abc is not on origin/lane-c1"])
    monkeypatch.setattr(mf, "pushed", not_pushed)
    with pytest.raises(mf.ManifestMismatch, match="not on origin"):
        census.cmd_ladder("instructor")
    monkeypatch.setattr(mf, "pushed", lambda: {"commit": "abc"})
    with pytest.raises(mf.ManifestMismatch, match="not declared"):
        census.cmd_ladder("instructor", smoke_id="S-undeclared")


def test_coverage_marks_excluded_and_cassette_only_sites_and_the_wrong_control_narrows_flips():
    fwd = summ({"t1": "failed", "t2": "passed", "t3": "passed"}, reach={"t1": ["a.py:1"], "t2": ["b.py:2"], "t3": ["b.py:2"]})
    brk = summ({"t1": "failed", "t2": "failed", "t3": "failed"})
    base = summ({"t1": "passed", "t2": "passed", "t3": "passed"})
    wrong = summ({"t1": "failed", "t2": "failed", "t3": "passed"})  # t3 only checks the shape of the answer
    rows = {r["site"]: r for r in census.controls_table(fwd, brk, base, wrong)}
    assert rows["a.py:1"]["cassette_only"] is True       # only the recorded answer passes
    assert rows["b.py:2"]["cassette_only"] is False and rows["b.py:2"]["flip_tests"] == ["t2"]
    assert rows["b.py:2"]["flip_tests_empty_only"] == ["t3"]


def test_cmd_coverage_applies_site_exclusions_and_refuses_untraced_calls(tmp_path, monkeypatch):
    """MF2: a wording-pinned site is excluded with its reason and never reaches the ladder's flips."""
    ev = tmp_path / "evidence"
    (ev / "demo").mkdir(parents=True)
    targets = tmp_path / "targets.json"
    targets.write_text(json.dumps({"demo": {"github": "o/d", "commit": "c", "install": [], "pytest_args": [],
                                            "site_exclusions": {"a.py:1": "assertions pinned to recorded wording"}}}))
    fwd = summ({"t1": "passed", "t2": "passed"}, reach={"t1": ["a.py:1"], "t2": ["b.py:2"]})
    brk = summ({"t1": "failed", "t2": "failed"})
    for label, sm in (("forward", fwd), ("broken", brk)):
        (ev / "demo" / f"control-{label}.json").write_text(json.dumps({"summary": sm}))
    monkeypatch.setattr(census, "EVIDENCE", ev)
    monkeypatch.setattr(census, "TARGETS", targets)
    monkeypatch.setattr(census, "ast_sites", lambda name: type("S", (), {"sites": [], "errors": []})())
    census.cmd_coverage("demo")
    cov = json.loads((ev / "demo" / "coverage.json").read_text())
    assert cov["covered_sites"] == ["b.py:2"] and cov["excluded_sites"] == {"a.py:1": "assertions pinned to recorded wording"}
    row = next(r for r in cov["traced_sites"] if r["site"] == "a.py:1")
    assert row["covered"] is False and row["excluded"] and row["flip_tests"] == ["t1"]
    fwd["calls"]["untraced"] = {"forward": 3}
    (ev / "demo" / "control-forward.json").write_text(json.dumps({"summary": fwd}))
    with pytest.raises(RuntimeError, match="not tagged"):
        census.cmd_coverage("demo")


def test_a_wrong_control_without_an_outcome_keeps_the_empty_flip_and_says_so():
    fwd = summ({"t1": "passed"}, reach={"t1": ["a.py:1"]})
    brk = summ({"t1": "failed"})
    lost = {"runs": [{"__run__": {"outcome": "infra", "message": "no junit for run 0"}}]}
    row = census.controls_table(fwd, brk, None, lost)[0]
    assert row["covered"] and row["flip_tests"] == ["t1"] and row["wrong_control_missing"] == ["t1"]
