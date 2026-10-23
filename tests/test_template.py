import asyncio, importlib, io, json, logging

import pytest

from model_moving_day.templates import nemotron_move as nm

LIGHT, SUPER = "nvidia/Nemotron-3_5-Lightning", "nvidia/nemotron-3-super-120b-a12b"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    importlib.reload(nm)
    nm.SITES.update({"retry_sync": {"model": LIGHT, "fallback": SUPER,
                                    "params": {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
                                    "drop": ["seed"]}})
    monkeypatch.setenv("NEMOTRON_MOVE_CHECK", "0")


class NotFound(Exception):
    status_code = 404


def test_off_by_default_the_callers_model_is_untouched(monkeypatch):
    monkeypatch.delenv("NEMOTRON_MOVE", raising=False)
    seen = []
    nm.call("retry_sync", lambda **kw: seen.append(kw), model="gpt-4.1-mini", seed=1)
    assert seen == [{"model": "gpt-4.1-mini", "seed": 1}]


def test_enabled_site_gets_the_census_model_and_fixes(monkeypatch):
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    seen = []
    nm.call("retry_sync", lambda **kw: seen.append(kw), model="gpt-4.1-mini", seed=1, extra_body={"x": 1})
    assert seen[0]["model"] == LIGHT and "seed" not in seen[0]
    assert seen[0]["extra_body"] == {"x": 1, "chat_template_kwargs": {"enable_thinking": False}}


def test_per_site_env_override(monkeypatch):
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    monkeypatch.setenv("NEMOTRON_MODEL_RETRY_SYNC", SUPER)
    assert nm.model_for("retry_sync", "gpt-4.1-mini") == SUPER


def test_404_falls_back_once_and_logs(monkeypatch, caplog):
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    seen = []

    def create(**kw):
        seen.append(kw["model"])
        if kw["model"] == LIGHT:
            raise NotFound("gone")
        return "ok"

    with caplog.at_level(logging.WARNING, logger="nemotron_move"):
        assert nm.call("retry_sync", create, model="m") == "ok"
    assert seen == [LIGHT, SUPER] and "404" in caplog.text


def test_other_errors_are_not_swallowed(monkeypatch):
    monkeypatch.setenv("NEMOTRON_MOVE", "1")

    def create(**kw):
        raise ValueError("bad request")

    with pytest.raises(ValueError):
        nm.call("retry_sync", create, model="m")


def test_async_call_falls_back_too(monkeypatch):
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    seen = []

    async def create(**kw):
        seen.append(kw["model"])
        if kw["model"] == LIGHT:
            raise NotFound("gone")
        return "ok"

    assert asyncio.run(nm.acall("retry_sync", create, model="m")) == "ok" and seen == [LIGHT, SUPER]


def test_deprecation_signal_warns_for_unlisted_models(monkeypatch, caplog):
    monkeypatch.setenv("NEMOTRON_MOVE_CHECK", "1")
    monkeypatch.setattr(nm.urllib.request, "urlopen",
                        lambda req, timeout=None: io.BytesIO(json.dumps({"data": [{"id": SUPER}]}).encode()))
    with caplog.at_level(logging.WARNING, logger="nemotron_move"):
        nm.check_models()
        nm.check_models()  # once per process
    assert caplog.text.count(LIGHT) == 1 and SUPER not in caplog.text.replace(LIGHT, "")
