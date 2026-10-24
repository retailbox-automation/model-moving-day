import asyncio, importlib, io, json, logging

import pytest

from model_moving_day.templates import nemotron_move as nm

LIGHT, SUPER = "nvidia/Nemotron-3_5-Lightning", "nvidia/nemotron-3-super-120b-a12b"
OFF = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    importlib.reload(nm)
    nm.SITES.update({
        "retry_sync": {"model": LIGHT, "params": OFF, "drop": ["seed"],
                       "fallback": {"model": SUPER, "params": {}, "drop": ["seed"]}},
        "validator": {"model": SUPER, "params": {}, "drop": [], "min_max_tokens": 8000, "fallback": None},
    })
    monkeypatch.setenv("NEMOTRON_MOVE_CHECK", "0")
    for k in ("NEMOTRON_MOVE_BASE_URL", "NEMOTRON_MOVE_API_KEY"):
        monkeypatch.delenv(k, raising=False)


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
    nm.call("retry_sync", lambda **kw: seen.append(kw), model="gpt-4.1-mini", seed=1,
            extra_body={"x": 1, "chat_template_kwargs": {"keep": True}, "seed": 2})
    assert seen[0]["model"] == LIGHT and "seed" not in seen[0]
    assert seen[0]["extra_body"] == {"x": 1, "chat_template_kwargs": {"keep": True, "enable_thinking": False}}


def test_budget_floor_is_set_when_absent_and_never_lowers_a_larger_budget(monkeypatch):
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    seen = []
    nm.call("validator", lambda **kw: seen.append(kw), model="m")
    nm.call("validator", lambda **kw: seen.append(kw), model="m", max_tokens=16000)
    nm.call("validator", lambda **kw: seen.append(kw), model="m", max_completion_tokens=100)
    assert [s.get("max_tokens") for s in seen] == [8000, 16000, None] and seen[2]["max_completion_tokens"] == 8000


def test_per_site_env_override(monkeypatch):
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    monkeypatch.setenv("NEMOTRON_MODEL_RETRY_SYNC", SUPER)
    assert nm.model_for("retry_sync", "gpt-4.1-mini") == SUPER


def test_nested_site_keeps_the_outer_sites_choice(monkeypatch):
    """A validator call (outer site) going through the library funnel (inner site) is not remapped twice."""
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    seen = []

    def funnel(**kw):  # the library funnel, itself a moved site
        return nm.call("retry_sync", lambda **k: seen.append(k) or "ok", **kw)

    assert nm.call("validator", funnel, model="gpt-3.5-turbo", seed=5) == "ok"
    assert seen == [{"model": SUPER, "seed": 5, "max_tokens": 8000}]  # validator's choice; retry_sync did not drop seed
    nm.call("retry_sync", lambda **k: seen.append(k), model="m")  # outside the outer call the funnel moves again
    assert seen[-1]["model"] == LIGHT


def test_nested_async_site_keeps_the_outer_choice(monkeypatch):
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    seen = []

    async def inner(**kw):
        seen.append(kw)
        return "ok"

    async def funnel(**kw):
        return await nm.acall("retry_sync", inner, **kw)

    assert asyncio.run(nm.acall("validator", funnel, model="m")) == "ok" and seen[0]["model"] == SUPER


def test_unknown_model_falls_back_once_to_the_measured_fallback_with_its_own_params(monkeypatch, caplog):
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    seen = []

    def create(**kw):
        seen.append(kw)
        if kw["model"] == LIGHT:
            raise NotFound("gone")
        return "ok"

    with caplog.at_level(logging.WARNING, logger="nemotron_move"):
        assert nm.call("retry_sync", create, model="m", seed=3) == "ok"
    assert [s["model"] for s in seen] == [LIGHT, SUPER] and "extra_body" not in seen[1] and "fallback" in caplog.text


def test_without_a_measured_fallback_the_error_is_raised(monkeypatch):
    monkeypatch.setenv("NEMOTRON_MOVE", "1")

    def create(**kw):
        raise NotFound("gone")

    with pytest.raises(NotFound):
        nm.call("validator", create, model="m")


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
    monkeypatch.setenv("NEMOTRON_MOVE_BASE_URL", "https://tf.example/v1/")
    monkeypatch.setenv("NEMOTRON_MOVE_API_KEY", "tf-key")
    seen = []

    def urlopen(req, timeout=None):
        seen.append((req.full_url, req.get_header("Authorization")))
        return io.BytesIO(json.dumps({"data": [{"id": SUPER}]}).encode())

    monkeypatch.setattr(nm.urllib.request, "urlopen", urlopen)
    with caplog.at_level(logging.WARNING, logger="nemotron_move"):
        nm.check_models()
        nm.check_models()  # once per process
    assert seen == [("https://tf.example/v1/models", "Bearer tf-key")]
    assert caplog.text.count(LIGHT) == 1 and SUPER not in caplog.text.replace(LIGHT, "")


def test_without_its_own_base_url_and_key_the_check_sends_nothing(monkeypatch, caplog):
    """F7: the app's OPENAI_API_KEY must never go to a host the user did not name for this check."""
    monkeypatch.setenv("NEMOTRON_MOVE_CHECK", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-user-openai-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    sent = []
    monkeypatch.setattr(nm.urllib.request, "urlopen", lambda req, timeout=None: sent.append(req))
    with caplog.at_level(logging.WARNING, logger="nemotron_move"):
        nm.check_models()
        nm.check_models()
    assert sent == [] and caplog.text.count("skipped") == 1


def test_a_failed_listing_is_retried_later_not_marked_done(monkeypatch):
    monkeypatch.setenv("NEMOTRON_MOVE_CHECK", "1")
    monkeypatch.setenv("NEMOTRON_MOVE_BASE_URL", "https://tf.example/v1")
    monkeypatch.setenv("NEMOTRON_MOVE_API_KEY", "tf-key")
    now, calls = [1000.0], []

    def urlopen(req, timeout=None):
        calls.append(now[0])
        if len(calls) == 1:
            raise OSError("offline")
        return io.BytesIO(b'{"data": []}')

    monkeypatch.setattr(nm.time, "time", lambda: now[0])
    monkeypatch.setattr(nm.urllib.request, "urlopen", urlopen)
    nm.check_models()
    nm.check_models()  # inside the back-off window: no new request
    now[0] += nm.RETRY_LISTING_S + 1
    nm.check_models()
    nm.check_models()  # succeeded: done for this process
    assert calls == [1000.0, 1000.0 + nm.RETRY_LISTING_S + 1]
