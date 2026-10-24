import ast, json, subprocess

import pytest

from model_moving_day import agent, census, find, freeze, ladder, manifest as mf, patch, tf_client as tf

CORE, VAL = "pkg/core.py:2", "pkg/validate.py:2"
FLIP, VFLIP = "tests/test_core.py::test_x", "tests/test_core.py::test_v"
CORE_SLUG, VAL_SLUG = "pkg_core_py_2", "pkg_validate_py_2"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def reply(tool_calls):
    tcs = [{"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
           for i, (n, a) in enumerate(tool_calls)]
    return tf.Reply(agent.SUPER, "", "", "", False, tcs, "tool_calls", {"prompt_tokens": 10, "completion_tokens": 5}, 0.0, 0.1, {})


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)

    def chat(self, model, messages, **params):
        return self.replies.pop(0)


WRAP_CORE = [("replace", {"path": "pkg/core.py", "old": "    response = func(*args, **kwargs)",
                          "new": f'    response = nemotron_move.call("{CORE_SLUG}", func, *args, **kwargs)'}),
             ("replace", {"path": "pkg/core.py", "old": "def run(", "new": "from pkg import nemotron_move\n\n\ndef run("})]
WRAP_VAL = [("replace", {"path": "pkg/validate.py", "old": "    return client.chat.completions.create(",
                         "new": f'    return nemotron_move.call("{VAL_SLUG}", client.chat.completions.create,'}),
            ("replace", {"path": "pkg/validate.py", "old": "def check(", "new": "from pkg import nemotron_move\n\n\ndef check("})]
FINISH = [("finish", {"summary": "wrapped"})]


@pytest.fixture
def world(tmp_path, monkeypatch):
    clones = tmp_path / "clones"
    repo = clones / "demo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "core.py").write_text("def run(func, *args, **kwargs):\n    response = func(*args, **kwargs)\n    return response\n")
    (repo / "pkg" / "validate.py").write_text('def check(client, text):\n    return client.chat.completions.create(\n'
                                              '        model="gpt-4.1-mini", messages=[{"role": "user", "content": text}])\n')
    (repo / "pkg" / "other.py").write_text("y = 1\n")
    (repo / "tests" / "test_core.py").write_text("def test_x():\n    pass\n\n\ndef test_v():\n    pass\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "i")
    fr = freeze.freeze(repo, [FLIP, VFLIP])
    entry = {"github": "o/demo", "commit": fr.commit, "license": "MIT", "install": [], "node_args": ["-q"], "env": {},
             "helper_package": "pkg", "image": {"uuid": "img", "tag": "t"}, "flip_tests": {CORE: [FLIP], VAL: [VFLIP]},
             "site_flags": {CORE: {"isolated": True}, VAL: {"isolated": True}},
             "frozen": {"commit": fr.commit, "sha256": fr.sha256(), "hashes": fr.hashes, "extra_paths": []}}
    man = {"targets": {"demo": entry}, "rules": mf.rules(), "budgets": {"cap_usd_per_job": 1.0},
           "baseline_before": mf.BASELINE_BEFORE, "not_claimed": ["test traffic"], "amendments": []}
    chosen = {"rung": "Lightning", "role": "ladder", "passes": True, "status": "pass", "params": {},
              "changes": {"model:model": 4, "dropped:seed": 4}, "model": ladder.LIGHT, "passed_runs": 5, "valid_runs": 5,
              "infra_runs": 0, "wilson95": [0.57, 1.0], "tokens_per_call": [100.0, 20.0], "reasoning_tokens": 0, "calls": 10,
              "usd_per_1000_calls": 0.01, "usd_per_passed_run": 0.00001, "diagnoses": {}, "first_failure": ""}
    fb = dict(chosen, rung="Super", role="fallback", model=ladder.SUPER, changes={"model:model": 4})
    red = dict(chosen, passes=False, status="fail", passed_runs=0)
    doc = {"run_id": "r1", "kind": "decision", "status": "complete", "manifest": {"sha256": "msha"}, "k": 5, "margin": 0.2,
           "rungs": ["Nano", "Lightning", "Super", "Ultra"], "baseline_note": "assumed 1.0", "excluded_sites": {},
           "started_utc": "2026-10-24T12:00:00Z",
           "sites": {CORE: {"status": "chosen", "chosen": "Lightning", "model": ladder.LIGHT, "fix": None,
                            "fallback": {"rung": "Super", "model": ladder.SUPER, "passes": True, "status": "pass"},
                            "reason": "", "flip_tests": [FLIP], "flags": {}, "cells": [chosen, fb]},
                     VAL: {"status": "red", "chosen": None, "model": None, "fix": None, "fallback": None,
                           "reason": "no model passed, the old model stays", "flip_tests": [VFLIP], "flags": {},
                           "cells": [dict(red, rung=r) for r in ("Nano", "Lightning", "Super", "Ultra")]}}}
    out = tmp_path / "evidence"
    (out / "demo").mkdir(parents=True)
    (out / "demo" / "coverage.json").write_text(json.dumps({"traced_sites": [], "ast_sites_not_reached": []}))
    targets = tmp_path / "targets.json"
    targets.write_text(json.dumps({"demo": {"retiring_model": "gpt-4.1-mini"}}))
    runs = [(doc, out / "demo" / "ladder-r1.json")]
    monkeypatch.setattr(mf, "load", lambda path=mf.PATH: (man, "msha"))
    monkeypatch.setattr(mf, "verify", lambda m, name, clones: [])
    monkeypatch.setattr(mf, "TARGETS", targets)
    monkeypatch.setattr(census, "EVIDENCE", out)
    monkeypatch.setattr(census, "ladder_runs", lambda name, kind="decision": runs)
    monkeypatch.setattr(patch, "UNKNOWN_MODEL_EVIDENCE", tmp_path / "absent.json")
    return {"clones": clones, "repo": repo, "out": out, "doc": doc, "man": man, "runs": runs}


def confirm_job(seen, core_bt=None, val_bt=None):
    """A confirm job: the moved site's calls went to /moved/ with Lightning, the guarded site's to the old model."""
    core_bt = core_bt or {"requested": {ladder.LIGHT: 2}, "modes": {"passthrough": 2}, "status": {"200": 2}, "moved": 2}
    val_bt = val_bt or {"requested": {"gpt-4.1-mini": 1}, "modes": {"old-model": 1}, "status": {"200": 1}, "moved": 0}

    def run_job(box, image, t, routes, k, cap_usd, ledger, **kw):
        seen.append({"routes": routes, "k": k, "tests": [a for a in t.pytest_args if "::" in a], **kw})
        runs = [{FLIP: {"outcome": "passed", "message": ""}, VFLIP: {"outcome": "failed", "message": "old model"}}
                for _ in range(k)]
        site = {"calls": 2 * k, "prompt_tokens": 100, "completion_tokens": 20, "changes": {}}
        summary = {"runs": runs, "exit_codes": [0] * k, "freeze_before": [], "freeze_after": [], "envelope_spent": 0.0,
                   "calls": {"tests": {FLIP: [CORE], VFLIP: [VAL]}, "sites": {CORE: site, VAL: dict(site)}, "untraced": {},
                             "by_test": {FLIP: core_bt, VFLIP: val_bt}}}

        class R:
            cost, elapsed_s, wall_s, exit_code = 0.0, 1.0, 1.0, 0
        return R(), summary
    return run_job


def brain(world, steps):
    return agent.Brain(FakeClient([reply(s) for s in steps]), agent.SUPER, world["out"] / "steps.jsonl", log=lambda m: None)


def run_patch(world, steps, job=None, **kw):
    return patch.cmd_patch("demo", clones=world["clones"], brain=brain(world, steps), run_job=job or confirm_job([]),
                           box=object(), ledger=None, check_pushed=False, **kw)


def assert_clone_untouched(world):
    repo = world["repo"]
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main" and git(repo, "status", "--porcelain") == ""
    assert len(git(repo, "worktree", "list").splitlines()) == 1  # the patch worktree is gone


def test_sdk_params_translate_the_measured_route_and_changes():
    cell = {"params": {"reasoning": "off", "min_max_tokens": 8000},
            "changes": {"model:model": 3, "dropped:seed": 3, "dropped:prefill": 1, "reasoning:enable_thinking": 3}}
    cfg = patch.sdk_params(cell, ladder.NANO)
    assert cfg["params"] == {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    assert cfg["drop"] == ["seed"] and cfg["min_max_tokens"] == 8000 and "prefill" in cfg["notes"][0]
    ultra = patch.sdk_params({"params": {}, "changes": {"reasoning:chat_template_kwargs": 2}}, ladder.ULTRA)
    assert ultra["params"]["extra_body"]["chat_template_kwargs"] == {"enable_thinking": True, "force_nonempty_content": True}


def test_slugs_use_the_whole_path_and_collisions_are_refused(tmp_path):
    """N14: two modules with the same name and line in different packages."""
    assert patch.slug("a/x.py:3") != patch.slug("b/x.py:3") and patch.slug(CORE) == CORE_SLUG
    for rel in ("a_b/x.py", "a/b_x.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("1\n2\n3\n")
    doc = {"sites": {s: {"status": "chosen", "model": ladder.LIGHT,
                         "cells": [{"role": "ladder", "passes": True, "params": {}, "changes": {}}]}
                     for s in ("a_b/x.py:3", "a/b_x.py:3")}}
    with pytest.raises(patch.PatchError, match="collide"):
        patch.plan(doc, tmp_path, [])


def test_a_moved_funnel_gets_guards_on_the_chat_sites_that_stay(world):
    """N2: the validator stayed on the old model; its calls pass through the funnel, so it is wrapped as a guard."""
    p = patch.plan(world["doc"], world["repo"], find.scan(world["repo"]).sites)
    kinds = {e["site"]: e["kind"] for e in p["sites"]}
    assert kinds == {CORE: "move", VAL: "keep"}
    core = next(e for e in p["sites"] if e["site"] == CORE)
    assert core["config"]["only"] == patch.CHAT_SHAPE and core["config"]["fallback"]["model"] == ladder.SUPER


def test_patch_moves_the_confirmed_site_guards_the_other_and_commits_only_to_a_local_branch(world):
    seen = []
    res = run_patch(world, [WRAP_CORE + WRAP_VAL + FINISH], confirm_job(seen))
    job = seen[0]
    assert job["routes"]["default"] == {"mode": "old-model"} and job["routes"]["moved"] == {"mode": "passthrough"}
    assert job["env"]["NEMOTRON_MOVE"] == "1" and job["env"]["NEMOTRON_MOVE_BASE_URL"].endswith("/moved/v1")
    assert set(job["extra_files"]) == {"pkg/core.py", "pkg/validate.py", "pkg/nemotron_move.py"} and job["require_frozen"]
    assert sorted(job["tests"]) == sorted([FLIP, VFLIP]) and job["k"] == 5
    assert res["moved"] == [CORE] and res["kept"] == {VAL: "guard"} and res["branch"] == patch.BRANCH
    assert not res["pr"].startswith("/")  # no local absolute paths in published evidence
    assert res["confirm"] == {CORE: "pass", VAL: "guard holds"}
    assert_clone_untouched(world)
    repo = world["repo"]
    assert git(repo, "remote") == "" and git(repo, "log", "-1", "--format=%s", patch.BRANCH).startswith("Move 1 LLM call")
    helper = git(repo, "show", f"{patch.BRANCH}:pkg/nemotron_move.py")
    tree = ast.parse(helper)
    sites = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == "SITES")
    assert sites[CORE_SLUG]["model"] == ladder.LIGHT and sites[CORE_SLUG]["drop"] == ["seed"]
    assert sites[CORE_SLUG]["fallback"]["model"] == ladder.SUPER and sites[VAL_SLUG] == {"keep": True}
    assert "assumed, not measured" in helper  # N12: no evidence of Token Factory's answer to an unknown ID yet
    text = (world["out"] / "demo" / "patch" / "PR.md").read_text()
    assert "## Configuration" in text and "NEMOTRON_MOVE_BASE_URL" in text and "Wrapped but not moved (1)" in text
    assert "## Ladder runs of this repository" in text and "`r1`" in text and "No human has reviewed" in text


@pytest.mark.parametrize("core_bt, problem", [
    ({"requested": {"gpt-4.1-mini": 2}, "modes": {"old-model": 2}, "status": {"200": 2}, "moved": 0}, "not moved"),
    ({"requested": {"gpt-4.1-mini": 1, ladder.LIGHT: 1}, "modes": {"passthrough": 2}, "status": {"200": 1, "400": 1},
      "moved": 2}, "400"),
    ({"requested": {"nvidia/other": 2}, "modes": {"passthrough": 2}, "status": {"200": 2}, "moved": 2}, "other models"),
], ids=["unmoved", "refused-400", "wrong-model"])
def test_a_site_whose_calls_were_not_routed_as_planned_is_not_moved_and_nothing_is_committed(world, core_bt, problem):
    """N1/N7/N8: the routing check reads the calls of the site's own flip tests; a site that fails it stays."""
    res = run_patch(world, [WRAP_CORE + WRAP_VAL + FINISH], confirm_job([], core_bt=core_bt))
    assert res["moved"] == [] and res["branch"] is None and res["confirm"][CORE] == "calls not routed as planned"
    confirm_file = sorted((world["out"] / "demo").glob("ladder-*.json"))[-1]
    cell = json.loads(confirm_file.read_text())["sites"][CORE]["cells"][-1]
    assert cell["role"] == "confirm" and any(problem in p for p in cell["routing_problems"])
    plan = json.loads((world["out"] / "demo" / "patch" / "plan.json").read_text())
    assert next(e for e in plan["sites"] if e["site"] == CORE)["kind"] == "keep"
    with pytest.raises(subprocess.CalledProcessError):
        git(world["repo"], "rev-parse", "--verify", patch.BRANCH)
    assert_clone_untouched(world)


def test_a_guard_that_leaked_is_reported(world):
    leaked = {"requested": {ladder.LIGHT: 1}, "modes": {"passthrough": 1}, "status": {"200": 1}, "moved": 1}
    res = run_patch(world, [WRAP_CORE + WRAP_VAL + FINISH], confirm_job([], val_bt=leaked))
    assert res["confirm"][VAL] == "guard leaked"


def test_a_patch_that_touches_files_outside_the_plan_is_refused_and_the_clone_stays_clean(world):
    stray = [("replace", {"path": "pkg/other.py", "old": "y = 1", "new": "y = 2"})] + WRAP_CORE + WRAP_VAL + FINISH
    with pytest.raises(patch.PatchError, match="outside the plan"):
        run_patch(world, [stray])
    assert_clone_untouched(world)


def test_any_other_code_change_in_a_planned_file_is_refused(world):
    """N7: unwrapping the helper calls must give back the original syntax tree, so NEMOTRON_MOVE unset = original."""
    sneaky = WRAP_CORE + [("replace", {"path": "pkg/core.py", "old": "    return response", "new": "    return None"})] + WRAP_VAL + FINISH
    with pytest.raises(patch.PatchError, match="changed the code"):
        run_patch(world, [sneaky])


def test_an_edited_helper_is_refused(world):
    edit_helper = [("replace", {"path": "pkg/nemotron_move.py", "old": "RETRY_LISTING_S = 300",
                                "new": "RETRY_LISTING_S = 301"})] + WRAP_CORE + WRAP_VAL + FINISH
    with pytest.raises(patch.PatchError, match="helper differs"):
        run_patch(world, [edit_helper])


def test_an_unwrapped_site_gets_one_more_try_then_the_patch_is_refused(world):
    with pytest.raises(patch.PatchError, match="not wrapped"):
        run_patch(world, [WRAP_CORE + FINISH, FINISH])
    assert_clone_untouched(world)


def test_a_slug_only_in_a_comment_does_not_count_as_wrapped(world):
    comment = WRAP_CORE + [("replace", {"path": "pkg/validate.py", "old": "def check(",
                                        "new": f'# "{VAL_SLUG}"\ndef check('})] + FINISH
    with pytest.raises(patch.PatchError):
        run_patch(world, [comment, FINISH])


def test_several_decision_runs_need_an_explicit_choice_and_all_are_listed(world):
    """N10: the newest complete run is not taken silently."""
    second = dict(world["doc"], run_id="r2", started_utc="2026-10-24T13:00:00Z")
    world["runs"].append((second, world["out"] / "demo" / "ladder-r2.json"))
    with pytest.raises(patch.PatchError, match="--ladder-run"):
        run_patch(world, [WRAP_CORE + WRAP_VAL + FINISH])
    res = run_patch(world, [WRAP_CORE + WRAP_VAL + FINISH], ladder_run="r1")
    text = (world["out"] / "demo" / "patch" / "PR.md").read_text()
    assert res["ladder_run"] == "r1" and "`r2`" in text and "| `r1` | 2026-10-24T12:00:00Z | yes |" in text


def test_a_run_under_an_earlier_manifest_counts_only_if_its_rules_and_target_entry_are_unchanged(world, monkeypatch):
    world["doc"]["manifest"]["sha256"] = "older"
    monkeypatch.setattr(mf, "version", lambda sha, path=mf.PATH: None)
    with pytest.raises(patch.PatchError, match="no commit"):
        run_patch(world, [WRAP_CORE + WRAP_VAL + FINISH])
    other = json.loads(json.dumps(world["man"]))
    other["rules"]["k"] = 2
    monkeypatch.setattr(mf, "version", lambda sha, path=mf.PATH: other)
    with pytest.raises(patch.PatchError, match="other rules"):
        run_patch(world, [WRAP_CORE + WRAP_VAL + FINISH])
    monkeypatch.setattr(mf, "version", lambda sha, path=mf.PATH: json.loads(json.dumps(world["man"])))
    assert run_patch(world, [WRAP_CORE + WRAP_VAL + FINISH])["moved"] == [CORE]


def test_a_fallback_cell_that_did_not_pass_gives_no_fallback(tmp_path):
    """M7: the helper may fall back only to a measured fallback cell that passed."""
    chosen = {"role": "ladder", "passes": True, "params": {}, "changes": {"model:model": 2}}
    failed_fb = {"role": "fallback", "passes": False, "params": {}, "changes": {}, "model": ladder.SUPER}
    doc = {"sites": {CORE: {"status": "chosen", "model": ladder.LIGHT, "cells": [chosen, failed_fb]}}}
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text("def run(func, *args, **kwargs):\n    response = func(*args, **kwargs)\n")
    p = patch.plan(doc, tmp_path, [])
    assert p["sites"][0]["config"]["fallback"] is None and p["sites"][0]["fallback"] is None
    doc["sites"][CORE]["cells"][1] = dict(failed_fb, passes=True)
    assert patch.plan(doc, tmp_path, [])["sites"][0]["config"]["fallback"]["model"] == ladder.SUPER


def test_the_retired_status_comes_from_evidence_when_measured(tmp_path, monkeypatch):
    ev = tmp_path / "unknown-model.json"
    ev.write_text(json.dumps({"statuses": [404]}))
    monkeypatch.setattr(patch, "UNKNOWN_MODEL_EVIDENCE", ev)
    src = patch.render_helper({"sites": []})
    assert "RETIRED_STATUSES = {404}  # Token Factory's answer to an unknown model ID: measured" in src


def test_a_helper_import_in_another_form_is_refused(world):
    """2026-10-24, instructor attempt 3: `from instructor.nemotron_move import nemotron_move` passed the old check
    and every confirm run failed at collection."""
    bad_import = [WRAP_CORE[0], ("replace", {"path": "pkg/core.py", "old": "def run(",
                                              "new": "from pkg.nemotron_move import nemotron_move\n\n\ndef run("})] + WRAP_VAL + FINISH
    with pytest.raises(patch.PatchError, match="changed the code"):
        run_patch(world, [bad_import])


def test_the_brain_is_told_the_exact_import_line():
    seen = []

    class Rec:
        def chat(self, model, messages, **params):
            seen.append(messages[0]["content"])
            return reply([("finish", {"summary": "x"})])
    import tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp())
    fr = freeze.Frozen("c", [], {}, [])
    agent.Brain(Rec(), agent.SUPER, d / "s.jsonl", log=lambda m: None).write_patch(d, fr, {"sites": []}, "paperqa.nemotron_move")
    assert "`from paperqa import nemotron_move`" in seen[0]
