"""Pre-registration of the census: census/MANIFEST.json, committed and pushed before the first ladder job.

Per target it fixes the pinned commit, the image, the flip tests of every covered call site (from coverage.json),
the call sites and tests excluded before the run and why, and the SHA-256 of every frozen behaviour-check file. For
the whole census it fixes the ladder rules (rungs with model IDs and prices, K, margin, baseline, what counts as an
infrastructure error, the fix catalogue, the brain), budgets, declared smoke runs, the publication rule and what is
not claimed, plus the SHA-256 of every source file of this package. `verify` recomputes all of it; the ladder and
the patch step refuse to run on any difference and write the manifest's own SHA-256 into their output. A change is
an amendment: the file keeps the list of amendments with the previous SHA-256 and the reason.

    python -m model_moving_day.census manifest --reason "first version"
"""
import datetime, hashlib, json, subprocess
from pathlib import Path

from model_moving_day import agent, freeze, ladder, proxy, runner, tf_client as tf

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "model_moving_day"
PATH = ROOT / "census" / "MANIFEST.json"
TARGETS = ROOT / "census" / "targets.json"
EVIDENCE = ROOT / "evidence" / "census"
SCHEMA = "mmd-census-manifest/1"
CAP_PER_JOB_USD = 1.00  # model spend envelope of one sandbox job (all K runs); instructor on Ultra ~ $0.15 at K=5
C1_CAP_USD = tf.DEFAULT_CAPS["c1"]


class ManifestMismatch(RuntimeError):
    def __init__(self, problems):
        super().__init__("census/MANIFEST.json does not match: " + "; ".join(problems[:12]))
        self.problems = problems


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_hashes():
    return {p.relative_to(ROOT).as_posix(): sha256_file(p) for p in sorted(PKG.rglob("*.py"))}


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd or ROOT, capture_output=True, text=True, check=True).stdout.strip()


def rules():
    """Everything that decides a cell, built from the code's own constants (so a code change shows up here)."""
    return {
        "rungs": [{"rung": r, "model": m, "usd_per_1m_tokens": list(tf.PRICES[m])} for r, m in ladder.RUNGS],
        "prices_source": "tokenfactory.nebius.com/api/public/models_info, read 2026-10-23",
        "k": ladder.K, "margin": ladder.MARGIN, "baseline_pass_rate": ladder.BASELINE,
        "required_passes": ladder.required_passes(ladder.K, ladder.BASELINE, ladder.MARGIN),
        "run_passes": "every flip test of the call site passed in that run (a skipped flip test is a failure)",
        "cell_passes": "at least K valid runs and pass rate >= baseline - margin (with K=5: at least 4 of 5)",
        "infra": {
            "a run is an infrastructure error, not a run, when": [
                "the job left no summary (sandbox run lost) or the summary has an error",
                "the pytest run timed out (exit code 'timeout')",
                "a flip test of the site is missing from the run's junit report",
                "the run logged a proxy status 402, 404 (the rung's model not served), 429, 5xx or None at the "
                "site or at any site its flip tests reach (or without a run index), whatever the run's outcome; "
                "each cell counts such runs by the outcome they had (runs_with_retried_infra)"],
            "stop": "a 401 or 403 anywhere stops the ladder (the key was refused), with everything so far saved",
            "retries": ladder.MAX_INFRA_RETRIES,
            "retry_rule": "re-run only the missing valid runs of the same rung and parameters; a cell already "
                          "holding more failures than a passing cell may have is not re-run",
            "persisting": "the site's status becomes 'infra' at that rung and it does not climb",
        },
        "fixes": {k: {"route_params": v[0], "justified_by_diagnoses": sorted(v[1]), "must_show_request_change": sorted(v[2])}
                  for k, v in agent.FIXES.items()},
        "fix_rule": "at most one fix per red cell, proposed by the brain and accepted only when the proxy's call log "
                    "recorded a diagnosis that justifies it; the fixed cell counts only if its calls show the "
                    "request change the fix declares, else it is published as 'fix not applied' and the site climbs",
        "brain": {"rule": "Super for repos under ~200K tokens of source, Ultra above", "models": [agent.SUPER, agent.ULTRA],
                  "sampling": agent.SAMPLING, "chat_template_kwargs": agent.CTK},
        "fallback": "after the climb, every chosen site below Ultra gets one measured cell on the next rung up with "
                    "its own parameters; the helper falls back only to a fallback cell that passed, else it raises",
        "confirm": "the patched app is run through the proxy route 'passthrough' (the body exactly as the app built "
                   "it, no normalization) with NEMOTRON_MOVE=1, K runs of the moved sites' flip tests, same rules",
        "nested_sites": "an outer moved site wins: a moved call inside another moved call is not remapped (the "
                        "proxy attributes such calls to the outer, innermost-AST site, which is what the ladder measures)",
        "embedding_model": {"model": tf.EMBEDDING_MODEL, "usd_per_1m_input_tokens": tf.PRICES[tf.EMBEDDING_MODEL][0],
                            "rule": "served for /v1/embeddings only when a target needs it; the same on every rung"},
        "jobs": {"runs_in_parallel": True, "test_uid": runner.TEST_UID,
                 "per_batch_timeout_s": runner.timeouts(ladder.K, 3000, True)[0],
                 "sandbox_timeout_s": runner.timeouts(ladder.K, 3000, True)[1],
                 "upstream": {"timeout_s": proxy.UPSTREAM_TIMEOUT_S, "attempts": proxy.UPSTREAM_ATTEMPTS,
                              "max_backoff_s": proxy.UPSTREAM_MAX_SLEEP_S}},
    }


BASELINE_BEFORE = {
    "model": "gpt-6-luna", "usd_per_1m_tokens": [0.10, 0.50],
    "why": "OpenAI's recommended replacement for gpt-4o, gpt-4o-mini, gpt-4.1 and gpt-4.1-mini (retiring 2027-01-14, "
           "announced 2026-10-14), the models the targets' tests name",
    "source": "platform.openai.com/docs/pricing.md and /docs/deprecations.md, read 2026-10-20 (docs/kb/05-c1-domain.md)",
    "method": "priced on the Nemotron cell's measured prompt tokens and visible output tokens per call (reasoning "
              "tokens excluded), i.e. as if the old app made the same calls; per passed run as well as per 1,000 calls",
}
PUBLICATION = ("every cell is published, including red cells, infra cells, cells of an aborted ladder, smoke runs and "
               "the 'old model stays' outcome; every job and cell is also appended to evidence/census/<target>/"
               "ladder-log.jsonl, and each ladder run writes its own ladder-<run_id>.json (never overwritten)")
NOT_CLAIMED = [
    "baseline pass rate 1.0 is assumed (strictest), not measured by us",
    "K runs of one cell run in one sandbox at the same time, often with temperature 0: they are not independent, "
    "so the Wilson interval is optimistic",
    "covered = the test fails when the proxy answers with an empty message (must-differ control 1); tests that check "
    "only types or shapes pass any well-formed answer; a second control with well-formed wrong answers is run only "
    "where the manifest lists control-wrong",
    "streaming tests get server-sent events re-synthesized by the proxy from a non-streaming upstream call",
    "during the ladder the proxy normalizes requests per rung (logged per call); the patched app is confirmed "
    "separately without normalization",
    "per-call $ is priced on Nemotron's token counts; the baseline uses the same counts (another tokenizer differs)",
    "test traffic, not production traffic",
    "runs are set aside as infrastructure whenever the proxy logged 402/404/429/5xx/None at the site or at a site its "
    "flip tests reach, whatever their outcome, and re-run at most twice; each cell lists how many set-aside runs "
    "had passed and how many had failed (runs_with_retried_infra)",
    "after a run the hash check catches changed or missing frozen files; new files in test directories are recorded "
    "but do not stop the ladder",
]
SMOKE = [{"id": "S-instructor-super-k5", "target": "instructor", "rungs": ["Super"], "k": 5,
          "purpose": "wiring check of the phase-B job with K=5 parallel runs (run tags, key file, test uid, frozen "
                     "hashes, infra accounting, timings) before the first decision run; published; not for decision"}]


def _evidence_sha(name):
    d = EVIDENCE / name
    return {p.name: sha256_file(p) for p in sorted(d.glob("*.json"))
            if p.name in ("coverage.json", "image.json") or p.name.startswith("control-")}


def target_entry(name, clones):
    """The manifest entry of one target, recomputed from targets.json, its evidence and its clone."""
    d = json.loads(TARGETS.read_text())[name]
    cov = json.loads((EVIDENCE / name / "coverage.json").read_text())
    image = json.loads((EVIDENCE / name / "image.json").read_text())
    rows = {r["site"]: r for r in cov["traced_sites"]}
    flips = {s: rows[s]["flip_tests"] for s in cov["covered_sites"]}
    clone = Path(clones) / name
    frozen = freeze.freeze(clone, sorted({t for ts in flips.values() for t in ts}), d.get("frozen_extra", []))
    return {
        "github": d["github"], "commit": d["commit"], "license": d["license"], "install": d["install"],
        "pytest_args": d["pytest_args"], "node_args": d["node_args"], "env": d.get("env", {}),
        "helper_package": d.get("helper_package"),
        "image": {"uuid": image["image_uuid"], "tag": image["tag"]},
        "evidence_sha256": _evidence_sha(name),
        "flip_tests": flips,
        "site_flags": {s: {k: rows[s].get(k) for k in ("isolated", "good_side", "cassette_only", "wrong_control",
                                                        "flip_tests_empty_only", "wrong_control_missing")}
                       for s in flips},
        "excluded_sites": d.get("site_exclusions", {}),
        "excluded_tests": d.get("exclusions", {}),
        "traced_not_covered": sorted(r["site"] for r in cov["traced_sites"] if not r["covered"] and not r.get("excluded")),
        "ast_sites_not_reached": sorted(set(cov["ast_sites_not_reached"]) - set(d.get("site_exclusions", {}))),
        "scope_decisions": d.get("scope_decisions", []),
        "baseline_evidence": d.get("baseline_evidence", ""),
        "frozen": {"commit": frozen.commit, "extra_paths": frozen.extra_paths, "files": len(frozen.hashes),
                   "sha256": frozen.sha256(), "hashes": frozen.hashes},
    }


def build(names, clones, reason, sandbox_runs, previous=None):
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    amendments = list((previous or {}).get("amendments", []))
    if previous:
        amendments.append({"utc": now, "previous_sha256": hashlib.sha256(dumps(previous).encode()).hexdigest(),
                           "reason": reason})
    return {
        "schema": SCHEMA, "first_written_utc": (previous or {}).get("first_written_utc", now), "written_utc": now,
        "reason": reason, "amendments": amendments,
        "code": {"git_head_when_written": git("rev-parse", "HEAD"), "files_sha256": code_hashes(),
                 "tf_client_sha256": sha256_file(PKG / "tf_client.py")},
        "rules": rules(),
        "budgets": {"cap_usd_per_job": CAP_PER_JOB_USD, "c1_cap_usd": C1_CAP_USD,
                    "tokens_only": "only model tokens count against the caps; the Sandboxes `cost` field is logged as a "
                                   "diagnostic (billing check 2026-10-24: Sandboxes are not charged)",
                    "sandbox_runs": sandbox_runs},
        "smoke": SMOKE, "publication": PUBLICATION, "baseline_before": BASELINE_BEFORE, "not_claimed": NOT_CLAIMED,
        "targets": {n: target_entry(n, clones) for n in names},
    }


def dumps(man):
    return json.dumps(man, indent=1, sort_keys=True) + "\n"


def write(man, path=PATH):
    Path(path).write_text(dumps(man))
    return sha256_file(path)


def load(path=PATH):
    data = Path(path).read_bytes()
    return json.loads(data), hashlib.sha256(data).hexdigest()


def verify(man, name, clones):
    """Problems (empty = the manifest holds) for a ladder or patch run on `name`."""
    problems = []
    if man.get("schema") != SCHEMA:
        problems.append(f"schema {man.get('schema')!r} != {SCHEMA}")
    code_now, code_then = code_hashes(), (man.get("code") or {}).get("files_sha256", {})
    problems += [f"code changed since the manifest: {f}" for f in sorted(set(code_now) | set(code_then))
                 if code_now.get(f) != code_then.get(f)]
    if man.get("rules") != rules():
        problems += [f"rule {k} differs" for k in sorted(set(rules()) | set(man.get("rules") or {}))
                     if (man.get("rules") or {}).get(k) != rules().get(k)]
    if name not in (man.get("targets") or {}):
        return problems + [f"target {name} is not in the manifest"]
    try:
        now = target_entry(name, clones)
    except Exception as e:  # missing evidence, missing clone, clone not a git repo
        return problems + [f"cannot recompute the {name} entry: {type(e).__name__}: {e}"]
    then = man["targets"][name]
    problems += [f"{name}.{k} differs" for k in sorted(set(now) | set(then)) if now.get(k) != then.get(k)]
    if now["frozen"]["commit"] != then.get("commit"):
        problems.append(f"{name} clone is at {now['frozen']['commit'][:12]}, the manifest pins {str(then.get('commit'))[:12]}")
    dirty = git("status", "--porcelain", cwd=Path(clones) / name)
    if dirty:
        problems.append(f"{name} clone has local changes: {dirty.splitlines()[:3]}")
    return problems


def push_time(commit, branch, remote="origin"):
    """UTC time of the earliest GitHub push to `branch` that carried `commit` (repository activity API), or None."""
    url = git("remote", "get-url", remote)
    slug = url.split("github.com")[-1].lstrip(":/").removesuffix(".git")
    r = subprocess.run(["gh", "api", f"repos/{slug}/activity?ref=refs/heads/{branch}&per_page=100"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    times = []
    for ev in json.loads(r.stdout or "[]"):
        after = ev.get("after") or ""
        if after and subprocess.run(["git", "merge-base", "--is-ancestor", commit, after], cwd=ROOT,
                                    capture_output=True).returncode == 0:
            times.append(ev.get("timestamp"))
    return min(times) if times else None


def pushed(path=PATH, remote="origin"):
    """Proof that the committed manifest is on the remote branch: {commit, branch, remote_head}. Raises otherwise."""
    rel = Path(path).relative_to(ROOT).as_posix()
    if git("status", "--porcelain", "--", rel):
        raise ManifestMismatch([f"{rel} has uncommitted changes"])
    commit = git("log", "-1", "--format=%H", "--", rel)
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    line = git("ls-remote", remote, f"refs/heads/{branch}")
    remote_head = line.split()[0] if line else ""
    if not commit or not remote_head or subprocess.run(["git", "merge-base", "--is-ancestor", commit, remote_head],
                                                       cwd=ROOT, capture_output=True).returncode != 0:
        raise ManifestMismatch([f"manifest commit {commit[:12]} is not on {remote}/{branch} ({remote_head[:12] or 'absent'}): "
                                "push it before the first ladder job"])
    return {"commit": commit, "branch": branch, "remote_head": remote_head}
