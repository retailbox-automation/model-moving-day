"""Static scan: which Python repositories still name a model OpenAI removes on January 14, 2027.

Implements evidence/scan/RULE.md (pushed before the scan, not changed after). It reads files only: no install, no
import, no execution of the scanned code, no model, no API key. Call sites come from find.scan() unchanged; retiring
IDs are exact whole-string matches of `ast.Constant` strings (f-string parts are not followed), with one optional
provider prefix. `scan_popular` picks the repositories by the one fixed `gh search repos` query and writes
candidates.json, repos.json, results.json and a SCAN.md skeleton.

    python -m model_moving_day.scan <path>            # same as: mmd scan <path|git-url> [--json]
"""
import ast, datetime, json, os, re, shutil, subprocess, sys
from pathlib import Path

from model_moving_day import find

RETIRING = {  # id -> (family, kind)
    "gpt-4o": ("GPT-4o", "bare"), "gpt-4o-2024-08-06": ("GPT-4o", "snapshot"), "gpt-4o-2024-11-20": ("GPT-4o", "snapshot"),
    "gpt-4o-mini": ("GPT-4o mini", "bare"), "gpt-4o-mini-2024-07-18": ("GPT-4o mini", "snapshot"),
    "gpt-4.1": ("GPT-4.1", "bare"), "gpt-4.1-2025-04-14": ("GPT-4.1", "snapshot"),
    "gpt-4.1-mini": ("GPT-4.1 mini", "bare"), "gpt-4.1-mini-2025-04-14": ("GPT-4.1 mini", "snapshot"),
}
PREFIXES = ("openai/", "azure/", "openrouter/openai/")
ALREADY_REMOVED = "gpt-4o-2024-05-13"
TEST_DIRS = {"tests", "test", "testing"}
DATA_EXT = {".yaml", ".yml", ".json", ".toml"}
MAX_DATA_BYTES = 5 * 1024 * 1024
VENDOR_DIRS = {".git", ".venv", "venv", "node_modules", "site-packages", "__pycache__"}
_ids = "|".join(re.escape(i) for i in sorted(RETIRING, key=len, reverse=True))
_pre = "|".join(re.escape(p) for p in PREFIXES)
DATA_RE = re.compile(rf"(?<![\w./-])(?:{_pre})?({_ids})(?![\w-]|\.\w)")  # whole word: gpt-4o-mini-tts is not gpt-4o
ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "scan_repo"
FIXTURE_COUNTS = {"call_sites": 3, "sites_literal_model": 2, "sites_model_elsewhere": 1, "source_ids": 2,
                  "test_ids": 3, "test_data_ids": 1, "already_removed": 1, "errors": 1}
QUERY = ["gh", "search", "repos", "openai", "--language=python", "--sort=stars", "--order=desc", "--archived=false",
         "--limit", "200", "--json", "fullName,stargazersCount,isFork,isArchived,pushedAt,defaultBranch,license,url"]
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"}


def _strip(s):
    for p in PREFIXES:
        if s.startswith(p):
            return s[len(p):]
    return s


def match_id(s):
    """Canonical retiring ID for an exact string (one optional provider prefix), else None."""
    s = _strip(s) if isinstance(s, str) else None
    return s if s in RETIRING else None


def is_test_file(rel):
    parts = Path(rel).parts
    name = parts[-1]
    return (any(p in TEST_DIRS for p in parts[:-1]) or name.startswith("test_") and name.endswith(".py")
            or name.endswith("_test.py") or name == "conftest.py")


def _walk(root, suffixes):
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.suffix in suffixes and p.is_file() and not any(x in VENDOR_DIRS for x in rel.parts[:-1]):
            yield p, rel.as_posix()


def _strings(tree):
    """(line, value) of every str Constant that is not a part of an f-string."""
    inside = {id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in ast.walk(n)}
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in inside]


def scan_detail(root):
    """(fields per RULE.md, find.ScanResult) for one repository on disk."""
    root = Path(root)
    res = find.scan(root)
    source = {rel for _, rel in find._source_files(root, find.SKIP_DIRS)}
    out = {"call_sites": len(res.sites), "sites_literal_model": sum(s.model_literal is not None for s in res.sites)}
    out["sites_model_elsewhere"] = out["call_sites"] - out["sites_literal_model"]
    out.update(source_ids=[], test_ids=[], test_data_ids=[], already_removed=[], errors=[], skipped_large=[])
    for path, rel in _walk(root, {".py"}):
        test = is_test_file(rel)
        try:
            tree = ast.parse(path.read_text(errors="replace"), filename=rel)
        except (SyntaxError, ValueError, OSError) as e:
            if test or rel in source:
                out["errors"].append({"file": rel, "error": f"{type(e).__name__}: {e}"})
            continue
        for line, v in _strings(tree):
            if _strip(v) == ALREADY_REMOVED:
                out["already_removed"].append({"file": rel, "line": line, "id": ALREADY_REMOVED})
            cid = match_id(v)
            if cid and (test or rel in source):
                key = "test_ids" if test else "source_ids"  # a file in both (e.g. *_test.py) counts as test
                out[key].append({"file": rel, "line": line, "id": cid, "kind": RETIRING[cid][1]})
    for path, rel in _walk(root, DATA_EXT):
        if not any(x in TEST_DIRS for x in Path(rel).parts[:-1]):
            continue
        try:
            if path.stat().st_size > MAX_DATA_BYTES:
                out["skipped_large"].append(rel)
                continue
            text = path.read_text(errors="replace")
        except OSError as e:
            out["errors"].append({"file": rel, "error": f"{type(e).__name__}: {e}"})
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for m in DATA_RE.finditer(line):
                out["test_data_ids"].append({"file": rel, "line": n, "id": m.group(1), "kind": RETIRING[m.group(1)][1]})
    for k in ("source_ids", "test_ids", "test_data_ids", "already_removed"):
        out[k].sort(key=lambda d: (d["file"], d["line"]))
    return out, res


def scan_repo(root):
    return scan_detail(root)[0]


def counts(r):
    return {k: v if isinstance(v, int) else len(v) for k, v in r.items() if k in FIXTURE_COUNTS}


def git_clone(url, dest, branch=None, timeout=300):
    """Shallow clone with no hooks, no submodules, no LFS smudge, no prompt; returns the HEAD SHA."""
    cmd = ["git", "-c", "core.hooksPath=/dev/null", "clone", "--depth", "1", "--no-recurse-submodules",
           "--single-branch"] + (["--branch", branch] if branch else []) + ["--", url, str(dest)]
    env = {**os.environ, **GIT_ENV}
    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout, env=env)
    return subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


def _run_gh(cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300).stdout


def _git_out(*args):
    try:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def check_fixture(path=FIXTURE):
    got = counts(scan_repo(path)) if path.is_dir() else None
    if got != FIXTURE_COUNTS:
        raise SystemExit(f"must-light control failed: fixture {path} gave {got}, expected {FIXTURE_COUNTS}")
    return got


def headline(results):
    k = len(results)
    t = sum(bool(r["test_ids"]) for r in results)
    s = sum(bool(r["source_ids"]) for r in results)
    u = sum(bool(r["test_ids"] or r["source_ids"]) for r in results)
    return {"K": k, "T": f"{t} of {k}", "S": f"{s} of {k}", "U": f"{u} of {k}"}


def scan_popular(out_dir, n=50, limit=200, clone_dir=None, gh=_run_gh, clone=git_clone, now=None, fixture=FIXTURE):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    check_fixture(fixture)
    clone_dir = Path(clone_dir or out_dir / "_clones")
    clone_dir.mkdir(parents=True, exist_ok=True)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    query = [*QUERY]
    query[query.index("--limit") + 1] = str(limit)
    raw = gh(query)
    (out_dir / "candidates.json").write_text(raw)
    cutoff = now - datetime.timedelta(days=180)
    repos, results, clone_errors = [], [], 0
    for c in json.loads(raw):
        row = {"repo": c["fullName"], "stars": c.get("stargazersCount"), "pushed_at": c.get("pushedAt"),
               "status": "dropped", "reason": None, "sha": None}
        repos.append(row)
        pushed = datetime.datetime.fromisoformat(c["pushedAt"]) if c.get("pushedAt") else None
        if len(results) >= n:
            row["status"], row["reason"] = "not reached", f"{n} repositories already kept"
        elif c.get("isFork"):
            row["reason"] = "fork"
        elif c.get("isArchived"):
            row["reason"] = "archived"
        elif pushed is None or pushed < cutoff:
            row["reason"] = f"no push in the 180 days before {now.isoformat(timespec='seconds')}"
        else:
            dest = clone_dir / c["fullName"].replace("/", "__")
            shutil.rmtree(dest, ignore_errors=True)
            step = "clone"
            try:
                row["sha"] = clone(c["url"], dest, c.get("defaultBranch") or None)
                step = "scan"
                r, _ = scan_detail(dest)
            except Exception as e:  # failed or timed-out clone (or a scan crash): dropped and counted
                clone_errors += step == "clone"
                row["reason"], row["error"] = f"{step} failed", f"{type(e).__name__}: {(getattr(e, 'stderr', None) or str(e)).strip()[:300]}"
                continue
            finally:
                shutil.rmtree(dest, ignore_errors=True)
            if r["call_sites"]:
                row["status"], row["reason"] = "kept", "call sites found"
                results.append({"repo": c["fullName"], "sha": row["sha"], "stars": c.get("stargazersCount"), **r})
            else:
                row["reason"] = "no LLM call site in non-test source"
    summary = {**headline(results), "candidates": len(repos), "clone_errors": clone_errors,
               "scan_errors": sum(r["reason"] == "scan failed" for r in repos),
               "parse_errors": sum(len(r["errors"]) for r in results),
               "repos_with_errors": sum(bool(r["errors"]) for r in results),
               "query_time": now.isoformat(timespec="seconds"), "query": " ".join(query)}
    (out_dir / "repos.json").write_text(json.dumps(repos, indent=1))
    (out_dir / "results.json").write_text(json.dumps(results, indent=1))
    write_scan_md(out_dir, summary)
    return summary


def write_scan_md(out_dir, summary):
    rule = _git_out("log", "-1", "--format=%H %cI", "--", "evidence/scan/RULE.md")
    rows = "\n".join("| | | | |" for _ in range(5))
    dirty = " (with uncommitted changes to tracked files)" if _git_out("status", "--porcelain", "--untracked-files=no") else ""
    text = f"""# Scan: retiring OpenAI model IDs in popular Python repositories

Rule: `evidence/scan/RULE.md`, commit {rule}. Code commit that ran: {_git_out("rev-parse", "HEAD")}{dirty}.
Query time (UTC): {summary["query_time"]}. Query: `{summary["query"]}`.

## Headline

- **T** (retiring ID in a test `.py` file): {summary["T"]}
- **S** (retiring ID in non-test source): {summary["S"]}
- **U** (either): {summary["U"]}

Errors: {summary["clone_errors"]} failed clones; {summary["scan_errors"]} failed scans; {summary["parse_errors"]} read/parse errors in {summary["repos_with_errors"]} kept repositories. Candidates: {summary["candidates"]}.

## Checked by hand against GitHub

| Repository | SHA | What the scan reported | What GitHub shows |
|---|---|---|---|
{rows}

## Deviations from RULE.md

"""
    (Path(out_dir) / "SCAN.md").write_text(text)


if __name__ == "__main__":
    from model_moving_day import cli
    sys.exit(cli.main(["scan", *sys.argv[1:]]))
