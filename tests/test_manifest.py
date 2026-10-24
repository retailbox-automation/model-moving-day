import json, subprocess

import pytest

from model_moving_day import freeze, ladder, manifest as mf


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A target with a clone, targets.json and evidence, wired into the manifest module."""
    clones = tmp_path / "clones"
    repo = clones / "demo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "pkg" / "a.py").write_text("x = 1\n")
    (repo / "tests" / "test_a.py").write_text("def test_one():\n    pass\n")
    (repo / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "i")
    commit = git(repo, "rev-parse", "HEAD")
    targets = tmp_path / "targets.json"
    targets.write_text(json.dumps({"demo": {"github": "o/demo", "commit": commit, "license": "MIT", "install": [],
                                            "pytest_args": ["tests"], "node_args": ["-q"], "helper_package": "pkg",
                                            "site_exclusions": {"pkg/b.py:9": "Responses API"}}}))
    ev = tmp_path / "evidence" / "demo"
    ev.mkdir(parents=True)
    (ev / "image.json").write_text(json.dumps({"image_uuid": "img-1", "tag": "mmd-demo"}))
    (ev / "control-forward.json").write_text("{}")
    cov = {"traced_sites": [{"site": "pkg/a.py:1", "flip_tests": ["tests/test_a.py::test_one"], "covered": True,
                             "isolated": True, "good_side": "forward", "cassette_only": False, "wrong_control": False}],
           "ast_sites_not_reached": ["pkg/b.py:9"], "covered_sites": ["pkg/a.py:1"]}
    (ev / "coverage.json").write_text(json.dumps(cov))
    monkeypatch.setattr(mf, "TARGETS", targets)
    monkeypatch.setattr(mf, "EVIDENCE", tmp_path / "evidence")
    return clones, repo, ev


def test_a_fresh_manifest_verifies_and_holds_flips_frozen_hashes_and_rules(world):
    clones, repo, ev = world
    man = mf.build(["demo"], clones, "first version", {"limit": "40"})
    assert mf.verify(man, "demo", clones) == []
    t = man["targets"]["demo"]
    assert t["flip_tests"] == {"pkg/a.py:1": ["tests/test_a.py::test_one"]} and t["image"]["uuid"] == "img-1"
    assert set(t["frozen"]["hashes"]) == {"tests/test_a.py", "pyproject.toml"} and t["excluded_sites"] == {"pkg/b.py:9": "Responses API"}
    assert t["ast_sites_not_reached"] == []  # the excluded site is listed with its reason, not as 'not reached'
    r = man["rules"]
    assert (r["k"], r["margin"], r["required_passes"]) == (5, 0.2, 4)
    assert [x["model"] for x in r["rungs"]] == [m for _, m in ladder.RUNGS]
    assert man["smoke"][0]["id"] == "S-instructor-super-k5" and man["smoke"][0]["k"] == 5
    assert "not for decision" in man["smoke"][0]["purpose"]


@pytest.mark.parametrize("tamper, expected", [
    (lambda man, clones, repo, ev: man["rules"].__setitem__("k", 2), "rule k differs"),
    (lambda man, clones, repo, ev: man["rules"]["rungs"][0].__setitem__("model", "nvidia/other"), "rule rungs differs"),
    (lambda man, clones, repo, ev: (ev / "coverage.json").write_text(json.dumps({
        "traced_sites": [], "ast_sites_not_reached": [], "covered_sites": []})), "demo.flip_tests differs"),
    (lambda man, clones, repo, ev: (repo / "tests" / "test_a.py").write_text("def test_one():\n    assert 0\n"), "demo.frozen differs"),
    (lambda man, clones, repo, ev: (repo / "pytest.ini").write_text("[pytest]\naddopts = --deselect x\n"), "demo.frozen differs"),
    (lambda man, clones, repo, ev: man["code"]["files_sha256"].__setitem__("model_moving_day/census.py", "0" * 64),
     "code changed since the manifest"),
], ids=["k", "model", "flip-tests", "test-file", "pytest-config", "code"])
def test_any_difference_is_refused(world, tamper, expected):
    clones, repo, ev = world
    man = mf.build(["demo"], clones, "first version", {})
    tamper(man, clones, repo, ev)
    problems = mf.verify(man, "demo", clones)
    assert any(expected in p for p in problems), problems


def test_a_clone_moved_off_the_pinned_commit_is_refused(world):
    clones, repo, ev = world
    man = mf.build(["demo"], clones, "first version", {})
    (repo / "pkg" / "a.py").write_text("x = 2\n")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-am", "moved")
    assert any("the manifest pins" in p for p in mf.verify(man, "demo", clones))


def test_amendments_keep_the_previous_sha(world, tmp_path):
    clones, repo, ev = world
    first = mf.build(["demo"], clones, "first version", {})
    sha1 = mf.write(first, tmp_path / "M.json")
    second = mf.build(["demo"], clones, "add a target", {}, previous=first)
    assert second["amendments"] == [{"utc": second["written_utc"], "previous_sha256": sha1, "reason": "add a target"}]
    assert second["first_written_utc"] == first["first_written_utc"]


def test_pushed_requires_the_manifest_commit_on_the_remote_branch(tmp_path, monkeypatch):
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(tmp_path, "clone", "-q", str(remote), str(work))
    git(work, "checkout", "-q", "-b", "lane-c1")
    (work / "census").mkdir()
    (work / "census" / "MANIFEST.json").write_text("{}\n")
    git(work, "add", ".")
    git(work, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "manifest")
    monkeypatch.setattr(mf, "ROOT", work)
    path = work / "census" / "MANIFEST.json"
    with pytest.raises(mf.ManifestMismatch, match="push it"):
        mf.pushed(path)
    git(work, "push", "-q", "origin", "lane-c1")
    proof = mf.pushed(path)
    assert proof["commit"] == git(work, "rev-parse", "HEAD") == proof["remote_head"] and proof["branch"] == "lane-c1"
    path.write_text('{"k": 2}\n')
    with pytest.raises(mf.ManifestMismatch, match="uncommitted"):
        mf.pushed(path)
