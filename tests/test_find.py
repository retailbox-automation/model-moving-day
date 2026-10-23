from pathlib import Path

from model_moving_day import find

REPO = Path(__file__).parent / "fixtures" / "find_repo"


def sites():
    return {(s.file, s.func): s for s in find.find_call_sites(REPO)}


def test_finds_every_llm_call_outside_tests():
    got = sites()
    assert set(got) == {
        ("app/openai_app.py", "summarize"), ("app/openai_app.py", "Extractor.extract"),
        ("app/openai_app.py", "answer"), ("app/openai_app.py", "embed"),
        ("app/litellm_app.py", "plan"), ("app/litellm_app.py", "critique"),
        ("app/anthropic_app.py", "chat"),
    }


def test_call_site_details():
    got = sites()
    s = got[("app/openai_app.py", "summarize")]
    assert (s.sdk, s.api, s.kind, s.is_async) == ("openai", "chat.completions.create", "chat", False)
    assert s.model_expr == "DEFAULT_MODEL" and s.model_literal == "gpt-4.1-mini"  # resolved through a module constant
    assert {"seed", "temperature"} <= set(s.kwargs)
    assert s.id == f"app/openai_app.py:{s.line}"

    e = got[("app/openai_app.py", "Extractor.extract")]
    assert e.is_async and e.model_expr == "self.model" and e.model_literal is None
    assert "parallel_tool_calls" in e.kwargs

    assert got[("app/openai_app.py", "answer")].api == "responses.create"
    assert got[("app/openai_app.py", "embed")].kind == "embedding"
    assert got[("app/litellm_app.py", "critique")].sdk == "litellm"
    assert got[("app/litellm_app.py", "critique")].model_literal == "gpt-4o-mini"
    assert got[("app/anthropic_app.py", "chat")].sdk == "anthropic"


def test_model_literals_and_env_vars():
    lits = {(m.file, m.value) for m in find.find_model_literals(REPO)}
    assert ("app/openai_app.py", "gpt-4.1-mini") in lits and ("app/openai_app.py", "o4-mini") in lits
    assert ("app/anthropic_app.py", "claude-sonnet-4-5-20250929") in lits
    assert not any(f.startswith("tests/") for f, _ in lits)
    envs = {(e.file, e.name) for e in find.find_env_vars(REPO)}
    assert envs == {("app/openai_app.py", "EXTRACT_MODEL"), ("app/openai_app.py", "ANSWER_MODEL")}


def test_unparsable_file_is_reported_not_skipped(tmp_path):
    (tmp_path / "bad.py").write_text("def f(:\n")
    (tmp_path / "ok.py").write_text("import litellm\nlitellm.completion(model='gpt-4o', messages=[])\n")
    res = find.scan(tmp_path)
    assert [s.file for s in res.sites] == ["ok.py"]
    assert [e[0] for e in res.errors] == ["bad.py"]
