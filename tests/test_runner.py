import json

import pytest

from model_moving_day import runner, sandbox as sb, tf_client as tf

T = runner.Target("demo", "owner/demo", "a" * 40, ["pytest>=8"], ["tests/llm", "-q"], env={"X": "1"})


def test_package_files_ship_the_proxy_job_and_tracer():
    files = runner.package_files()
    for name in ("proxy.py", "job.py", "tf_client.py", "normalize.py", "tracer/sitecustomize.py"):
        assert f"/mmd/model_moving_day/{name}" in files


def test_prepare_script_pins_the_commit_tarball():
    s = runner.prepare_script(T)
    assert "codeload.github.com/owner/demo/tar.gz/" + "a" * 40 in s and "'pytest>=8'" in s and "-e /work/repo" in s
    assert "export " not in s


def test_prepare_script_exports_the_install_environment_before_installing():
    """paper-qa builds with setuptools_scm, which needs a version when the tarball has no git metadata."""
    t = runner.Target("demo", "owner/demo", "a" * 40, [], [], prepare_env={"SETUPTOOLS_SCM_PRETEND_VERSION": "5.0.0+mmd"})
    s = runner.prepare_script(t)
    assert s.index("export SETUPTOOLS_SCM_PRETEND_VERSION='5.0.0+mmd'") < s.index("uv pip install")


class FakeBox:
    tag = "c1"

    def __init__(self, stdout):
        self.stdout, self.calls = stdout, []

    def run(self, image, shell, **kw):
        self.calls.append((image, shell, kw))
        return sb.RunResult(0, self.stdout, "", 1.0, 2.0, 0.001, "")


def test_run_job_settles_the_envelope_to_what_the_job_spent(tmp_path, monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    led = tf.Ledger(tmp_path / "l.jsonl", caps={"total": 1.0, "c1": 1.0})
    box = FakeBox("noise\n" + sb.JSON_MARK + json.dumps({"envelope_spent": 0.03, "runs": []}) + "\n")
    r, summary = runner.run_job(box, "img-uuid", T, {"default": {"mode": "broken"}}, k=2, cap_usd=0.5, ledger=led,
                                extra_files={"pkg/a.py": "/local/a.py"})
    assert summary["envelope_spent"] == 0.03 and led.spent()[1]["c1"] == pytest.approx(0.03)
    image, shell, kw = box.calls[0]
    spec = json.loads(kw["files"]["/mmd/spec.json"])
    assert image == "img-uuid" and spec["k"] == 2 and spec["routes"]["default"]["mode"] == "broken"
    assert kw["files"]["/work/repo/pkg/a.py"] == "/local/a.py"
    # the key travels as a file the job deletes, never in the run's environment (where /proc/<pid>/environ shows it)
    assert "NEBIUS_API_KEY" not in kw["env"] and kw["files"][runner.KEY_FILE] == b"k" and spec["key_file"] == runner.KEY_FILE
    assert "k" not in json.dumps(spec["env"]) and spec["test_uid"] == runner.TEST_UID and spec["parallel"] is True


def test_run_job_without_a_summary_keeps_the_whole_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    led = tf.Ledger(tmp_path / "l.jsonl", caps={"total": 1.0, "c1": 1.0})
    runner.run_job(FakeBox("crashed"), "img", T, {}, k=1, cap_usd=0.5, ledger=led)
    assert led.spent()[1]["c1"] == pytest.approx(0.5)


def test_timeouts_fit_inside_the_sandbox_limit():
    """K runs in parallel share one window; in sequence each run gets (3600 - 300) / K, never more."""
    assert runner.timeouts(5, 3000, True) == (3000, 3300)
    assert runner.timeouts(5, 9000, True) == (3300, 3600)
    assert runner.timeouts(5, 1500, False) == (660, 3600)
    assert runner.timeouts(2, 900, False) == (900, 2100)


def test_job_spec_carries_the_frozen_hashes_and_the_sandbox_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    led = tf.Ledger(tmp_path / "l.jsonl", caps={"total": 1.0, "c1": 1.0})
    box = FakeBox(sb.JSON_MARK + json.dumps({"envelope_spent": 0.0, "runs": []}) + "\n")
    runner.run_job(box, "img", T, {}, k=5, cap_usd=0.5, ledger=led, frozen_hashes={"tests/a.py": "0" * 64},
                   frozen_extra=["data"], require_frozen=True)
    _, _, kw = box.calls[0]
    spec = json.loads(kw["files"]["/mmd/spec.json"])
    assert spec["frozen_hashes"] == {"tests/a.py": "0" * 64} and spec["require_frozen"] and spec["frozen_extra"] == ["data"]
    assert kw["timeout"] == 3300 and spec["timeout_s"] == 3000


def test_an_envelope_overrun_keeps_the_summary_and_the_reservation(tmp_path, monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    led = tf.Ledger(tmp_path / "l.jsonl", caps={"total": 1.0, "c1": 1.0})
    box = FakeBox(sb.JSON_MARK + json.dumps({"envelope_spent": 0.7, "runs": [{}]}) + "\n")
    _, summary = runner.run_job(box, "img", T, {}, k=1, cap_usd=0.5, ledger=led)
    assert summary["runs"] == [{}] and "exceeds the envelope" in summary["envelope_error"]
    assert led.spent()[1]["c1"] == pytest.approx(0.5)


def test_import_stubs_are_uploaded_and_any_use_fails(tmp_path, monkeypatch):
    """paper-qa's test module imports optional readers at the top (one is AGPL and is not installed)."""
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    led = tf.Ledger(tmp_path / "l.jsonl", caps={"total": 1.0, "c1": 1.0})
    t = runner.Target("demo", "o/d", "a" * 40, [], ["tests"], import_stubs={"agpl_reader": "AGPL-3.0, not installed"})
    box = FakeBox(sb.JSON_MARK + json.dumps({"envelope_spent": 0.0, "runs": []}) + "\n")
    runner.run_job(box, "img", t, {}, k=1, cap_usd=0.1, ledger=led)
    _, _, kw = box.calls[0]
    src = kw["files"]["/mmd/stubs/agpl_reader/__init__.py"].decode()
    assert json.loads(kw["files"]["/mmd/spec.json"])["stub_path"] == runner.STUB_DIR
    ns = {}
    exec(src, ns)
    with pytest.raises(RuntimeError, match="AGPL-3.0, not installed"):
        ns["__getattr__"]("parse_pdf_to_pages")("x.pdf")
