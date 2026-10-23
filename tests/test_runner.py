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
    assert kw["files"]["/work/repo/pkg/a.py"] == "/local/a.py" and kw["env"]["NEBIUS_API_KEY"] == "k"


def test_run_job_without_a_summary_keeps_the_whole_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    led = tf.Ledger(tmp_path / "l.jsonl", caps={"total": 1.0, "c1": 1.0})
    runner.run_job(FakeBox("crashed"), "img", T, {}, k=1, cap_usd=0.5, ledger=led)
    assert led.spent()[1]["c1"] == pytest.approx(0.5)
