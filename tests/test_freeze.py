import subprocess

import pytest

from model_moving_day import freeze


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "core.py").write_text("x = 1\n")
    (tmp_path / "tests" / "cassettes").mkdir(parents=True)
    (tmp_path / "tests" / "test_core.py").write_text("def test_x():\n    assert True\n")
    (tmp_path / "tests" / "cassettes" / "a.yaml").write_text("recorded: true\n")
    (tmp_path / "conftest.py").write_text("")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"],
                   cwd=tmp_path, check=True)
    return tmp_path


def test_freeze_records_commit_tests_and_hashes(repo):
    fr = freeze.freeze(repo, ["tests/test_core.py::test_x"])
    assert len(fr.commit) == 40 and fr.tests == ["tests/test_core.py::test_x"]
    assert set(fr.hashes) == {"tests/test_core.py", "tests/cassettes/a.yaml", "conftest.py"}
    assert "app/core.py" not in fr.hashes


def test_any_change_to_a_frozen_file_is_a_violation(repo):
    fr = freeze.freeze(repo, [])
    (repo / "tests" / "cassettes" / "a.yaml").write_text("recorded: false\n")
    with pytest.raises(freeze.FreezeViolation) as e:
        freeze.verify(repo, fr)
    assert "tests/cassettes/a.yaml" in str(e.value)


def test_added_and_deleted_frozen_files_are_violations(repo):
    fr = freeze.freeze(repo, [])
    (repo / "tests" / "test_new.py").write_text("")
    with pytest.raises(freeze.FreezeViolation):
        freeze.verify(repo, fr)
    (repo / "tests" / "test_new.py").unlink()
    (repo / "conftest.py").unlink()
    with pytest.raises(freeze.FreezeViolation):
        freeze.verify(repo, fr)


def test_source_edits_are_allowed(repo):
    fr = freeze.freeze(repo, [])
    (repo / "app" / "core.py").write_text("x = 2\n")
    freeze.verify(repo, fr)


def test_guard_checks_before_and_after_a_step(repo):
    fr = freeze.freeze(repo, [])
    with pytest.raises(freeze.FreezeViolation):
        with freeze.guard(repo, fr):
            (repo / "tests" / "test_core.py").write_text("def test_x():\n    pass\n")


def test_manifest_hash_is_stable_and_changes_with_content(repo):
    a = freeze.freeze(repo, ["t1"]).sha256()
    assert a == freeze.freeze(repo, ["t1"]).sha256()
    assert a != freeze.freeze(repo, ["t1", "t2"]).sha256()


def test_pytest_configuration_and_nested_test_dirs_are_frozen(repo):
    """An agent must not be able to switch tests off through addopts/--deselect or hide a test deeper in the tree."""
    (repo / "pytest.ini").write_text("[pytest]\n")
    (repo / "pyproject.toml").write_text("[tool.pytest.ini_options]\naddopts = ''\n")
    (repo / "pkg" / "sub" / "tests").mkdir(parents=True)
    (repo / "pkg" / "sub" / "tests" / "test_deep.py").write_text("def test_d():\n    pass\n")
    (repo / "build" / "lib" / "tests").mkdir(parents=True)
    (repo / "build" / "lib" / "tests" / "copy.py").write_text("")
    fr = freeze.freeze(repo, [])
    assert {"pytest.ini", "pyproject.toml", "pkg/sub/tests/test_deep.py"} <= set(fr.hashes)
    assert "build/lib/tests/copy.py" not in fr.hashes
    (repo / "pyproject.toml").write_text("[tool.pytest.ini_options]\naddopts = '--deselect tests/test_core.py::test_x'\n")
    with pytest.raises(freeze.FreezeViolation) as e:
        freeze.verify(repo, fr)
    assert "changed pyproject.toml" in str(e.value)
    (repo / "pyproject.toml").write_text("[tool.pytest.ini_options]\naddopts = ''\n")
    (repo / "tox.ini").write_text("[pytest]\naddopts = -p no:asyncio\n")
    with pytest.raises(freeze.FreezeViolation) as e:
        freeze.verify(repo, fr)
    assert "added tox.ini" in str(e.value)
