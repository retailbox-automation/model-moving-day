import json

import pytest

from model_moving_day import sandbox as sb
from model_moving_day import tf_client as tf


class FakeResult:
    def __init__(self, exit_code=0, stdout="", stderr="", elapsed=1.5, cost=0.0):
        import datetime
        self.exit_code, self.stdout, self.stderr, self.cost = exit_code, stdout, stderr, cost
        self.elapsed_time = datetime.timedelta(seconds=elapsed)


class FakeImage:
    def __init__(self, result, calls):
        self._result, self.calls, self.uuid, self.tag = result, calls, "11111111-1111-1111-1111-111111111111", None

    def run(self, **kw):
        self.calls.append(kw)
        return self

    def wait(self):
        return self

    @property
    def result(self):
        return self._result


class FakeImages:
    def __init__(self, result):
        self.calls, self.result, self.used = [], result, []

    def use(self, ref):
        self.used.append(ref)
        return FakeImage(self.result, self.calls)


class FakeContree:
    def __init__(self, result):
        self.images = FakeImages(result)


def box(tmp_path, result, **kw):
    led = tf.Ledger(tmp_path / "l.jsonl", caps={"total": 1.0, "c1": 1.0})
    return sb.Sandbox(FakeContree(result), ledger=led, log_path=tmp_path / "runs.jsonl", **kw), led


def test_run_returns_output_exit_code_and_times(tmp_path):
    b, _ = box(tmp_path, FakeResult(exit_code=3, stdout="hello\n", elapsed=2.0))
    r = b.run("python:3.12-slim", "echo hello", env={"A": "1"}, timeout=60)
    assert (r.exit_code, r.stdout, r.elapsed_s) == (3, "hello\n", 2.0)
    assert r.wall_s >= 0 and b.contree.images.used == ["python:3.12-slim"]
    call = b.contree.images.calls[0]
    assert call["shell"] == "echo hello" and call["env"] == {"A": "1"} and call["hostname"].startswith("mmd-")


def test_each_run_reserves_then_settles_to_the_reported_cost_and_is_logged(tmp_path):
    b, led = box(tmp_path, FakeResult(cost=0.002, elapsed=4.0))
    b.run("python:3.12-slim", "pytest -q", env={"SECRET": "s3cr3t"})
    assert led.spent()[1]["c1"] == pytest.approx(0.002)
    rec = json.loads((tmp_path / "runs.jsonl").read_text())
    assert rec["image"] == "python:3.12-slim" and rec["command"] == "pytest -q" and rec["cost"] == 0.002
    assert rec["elapsed_s"] == 4.0 and "operation" in rec and "SECRET" in rec["env_keys"]
    assert "s3cr3t" not in (tmp_path / "runs.jsonl").read_text()


def test_a_run_can_reserve_more_than_the_default(tmp_path):
    b, led = box(tmp_path, FakeResult(cost=0.64, elapsed=60.0))
    seen = []
    orig = led.reserve
    led.reserve = lambda tag, usd, note: (seen.append(usd), orig(tag, usd, note))
    b.run("python:3.12-slim", "sleep 60", reserve=0.80)
    assert seen == [0.80] and led.spent()[1]["c1"] == pytest.approx(0.64)
    assert json.loads((tmp_path / "runs.jsonl").read_text())["reserved"] == 0.80


def test_failed_run_keeps_the_full_reservation(tmp_path):
    class Boom(FakeContree):
        def __init__(self):
            super().__init__(None)
            self.images.use = lambda ref: (_ for _ in ()).throw(RuntimeError("api down"))
    led = tf.Ledger(tmp_path / "l.jsonl", caps={"total": 1.0, "c1": 1.0})
    b = sb.Sandbox(Boom(), ledger=led, log_path=tmp_path / "runs.jsonl")
    with pytest.raises(RuntimeError):
        b.run("python:3.12-slim", "true")
    assert led.spent()[1]["c1"] == pytest.approx(sb.RUN_RESERVE)
    assert json.loads((tmp_path / "runs.jsonl").read_text())["error"].startswith("RuntimeError")


def test_run_count_limit_is_enforced_across_instances(tmp_path):
    b, _ = box(tmp_path, FakeResult(), max_runs=2)
    b.run("img", "true")
    b.run("img", "true")
    b2, _ = box(tmp_path, FakeResult(), max_runs=2)
    with pytest.raises(sb.SandboxRunLimit):
        b2.run("img", "true")
    assert len(b2.contree.images.calls) == 0


def test_trailing_json_block_is_extracted():
    out = "pytest noise\n" + sb.JSON_MARK + json.dumps({"passed": 3}) + "\n"
    assert sb.trailing_json(out) == {"passed": 3}
    assert sb.trailing_json("no marker here") is None


def test_envelope_reserves_first_and_settles_to_actual(tmp_path):
    led = tf.Ledger(tmp_path / "l.jsonl", caps={"total": 1.0, "c1": 1.0})
    env = sb.Envelope(led, "c1", 0.30, "run x")
    assert led.spent()[0] == pytest.approx(0.30)
    env.settle(0.04)
    assert led.spent()[0] == pytest.approx(0.04)
    with pytest.raises(RuntimeError):
        env.settle(0.01)  # settling twice would double-count


def test_envelope_refused_when_cap_has_no_room(tmp_path):
    led = tf.Ledger(tmp_path / "l.jsonl", caps={"total": 0.10, "c1": 0.10})
    with pytest.raises(tf.BudgetExceeded):
        sb.Envelope(led, "c1", 0.20, "too big")
    assert led.spent()[0] == pytest.approx(0)


def test_envelope_never_settles_above_itself(tmp_path):
    led = tf.Ledger(tmp_path / "l.jsonl", caps={"total": 1.0, "c1": 1.0})
    env = sb.Envelope(led, "c1", 0.10, "run y")
    with pytest.raises(ValueError):
        env.settle(0.11)  # the in-sandbox cap equals the envelope, so more means a broken cap
    assert led.spent()[0] == pytest.approx(0.10)  # reservation kept
