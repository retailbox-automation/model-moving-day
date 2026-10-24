import json, subprocess

from model_moving_day import fakes, pr


def test_dry_run_climbs_fixes_publishes_red_and_infra_cells_and_says_it_is_a_dry_run(tmp_path):
    lo, text = fakes.dry_run(tmp_path)
    s = lo["sites"]
    ex = s["app/extract.py:40"]
    assert ex["chosen"] == "Nano" and ex["fix"]["kind"] == "reasoning_off" and ex["fix"]["applied"]
    assert [(c["rung"], c["role"]) for c in ex["cells"]] == [("Nano", "ladder"), ("Nano", "ladder"), ("Lightning", "fallback")]
    assert s["app/summary.py:12"]["chosen"] == "Lightning" and s["app/summary.py:12"]["fallback"]["rung"] == "Super"
    assert s["app/plan.py:88"]["status"] == "red" and s["app/plan.py:88"]["reason"] == "no model passed, the old model stays"
    assert len(s["app/plan.py:88"]["cells"]) == 4
    flaky = s["app/flaky.py:7"]  # the platform kept failing at Nano: published as infra, never climbed
    assert flaky["status"] == "infra" and flaky["chosen"] is None and [c["rung"] for c in flaky["cells"]] == ["Nano"]
    assert "**stays on the old model**" in text and "`app/unused.py:7`" in text and "+reasoning_off" in text
    assert text.startswith("> DRY RUN") and "# DRY RUN: Move" in text and "A human approved" not in text
    assert "infra: 9 runs lost" in text and "not measured: excluded before the run (example exclusion)" in text
    assert json.loads((tmp_path / "_dryrun" / "ladder.json").read_text())["k"] == 3


def test_baseline_price_uses_visible_output_only():
    cell = {"tokens_per_call": [2000.0, 500.0], "reasoning_tokens": 400, "calls": 2}
    assert pr.baseline_usd_per_1000(cell, (0.10, 0.50)) == 1000 * (2000 * 0.10 + 300 * 0.50) / 1e6


def test_commit_goes_to_a_local_branch_only(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "a.py").write_text("x = 1\n")
    (repo / "PR.md").write_text("text\n")
    sha = pr.commit_to_local_branch(repo, "mmd/move", ["a.py"], "Move LLM calls", "PR.md")
    out = subprocess.run(["git", "branch", "--show-current"], cwd=repo, capture_output=True, text=True).stdout.strip()
    assert out == "mmd/move" and len(sha) == 40
    assert subprocess.run(["git", "remote"], cwd=repo, capture_output=True, text=True).stdout.strip() == ""


def test_a_cassette_only_site_is_marked_in_the_pr():
    v = {"status": "chosen", "chosen": "Nano", "model": "m", "fix": None, "fallback": None, "reason": "", "flip_tests": ["t"],
         "flags": {"cassette_only": True}, "cells": []}
    lo = {"sites": {"llm/x.py:1": v}, "rungs": ["Nano"], "k": 5, "margin": 0.2}
    text = pr.render({"name": "llm", "github": "o/r", "commit": "0" * 40, "license": "Apache-2.0"}, lo, {},
                     {"model": "gpt-6-luna", "price": (0.1, 0.5), "source": "s", "retiring_model": "gpt-4o-mini"})
    assert "good side = recorded cassette" in text
