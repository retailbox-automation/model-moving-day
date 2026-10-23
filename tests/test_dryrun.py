import json, subprocess

from model_moving_day import fakes, pr


def test_dry_run_climbs_fixes_and_publishes_red_cells(tmp_path):
    lo, text = fakes.dry_run(tmp_path)
    s = lo["sites"]
    assert s["app/extract.py:40"]["chosen"] == "Nano" and s["app/extract.py:40"]["fix"]["kind"] == "reasoning_off"
    assert [c["rung"] for c in s["app/extract.py:40"]["cells"]] == ["Nano", "Nano"]  # red cell kept, then the fixed one
    assert s["app/summary.py:12"]["chosen"] == "Lightning"
    assert s["app/plan.py:88"]["chosen"] is None and s["app/plan.py:88"]["reason"] == "no model passed, the old model stays"
    assert len([c for c in s["app/plan.py:88"]["cells"]]) == 4
    assert "**stays on the old model**" in text and "`app/unused.py:7`" in text and "+reasoning_off" in text
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
