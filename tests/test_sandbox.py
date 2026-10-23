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


def test_run_returns_output_exit_code_and_times():
    fake = FakeContree(FakeResult(exit_code=3, stdout="hello\n", elapsed=2.0))
    r = sb.Sandbox(fake).run("python:3.12-slim", "echo hello", env={"A": "1"}, timeout=60)
    assert (r.exit_code, r.stdout, r.elapsed_s) == (3, "hello\n", 2.0)
    assert r.wall_s >= 0 and fake.images.used == ["python:3.12-slim"]
    assert fake.images.calls[0]["shell"] == "echo hello" and fake.images.calls[0]["env"] == {"A": "1"}


def test_any_reported_cost_stops_the_run():
    fake = FakeContree(FakeResult(cost=0.002))
    with pytest.raises(sb.SandboxCostShown):
        sb.Sandbox(fake).run("python:3.12-slim", "true")


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
