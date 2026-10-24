import ast, json, subprocess

import pytest

from model_moving_day import agent, census, freeze, ladder, manifest as mf, patch, pr, tf_client as tf

SITE, FLIP = "pkg/core.py:2", "tests/test_core.py::test_x"


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


WRAP = [reply([("replace", {"path": "pkg/core.py", "old": "    response = func(*args, **kwargs)",
                            "new": '    response = nemotron_move.call("core_2", func, *args, **kwargs)'}),
               ("replace", {"path": "pkg/core.py", "old": "def run(", "new": "from pkg import nemotron_move\n\n\ndef run("}),
               ("finish", {"summary": "wrapped"})])]


@pytest.fixture
def world(tmp_path, monkeypatch):
    clones = tmp_path / "clones"
    repo = clones / "demo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "core.py").write_text("def run(func, *args, **kwargs):\n    response = func(*args, **kwargs)\n    return response\n")
    (repo / "pkg" / "other.py").write_text("y = 1\n")
    (repo / "tests" / "test_core.py").write_text("def test_x():\n    pass\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "i")
    fr = freeze.freeze(repo, [FLIP])
    entry = {"github": "o/demo", "commit": fr.commit, "license": "MIT", "install": [], "node_args": ["-q"], "env": {},
             "helper_package": "pkg", "image": {"uuid": "img", "tag": "t"}, "flip_tests": {SITE: [FLIP]},
             "frozen": {"commit": fr.commit, "sha256": fr.sha256(), "hashes": fr.hashes, "extra_paths": []}}
    man = {"targets": {"demo": entry}, "rules": mf.rules(), "budgets": {"cap_usd_per_job": 1.0},
           "baseline_before": mf.BASELINE_BEFORE, "not_claimed": ["test traffic"]}
    chosen = {"rung": "Lightning", "role": "ladder", "passes": True, "status": "pass", "params": {},
              "changes": {"model:model": 4, "dropped:seed": 4}, "model": ladder.LIGHT, "passed_runs": 5, "valid_runs": 5,
              "infra_runs": 0, "wilson95": [0.57, 1.0], "tokens_per_call": [100.0, 20.0], "reasoning_tokens": 0, "calls": 10,
              "usd_per_1000_calls": 0.01, "usd_per_passed_run": 0.00001, "diagnoses": {}, "first_failure": ""}
    fb = dict(chosen, rung="Super", role="fallback", model=ladder.SUPER, changes={"model:model": 4})
    doc = {"run_id": "r1", "kind": "decision", "status": "complete", "manifest": {"sha256": "msha"}, "k": 5, "margin": 0.2,
           "rungs": ["Nano", "Lightning", "Super", "Ultra"], "baseline_note": "assumed 1.0", "excluded_sites": {},
           "sites": {SITE: {"status": "chosen", "chosen": "Lightning", "model": ladder.LIGHT, "fix": None,
                            "fallback": {"rung": "Super", "model": ladder.SUPER, "passes": True, "status": "pass"},
                            "reason": "", "flip_tests": [FLIP], "cells": [chosen, fb]}}}
    out = tmp_path / "evidence"
    (out / "demo").mkdir(parents=True)
    (out / "demo" / "coverage.json").write_text(json.dumps({"traced_sites": [], "ast_sites_not_reached": []}))
    targets = tmp_path / "targets.json"
    targets.write_text(json.dumps({"demo": {"retiring_model": "gpt-4.1-mini"}}))
    monkeypatch.setattr(mf, "load", lambda path=mf.PATH: (man, "msha"))
    monkeypatch.setattr(mf, "verify", lambda m, name, clones: [])
    monkeypatch.setattr(mf, "TARGETS", targets)
    monkeypatch.setattr(census, "EVIDENCE", out)
    monkeypatch.setattr(census, "latest_ladder", lambda name, kind="decision": (doc, out / "demo" / "ladder-r1.json"))
    monkeypatch.setattr(census, "ast_sites", lambda name: type("S", (), {"sites": []})())
    monkeypatch.setattr(patch, "UNKNOWN_MODEL_EVIDENCE", tmp_path / "absent.json")
    return clones, repo, out


def passing_job(seen):
    def run_job(box, image, t, routes, k, cap_usd, ledger, **kw):
        seen.append({"routes": routes, "k": k, **kw})
        runs = [{FLIP: {"outcome": "passed", "message": ""}} for _ in range(k)]
        site = {"calls": 2 * k, "prompt_tokens": 100, "completion_tokens": 20, "models": {ladder.LIGHT: 2 * k}, "changes": {}}
        summary = {"runs": runs, "exit_codes": [0] * k, "freeze_before": [], "freeze_after": [], "envelope_spent": 0.0,
                   "calls": {"tests": {FLIP: [SITE]}, "sites": {SITE: site}, "untraced": {}}}

        class R:
            cost, elapsed_s, wall_s, exit_code = 0.0, 1.0, 1.0, 0
        return R(), summary
    return run_job


def test_sdk_params_translate_the_measured_route_and_changes():
    cell = {"params": {"reasoning": "off", "min_max_tokens": 8000},
            "changes": {"model:model": 3, "dropped:seed": 3, "dropped:prefill": 1, "reasoning:enable_thinking": 3}}
    cfg = patch.sdk_params(cell, ladder.NANO)
    assert cfg["params"] == {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    assert cfg["drop"] == ["seed"] and cfg["min_max_tokens"] == 8000 and "prefill" in cfg["notes"][0]
    ultra = patch.sdk_params({"params": {}, "changes": {"reasoning:chat_template_kwargs": 2}}, ladder.ULTRA)
    assert ultra["params"]["extra_body"]["chat_template_kwargs"] == {"enable_thinking": True, "force_nonempty_content": True}


def test_patch_wraps_the_site_confirms_through_passthrough_and_commits_to_a_local_branch_only(world):
    clones, repo, out = world
    seen = []
    brain = agent.Brain(FakeClient(WRAP), agent.SUPER, out / "steps.jsonl", log=lambda m: None)
    res = patch.cmd_patch("demo", clones=clones, brain=brain, run_job=passing_job(seen), box=object(),
                          ledger=None, check_pushed=False)
    job = seen[0]
    assert job["routes"]["default"] == {"mode": "passthrough"} and job["k"] == 5
    assert job["env"]["NEMOTRON_MOVE"] == "1" and job["env"]["NEMOTRON_MOVE_API_KEY"] == "mmd-proxy"
    assert set(job["extra_files"]) == {"pkg/core.py", "pkg/nemotron_move.py"} and job["require_frozen"]
    assert res["confirm"] == {SITE: "pass"} and res["branch"] == patch.BRANCH
    # the clone is back on the pinned commit; the patch lives only on the local branch, with no remote
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main" and git(repo, "status", "--porcelain") == ""
    assert git(repo, "remote") == ""
    helper = git(repo, "show", f"{patch.BRANCH}:pkg/nemotron_move.py")
    tree = ast.parse(helper)
    sites = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == "SITES")
    assert sites["core_2"]["model"] == ladder.LIGHT and sites["core_2"]["drop"] == ["seed"]
    assert sites["core_2"]["fallback"]["model"] == ladder.SUPER
    text = (out / "demo" / "patch" / "PR.md").read_text()
    assert "Confirm run of the patched app" in text and "No human has reviewed" in text and "test traffic" in text


def test_a_patch_that_touches_files_outside_the_plan_is_refused(world):
    clones, repo, out = world
    stray = [reply([("replace", {"path": "pkg/other.py", "old": "y = 1", "new": "y = 2"}),
                    ("replace", {"path": "pkg/core.py", "old": "    response = func(*args, **kwargs)",
                                 "new": '    response = nemotron_move.call("core_2", func, *args, **kwargs)'}),
                    ("finish", {"summary": "x"})])]
    brain = agent.Brain(FakeClient(stray), agent.SUPER, out / "steps.jsonl", log=lambda m: None)
    with pytest.raises(patch.PatchError, match="outside the plan"):
        patch.cmd_patch("demo", clones=clones, brain=brain, run_job=passing_job([]), box=object(), ledger=None,
                        check_pushed=False)


def test_an_unwrapped_site_gets_one_more_try_then_the_patch_is_refused(world):
    clones, repo, out = world
    lazy = [reply([("finish", {"summary": "done"})]), reply([("finish", {"summary": "done"})])]
    brain = agent.Brain(FakeClient(lazy), agent.SUPER, out / "steps.jsonl", log=lambda m: None)
    with pytest.raises(patch.PatchError, match="not wrapped"):
        patch.cmd_patch("demo", clones=clones, brain=brain, run_job=passing_job([]), box=object(), ledger=None,
                        check_pushed=False)


def test_the_confirm_run_fails_when_the_helper_sent_another_model(world):
    clones, repo, out = world
    seen = []
    base = passing_job(seen)

    def other_model(*a, **kw):
        r, s = base(*a, **kw)
        s["calls"]["sites"][SITE]["models"] = {"nvidia/other": 2}
        return r, s
    brain = agent.Brain(FakeClient(WRAP), agent.SUPER, out / "steps.jsonl", log=lambda m: None)
    res = patch.cmd_patch("demo", clones=clones, brain=brain, run_job=other_model, box=object(), ledger=None,
                          check_pushed=False)
    assert res["confirm"] == {SITE: "helper sent another model"}


def test_a_fallback_cell_that_did_not_pass_gives_no_fallback(tmp_path):
    """M7: the helper may fall back only to a measured fallback cell that passed."""
    chosen = {"role": "ladder", "passes": True, "params": {}, "changes": {"model:model": 2}}
    failed_fb = {"role": "fallback", "passes": False, "params": {}, "changes": {}, "model": ladder.SUPER}
    doc = {"sites": {SITE: {"status": "chosen", "model": ladder.LIGHT, "cells": [chosen, failed_fb]}}}
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text("def run(func, *args, **kwargs):\n    response = func(*args, **kwargs)\n")
    p = patch.plan(doc, tmp_path)
    assert p["sites"][0]["config"]["fallback"] is None and p["sites"][0]["fallback"] is None
    doc["sites"][SITE]["cells"][1] = dict(failed_fb, passes=True)
    assert patch.plan(doc, tmp_path)["sites"][0]["config"]["fallback"]["model"] == ladder.SUPER


def test_a_ladder_run_under_an_unknown_manifest_is_refused_but_an_amended_one_is_accepted(world, monkeypatch):
    clones, repo, out = world
    man, _ = mf.load()
    doc, path = census.latest_ladder("demo")
    doc["manifest"]["sha256"] = "older"
    with pytest.raises(patch.PatchError, match="neither"):
        patch.cmd_patch("demo", clones=clones, brain=None, run_job=passing_job([]), box=object(), ledger=None,
                        check_pushed=False)
    man["amendments"] = [{"previous_sha256": "older", "reason": "added another target"}]
    brain = agent.Brain(FakeClient(WRAP), agent.SUPER, out / "steps.jsonl", log=lambda m: None)
    res = patch.cmd_patch("demo", clones=clones, brain=brain, run_job=passing_job([]), box=object(), ledger=None,
                          check_pushed=False)
    assert res["confirm"] == {SITE: "pass"}
