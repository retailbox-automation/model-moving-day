import asyncio, importlib, io, json, logging

import pytest

from model_moving_day.templates import nemotron_move as nm

LIGHT, SUPER = "nvidia/Nemotron-3_5-Lightning", "nvidia/nemotron-3-super-120b-a12b"
OFF = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
CHAT = "openai.resources.chat.completions"


class FakeClient:
    """Stands in for an openai client: with_options returns a copy pointed elsewhere."""

    def __init__(self, seen, base="https://api.openai.example/v1", key="sk-app", fail_models=()):
        self.seen, self.base, self.key, self.fail_models = seen, base, key, fail_models

    def with_options(self, base_url, api_key):
        return FakeClient(self.seen, base_url, api_key, self.fail_models)


class NotFound(Exception):
    status_code = 404


class BadRequest(Exception):
    status_code = 400


class Completions:
    def __init__(self, client):
        self._client = client

    def create(self, **kw):
        self._client.seen.append({"base": self._client.base, "key": self._client.key, **kw})
        if kw.get("model") in self._client.fail_models:
            raise self._client.fail_models[kw["model"]]("gone")
        return "ok"


class AsyncCompletions(Completions):
    async def create(self, **kw):
        return Completions.create(self, **kw)


class Responses(Completions):
    pass


Completions.__module__ = AsyncCompletions.__module__ = "openai.resources.chat.completions.completions"
Responses.__module__ = "openai.resources.responses.responses"


def chat(seen, **kw):
    return Completions(FakeClient(seen, **kw)).create


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    importlib.reload(nm)
    nm.SITES.update({
        "retry_sync": {"model": LIGHT, "params": OFF, "drop": ["seed"], "only": CHAT,
                       "fallback": {"model": SUPER, "params": {}, "drop": ["seed"]}},
        "validator": {"model": SUPER, "params": {}, "drop": [], "min_max_tokens": 8000, "only": CHAT, "fallback": None},
        "guarded": {"keep": True},
    })
    monkeypatch.setenv("NEMOTRON_MOVE_CHECK", "0")
    monkeypatch.setenv("NEMOTRON_MOVE_BASE_URL", "https://api.tokenfactory.example/v1")
    monkeypatch.setenv("NEMOTRON_MOVE_API_KEY", "tf-key")


def test_off_by_default_the_callers_model_and_endpoint_are_untouched(monkeypatch):
    monkeypatch.delenv("NEMOTRON_MOVE", raising=False)
    seen = []
    nm.call("retry_sync", chat(seen), model="gpt-4.1-mini", seed=1)
    assert seen == [{"base": "https://api.openai.example/v1", "key": "sk-app", "model": "gpt-4.1-mini", "seed": 1}]


def test_a_moved_call_goes_to_token_factory_with_the_census_model_and_fixes(monkeypatch):
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    seen = []
    nm.call("retry_sync", chat(seen), model="gpt-4.1-mini", seed=1,
            extra_body={"x": 1, "chat_template_kwargs": {"keep": True}, "seed": 2})
    assert seen[0]["base"] == "https://api.tokenfactory.example/v1" and seen[0]["key"] == "tf-key"
    assert seen[0]["model"] == LIGHT and "seed" not in seen[0]
    assert seen[0]["extra_body"] == {"x": 1, "chat_template_kwargs": {"keep": True, "enable_thinking": False}}


def test_without_the_token_factory_endpoint_nothing_is_moved(monkeypatch, caplog):
    """N3: moving the model name while the app still talks to its own vendor would send a Nemotron ID to OpenAI."""
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    monkeypatch.delenv("NEMOTRON_MOVE_BASE_URL")
    seen = []
    with caplog.at_level(logging.WARNING, logger="nemotron_move"):
        nm.call("retry_sync", chat(seen), model="gpt-4.1-mini")
        nm.call("retry_sync", chat(seen), model="gpt-4.1-mini")
    assert [s["model"] for s in seen] == ["gpt-4.1-mini"] * 2 and seen[0]["base"] == "https://api.openai.example/v1"
    assert caplog.text.count("not moved") == 1


def test_only_the_measured_call_shape_is_moved(monkeypatch):
    """A funnel also carries Responses-API calls and other providers: those keep their model and endpoint."""
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    seen = []
    nm.call("retry_sync", Responses(FakeClient(seen)).create, model="gpt-4.1-mini")
    nm.call("retry_sync", lambda **kw: seen.append(dict(kw, base="plain")), model="claude-x")
    assert [(s["model"], s["base"]) for s in seen] == [("gpt-4.1-mini", "https://api.openai.example/v1"), ("claude-x", "plain")]


def test_litellm_functions_get_api_base_and_key(monkeypatch):
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    nm.SITES["lite"] = {"model": LIGHT, "params": {}, "drop": [], "only": "litellm", "fallback": None}
    seen = []

    def completion(**kw):
        seen.append(kw)
    completion.__module__ = "litellm.main"
    nm.call("lite", completion, model="gpt-4o", api_key="sk-app")
    assert seen == [{"model": LIGHT, "api_key": "tf-key", "api_base": "https://api.tokenfactory.example/v1"}]


def test_budget_floor_is_set_when_absent_and_never_lowers_a_larger_budget(monkeypatch):
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    seen = []
    nm.call("validator", chat(seen), model="m")
    nm.call("validator", chat(seen), model="m", max_tokens=16000)
    nm.call("validator", chat(seen), model="m", max_completion_tokens=100)
    assert [s.get("max_tokens") for s in seen] == [8000, 16000, None] and seen[2]["max_completion_tokens"] == 8000


def test_per_site_env_override(monkeypatch):
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    monkeypatch.setenv("NEMOTRON_MODEL_RETRY_SYNC", SUPER)
    assert nm.model_for("retry_sync", "gpt-4.1-mini") == SUPER and nm.model_for("guarded", "gpt-4.1-mini") == "gpt-4.1-mini"


def test_an_unroutable_outer_site_is_moved_by_the_funnel_below_it_with_its_own_plan(monkeypatch):
    """instructor: the validator calls an instructor-patched create (a plain function, not routable); the retry
    funnel below it holds the SDK method. The validator's measured model must win, routed at the funnel."""
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    seen = []
    create = chat(seen)

    def instructor_create(**kw):  # what llm_validators.py:34 calls
        return nm.call("retry_sync", create, **kw)

    nm.call("validator", instructor_create, model="gpt-3.5-turbo", seed=5)
    assert seen == [{"base": "https://api.tokenfactory.example/v1", "key": "tf-key", "model": SUPER, "seed": 5,
                     "max_tokens": 8000}]  # validator's plan: SUPER, budget floor, no seed drop
    nm.call("retry_sync", create, model="m")  # outside the outer call the funnel moves with its own plan
    assert seen[-1]["model"] == LIGHT and seen[-1]["base"] == "https://api.tokenfactory.example/v1"


def test_a_kept_outer_site_stops_a_moved_funnel_from_moving_its_calls(monkeypatch):
    """N2: the validator stayed on the old model (red cell); the funnel below it must not move its calls."""
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    seen = []
    create = chat(seen)
    nm.call("guarded", lambda **kw: nm.call("retry_sync", create, **kw), model="gpt-4.1-mini", seed=1)
    assert seen == [{"base": "https://api.openai.example/v1", "key": "sk-app", "model": "gpt-4.1-mini", "seed": 1}]


def test_nested_async_site_keeps_the_outer_choice(monkeypatch):
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    seen = []
    create = AsyncCompletions(FakeClient(seen)).create

    async def funnel(**kw):
        return await nm.acall("retry_sync", create, **kw)

    asyncio.run(nm.acall("guarded", funnel, model="m"))
    asyncio.run(nm.acall("validator", funnel, model="m"))
    assert [s["model"] for s in seen] == ["m", SUPER]


def test_unknown_model_falls_back_once_to_the_measured_fallback_with_its_own_params(monkeypatch, caplog):
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    seen = []
    with caplog.at_level(logging.WARNING, logger="nemotron_move"):
        assert nm.call("retry_sync", chat(seen, fail_models={LIGHT: NotFound}), model="m", seed=3) == "ok"
    assert [s["model"] for s in seen] == [LIGHT, SUPER] and "extra_body" not in seen[1] and "fallback" in caplog.text
    assert seen[1]["base"] == "https://api.tokenfactory.example/v1"


def test_a_400_is_raised_not_treated_as_a_retired_model(monkeypatch):
    """N12: only RETIRED_STATUSES trigger the fallback."""
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    seen = []
    with pytest.raises(BadRequest):
        nm.call("retry_sync", chat(seen, fail_models={LIGHT: BadRequest}), model="m")
    assert [s["model"] for s in seen] == [LIGHT]


def test_without_a_measured_fallback_the_error_is_raised(monkeypatch):
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    with pytest.raises(NotFound):
        nm.call("validator", chat([], fail_models={SUPER: NotFound}), model="m")


def test_async_call_falls_back_too(monkeypatch):
    monkeypatch.setenv("NEMOTRON_MOVE", "1")
    seen = []
    create = AsyncCompletions(FakeClient(seen, fail_models={LIGHT: NotFound})).create
    assert asyncio.run(nm.acall("retry_sync", create, model="m")) == "ok" and [s["model"] for s in seen] == [LIGHT, SUPER]


def test_deprecation_signal_warns_for_unlisted_models(monkeypatch, caplog):
    monkeypatch.setenv("NEMOTRON_MOVE_CHECK", "1")
    monkeypatch.setenv("NEMOTRON_MOVE_BASE_URL", "https://tf.example/v1/")
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
    monkeypatch.delenv("NEMOTRON_MOVE_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-user-openai-key")
    sent = []
    monkeypatch.setattr(nm.urllib.request, "urlopen", lambda req, timeout=None: sent.append(req))
    with caplog.at_level(logging.WARNING, logger="nemotron_move"):
        nm.check_models()
        nm.check_models()
    assert sent == [] and caplog.text.count("skipped") == 1


def test_a_failed_listing_is_retried_later_not_marked_done(monkeypatch):
    monkeypatch.setenv("NEMOTRON_MOVE_CHECK", "1")
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
