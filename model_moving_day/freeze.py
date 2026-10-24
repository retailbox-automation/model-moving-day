"""Freeze a target repo's own behaviour checks before the agent touches anything.

Frozen = the pinned commit, the list of test node IDs, and the SHA-256 of every file the checks depend on:
everything under a directory named tests/ or test/ at any depth, every conftest.py, the files that configure
pytest (pyproject.toml, pytest.ini, tox.ini, setup.cfg, noxfile.py, wherever they are), plus extra paths
(fixtures, cassettes, data) given by the caller. `verify` fails hard on any change, addition or deletion;
`guard` runs it before and after each agent step.
"""
import hashlib, json, subprocess
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

TEST_DIRS = {"tests", "test"}
CONFIG_FILES = {"conftest.py", "pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg", "noxfile.py"}
IGNORE_PARTS = {".git", "__pycache__", ".pytest_cache", ".venv", "node_modules", ".mypy_cache", ".ruff_cache",
                "build", "dist"}  # build/ and dist/ hold copies made by an install, not the checks


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


def frozen_files(root, extra_paths=()):
    """Relative paths of every file that belongs to the frozen behaviour checks of the repo at `root`."""
    root, out = Path(root), set()
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if not p.is_file() or IGNORE_PARTS & set(rel.parts) or any(x.endswith(".egg-info") for x in rel.parts):
            continue
        if TEST_DIRS & set(rel.parts[:-1]) or p.name in CONFIG_FILES or any(
                rel.as_posix() == e or rel.as_posix().startswith(e.rstrip("/") + "/") for e in extra_paths):
            out.add(rel.as_posix())
    return out



def freeze(repo, tests, extra_paths=()):
    root = Path(repo)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    files = frozen_files(root, list(extra_paths))
    return Frozen(commit, list(tests), {f: _sha(root / f) for f in sorted(files)}, list(extra_paths))


def verify(repo, frozen):
    root = Path(repo)
    now = frozen_files(root, frozen.extra_paths)
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
