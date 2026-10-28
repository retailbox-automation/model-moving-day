import json, shutil, subprocess, sys
from pathlib import Path

import pytest

from model_moving_day import cli, scan

FIX = Path(__file__).parent / "fixtures" / "scan_repo"
NOW = scan.datetime.datetime(2026, 10, 28, 20, 0, tzinfo=scan.datetime.timezone.utc)


@pytest.mark.parametrize("s,want", [
    ("gpt-4o", "gpt-4o"), ("gpt-4o-2024-08-06", "gpt-4o-2024-08-06"), ("gpt-4o-2024-11-20", "gpt-4o-2024-11-20"),
    ("gpt-4o-mini", "gpt-4o-mini"), ("gpt-4o-mini-2024-07-18", "gpt-4o-mini-2024-07-18"), ("gpt-4.1", "gpt-4.1"),
    ("gpt-4.1-2025-04-14", "gpt-4.1-2025-04-14"), ("gpt-4.1-mini", "gpt-4.1-mini"),
    ("gpt-4.1-mini-2025-04-14", "gpt-4.1-mini-2025-04-14"),
    ("openai/gpt-4o", "gpt-4o"), ("azure/gpt-4.1-mini", "gpt-4.1-mini"), ("openrouter/openai/gpt-4o-mini", "gpt-4o-mini"),
    ("gpt-4o-mini-tts", None), ("gpt-4o-audio", None), ("gpt-4o-audio-preview", None), ("gpt-4.1-nano", None),
    ("gpt-4o-realtime-preview", None), ("gpt-4o-transcribe", None), ("gpt-4o-search-preview", None),
    ("gpt-4o-2024-05-13", None), ("GPT-4o", None), (" gpt-4o", None), ("gpt-4o ", None), ("openrouter/gpt-4o", None),
    ("openai/openai/gpt-4o", None), ("azure/openai/gpt-4o", None), ("models/gpt-4o", None), ("use gpt-4o", None),
    ("", None), (None, None), (4, None),
])
def test_match_id(s, want):
    assert scan.match_id(s) == want


def test_retiring_table_is_the_rule():
    assert len(scan.RETIRING) == 9
    assert {i for i, (_, k) in scan.RETIRING.items() if k == "bare"} == {"gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini"}
    assert scan.PREFIXES == ("openai/", "azure/", "openrouter/openai/") and scan.ALREADY_REMOVED == "gpt-4o-2024-05-13"


@pytest.mark.parametrize("rel,want", [
    ("tests/a.py", True), ("pkg/test/a.py", True), ("pkg/testing/util.py", True), ("test_a.py", True),
    ("pkg/test_a.py", True), ("pkg/a_test.py", True), ("conftest.py", True), ("pkg/conftest.py", True),
    ("pkg/a.py", False), ("pkg/testsuite/a.py", False), ("pkg/contest.py", False), ("pkg/latest.py", False),
    ("pkg/test.py", False), ("pkg/mytests/a.py", False),
])
def test_is_test_file(rel, want):
    assert scan.is_test_file(rel) is want


def test_fixture_exact_counts():
    r = scan.scan_repo(FIX)
    assert scan.counts(r) == scan.FIXTURE_COUNTS
    assert [(d["file"], d["id"], d["kind"]) for d in r["source_ids"]] == [
        ("app/client.py", "gpt-4.1-mini-2025-04-14", "snapshot"), ("app/client.py", "gpt-4o", "bare")]
    assert [(d["file"], d["id"]) for d in r["test_ids"]] == [
        ("app/client_test.py", "gpt-4.1"), ("tests/conftest.py", "gpt-4o-mini"), ("tests/test_client.py", "gpt-4o-mini")]
    assert [(d["file"], d["line"], d["id"]) for d in r["test_data_ids"]] == [("tests/cassettes/x.yaml", 3, "gpt-4.1")]
    assert [d["file"] for d in r["already_removed"]] == ["app/client.py"]
    assert [e["file"] for e in r["errors"]] == ["app/broken.py"] and r["skipped_large"] == []


def test_near_misses_docs_and_fstrings_not_counted():
    r = scan.scan_repo(FIX)
    found = {d["id"] for k in ("source_ids", "test_ids", "test_data_ids") for d in r[k]}
    assert not found & {"gpt-4o-mini-tts", "gpt-4.1-nano", "gpt-4o-realtime-preview"}
    assert all(not d["file"].startswith("docs/") for k in ("source_ids", "test_ids") for d in r[k])
    src = (FIX / "app" / "client.py").read_text().splitlines()
    assert [d["line"] for d in r["source_ids"]] == [i for i, l in enumerate(src, 1) if "MODEL = " in l or 'model="gpt-4o"' in l]


@pytest.mark.parametrize("text,ids", [
    ('model: gpt-4o', ["gpt-4o"]), ('"openai/gpt-4o-mini"', ["gpt-4o-mini"]), ("gpt-4.1.", ["gpt-4.1"]),
    ("gpt-4o-mini-tts gpt-4.1-nano gpt-4.1.5 xgpt-4o gpt-4o_x models/gpt-4o GPT-4O", []),
    ("[gpt-4o, gpt-4.1-mini-2025-04-14]", ["gpt-4o", "gpt-4.1-mini-2025-04-14"]),
])
def test_test_data_whole_word(text, ids):
    assert [m.group(1) for m in scan.DATA_RE.finditer(text)] == ids


def test_large_test_data_skipped(tmp_path, monkeypatch):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "big.json").write_text('["gpt-4o"]' + " " * 64)
    (tmp_path / "tests" / "small.json").write_text('["gpt-4o"]')
    (tmp_path / "data.json").write_text('["gpt-4o"]')  # not under a test directory
    monkeypatch.setattr(scan, "MAX_DATA_BYTES", 32)
    r = scan.scan_repo(tmp_path)
    assert r["skipped_large"] == ["tests/big.json"] and [d["file"] for d in r["test_data_ids"]] == ["tests/small.json"]


def cand(name, stars, pushed="2026-10-20T00:00:00Z", **kw):
    return {"fullName": name, "stargazersCount": stars, "isFork": False, "isArchived": False, "pushedAt": pushed,
            "defaultBranch": "main", "license": {"key": "mit"}, "url": f"https://github.com/{name}", **kw}


def test_scan_popular_selection(tmp_path):
    cands = [cand("a/fork", 900, isFork=True), cand("b/arch", 800, isArchived=True),
             cand("c/stale", 700, pushed="2026-04-30T19:59:59Z"), cand("d/ok1", 600), cand("e/nocalls", 500),
             cand("f/fail", 400), cand("g/ok2", 300, pushed="2026-05-01T20:00:01Z"), cand("h/late", 200)]
    raw = json.dumps(cands)
    calls, clones = [], []

    def gh(cmd):
        calls.append(cmd)
        return raw

    def clone(url, dest, branch):
        clones.append((url, branch))
        if "fail" in url:
            raise subprocess.TimeoutExpired("git", 300)
        if "nocalls" in url:
            (dest / "pkg").mkdir(parents=True)
            (dest / "pkg" / "x.py").write_text('M = "gpt-4o"\n')
        else:
            shutil.copytree(FIX, dest)
        return "f" * 40

    out, cl = tmp_path / "out", tmp_path / "clones"
    s = scan.scan_popular(out, n=2, clone_dir=cl, gh=gh, clone=clone, now=NOW)
    rule = (Path(__file__).parents[1] / "evidence" / "scan" / "RULE.md").read_text()
    assert len(calls) == 1 and f"`{' '.join(calls[0])}`" in rule  # the exact query RULE.md fixed
    assert (out / "candidates.json").read_text() == raw
    assert [u for u, _ in clones] == [f"https://github.com/{n}" for n in ("d/ok1", "e/nocalls", "f/fail", "g/ok2")]
    assert all(b == "main" for _, b in clones) and not any(cl.iterdir())  # every clone deleted
    repos = {r["repo"]: r for r in json.loads((out / "repos.json").read_text())}
    assert len(repos) == 8
    assert {n: (r["status"], r["reason"].split(" ")[0]) for n, r in repos.items()} == {
        "a/fork": ("dropped", "fork"), "b/arch": ("dropped", "archived"), "c/stale": ("dropped", "no"),
        "d/ok1": ("kept", "call"), "e/nocalls": ("dropped", "no"), "f/fail": ("dropped", "clone"),
        "g/ok2": ("kept", "call"), "h/late": ("not reached", "2")}
    assert "180 days" in repos["c/stale"]["reason"] and "call site" in repos["e/nocalls"]["reason"]
    assert "TimeoutExpired" in repos["f/fail"]["error"] and repos["d/ok1"]["sha"] == "f" * 40
    results = json.loads((out / "results.json").read_text())
    assert [r["repo"] for r in results] == ["d/ok1", "g/ok2"]
    assert scan.counts(results[0]) == scan.FIXTURE_COUNTS and results[0]["stars"] == 600
    assert (s["K"], s["T"], s["S"], s["U"]) == (2, "2 of 2", "2 of 2", "2 of 2")
    assert (s["clone_errors"], s["parse_errors"], s["repos_with_errors"]) == (1, 2, 2)
    md = (out / "SCAN.md").read_text()
    assert "**T** (retiring ID in a test `.py` file): 2 of 2" in md and "1 failed clones" in md
    assert md.count("| | | | |") == 5 and "2026-10-28T20:00:00+00:00" in md


def test_scan_popular_runs_out_of_candidates(tmp_path):
    raw = json.dumps([cand("d/ok1", 10)])
    s = scan.scan_popular(tmp_path / "o", n=50, clone_dir=tmp_path / "c", gh=lambda c: raw,
                          clone=lambda u, d, b: shutil.copytree(FIX, d) and "a" * 40, now=NOW)
    assert s["K"] == 1 and s["T"] == "1 of 1"


def test_must_light_control_stops_the_run(tmp_path):
    bad = tmp_path / "fixture"
    bad.mkdir()
    (bad / "x.py").write_text("x = 1\n")
    called = []
    with pytest.raises(SystemExit, match="must-light"):
        scan.scan_popular(tmp_path / "o", gh=lambda c: called.append(c) or "[]", now=NOW, fixture=bad)
    assert called == [] and not (tmp_path / "o" / "candidates.json").exists()


def test_git_clone_flags(monkeypatch, tmp_path):
    seen = []

    class Done:
        stdout = "abc\n"

    def run(cmd, **kw):
        seen.append((cmd, kw))
        return Done()

    monkeypatch.setattr(scan.subprocess, "run", run)
    assert scan.git_clone("https://github.com/x/y", tmp_path / "d", "dev") == "abc"
    cmd, kw = seen[0]
    assert cmd[:4] == ["git", "-c", "core.hooksPath=/dev/null", "clone"]
    assert {"--depth", "1", "--no-recurse-submodules", "--single-branch"} <= set(cmd) and cmd[cmd.index("--branch") + 1] == "dev"
    assert kw["timeout"] == 300 and kw["env"]["GIT_TERMINAL_PROMPT"] == "0" and kw["env"]["GIT_LFS_SKIP_SMUDGE"] == "1"


def test_cli_scan_report(capsys):
    assert cli.main(["scan", str(FIX)]) == 0
    out = capsys.readouterr().out
    assert "call sites: 3 (model literal or constant 2, model from elsewhere 1)" in out
    assert "app/client.py:17  summarize  chat  model='gpt-4o'" in out
    assert "retiring IDs in source: 2" in out and "retiring IDs in tests: 3" in out
    assert "retiring IDs in test data: 1" in out and "tests/cassettes/x.yaml:3  gpt-4.1 (bare)" in out
    assert out.rstrip().endswith("verdict: names a model removed on 2027-01-14 in source and tests")


def test_cli_scan_missing_path(tmp_path, capsys):
    assert cli.main(["scan", str(tmp_path / "nope")]) == 2 and "not a directory" in capsys.readouterr().err


def test_cli_scan_json(capsys):
    cli.main(["scan", str(FIX), "--json"])
    assert scan.counts(json.loads(capsys.readouterr().out)) == scan.FIXTURE_COUNTS


def test_cli_passes_census_commands_through(monkeypatch):
    from model_moving_day import census
    seen = []
    monkeypatch.setattr(sys, "argv", ["x"])
    monkeypatch.setattr(census, "main", lambda: seen.append(list(sys.argv)))
    cli.main(["prepare", "instructor"])
    cli.main(["manifest", "--targets", "instructor,llm", "--reason", "r"])
    assert seen == [["mmd", "prepare", "instructor"], ["mmd", "manifest", "--targets", "instructor,llm", "--reason", "r"]]


def test_module_entry_point():
    p = subprocess.run([sys.executable, "-m", "model_moving_day.scan", str(FIX), "--json"], capture_output=True,
                       text=True, cwd=Path(__file__).parents[1], check=True)
    assert scan.counts(json.loads(p.stdout)) == scan.FIXTURE_COUNTS


def test_git_clone_real_local_repo_and_cli_url(tmp_path, capsys):
    src = tmp_path / "src"
    shutil.copytree(FIX, src)
    g = ["git", "-C", str(src), "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
    subprocess.run(g + ["add", "-A"], check=True)
    subprocess.run(g + ["commit", "-qm", "fixture"], check=True)
    head = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert scan.git_clone(src.as_uri(), tmp_path / "dst", "main") == head
    assert scan.counts(scan.scan_repo(tmp_path / "dst")) == scan.FIXTURE_COUNTS
    capsys.readouterr()
    cli.main(["scan", src.as_uri(), "--json"])  # a URL is shallow-cloned into a temp dir with the same flags
    d = json.loads(capsys.readouterr().out)
    assert d["sha"] == head and scan.counts(d) == scan.FIXTURE_COUNTS
