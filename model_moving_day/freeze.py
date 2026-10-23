"""Freeze a target repo's own behaviour checks before the agent touches anything.

Frozen = the pinned commit, the list of test node IDs, and the SHA-256 of every file the checks depend on:
everything under test directories (tests/, test/), every conftest.py, plus extra paths (fixtures, cassettes,
data) given by the caller. `verify` fails hard on any change, addition or deletion; `guard` runs it before and
after each agent step.
"""
import hashlib, json, subprocess
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

TEST_DIRS = ("tests", "test")
IGNORE_PARTS = {".git", "__pycache__", ".pytest_cache", ".venv", "node_modules", ".mypy_cache", ".ruff_cache"}


class FreezeViolation(RuntimeError):
    pass


@dataclass
class Frozen:
    commit: str
    tests: list
    hashes: dict
    extra_paths: list = field(default_factory=list)

    def sha256(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _frozen_files(root, extra_paths):
    out = set()
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if not p.is_file() or IGNORE_PARTS & set(rel.parts):
            continue
        if rel.parts[0] in TEST_DIRS or p.name == "conftest.py" or any(
                rel.as_posix() == e or rel.as_posix().startswith(e.rstrip("/") + "/") for e in extra_paths):
            out.add(rel.as_posix())
    return out


def freeze(repo, tests, extra_paths=()):
    root = Path(repo)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    files = _frozen_files(root, list(extra_paths))
    return Frozen(commit, list(tests), {f: _sha(root / f) for f in sorted(files)}, list(extra_paths))


def verify(repo, frozen):
    root = Path(repo)
    now = _frozen_files(root, frozen.extra_paths)
    problems = [f"added {f}" for f in sorted(now - set(frozen.hashes))]
    problems += [f"deleted {f}" for f in sorted(set(frozen.hashes) - now)]
    problems += [f"changed {f}" for f in sorted(now & set(frozen.hashes)) if _sha(root / f) != frozen.hashes[f]]
    if problems:
        raise FreezeViolation("frozen behaviour checks were modified: " + "; ".join(problems[:20]))


@contextmanager
def guard(repo, frozen):
    verify(repo, frozen)
    yield
    verify(repo, frozen)
