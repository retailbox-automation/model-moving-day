import json, subprocess

import pytest

from model_moving_day import agent, freeze, tf_client as tf


def reply(tool_calls=None, content=""):
    tcs = [{"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
           for i, (n, a) in enumerate(tool_calls or [])]
    return tf.Reply(agent.SUPER, content, "", content, False, tcs, "tool_calls" if tcs else "stop",
                    {"prompt_tokens": 100, "completion_tokens": 20}, 0.0001, 0.5, {})


class FakeClient:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def chat(self, model, messages, **params):
        self.requests.append((model, messages, params))
        return self.replies.pop(0)


def brain(tmp_path, replies):
    return agent.Brain(FakeClient(replies), agent.SUPER, tmp_path / "steps.jsonl", log=lambda m: None)


def test_diagnosis_backed_by_the_call_log_becomes_a_fix(tmp_path):
    b = brain(tmp_path, [reply([("propose_fix", {"kind": "reasoning_off", "cause": "content starts with a newline"})])])
    fix = b.diagnose({"site": "s", "rung": "Super", "diagnoses": {"leading_whitespace": 3}})
    assert fix.kind == "reasoning_off" and fix.params == {"reasoning": "off"} and fix.by == agent.SUPER
    step = json.loads((tmp_path / "steps.jsonl").read_text().splitlines()[0])
    assert step["prompt_tokens"] == 100 and step["cost"] == 0.0001 and step["model"] == agent.SUPER
    _, _, params = b.client.requests[0]
    assert params["tool_choice"]["function"]["name"] == "propose_fix"
    assert params["chat_template_kwargs"] == {"force_nonempty_content": True}


def test_diagnosis_without_evidence_is_rejected(tmp_path):
    b = brain(tmp_path, [reply([("propose_fix", {"kind": "raise_max_tokens", "cause": "guess"})])])
    assert b.diagnose({"site": "s", "rung": "Nano", "diagnoses": {}}) is None


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "pkg").mkdir(parents=True)
    (r / "tests").mkdir()
    (r / "pkg" / "core.py").write_text("def run(func, *args, **kwargs):\n    response = func(*args, **kwargs)\n    return response\n")
    (r / "tests" / "test_core.py").write_text("def test_x():\n    pass\n")
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "i"], cwd=r, check=True)
    return r


PLAN = {"sites": [{"slug": "core_run", "site": "pkg/core.py:2", "is_async": False, "model": "L", "fallback": "S"}]}


def test_patch_loop_edits_source_and_refuses_frozen_tests(tmp_path, repo):
    fr = freeze.freeze(repo, [])
    b = brain(tmp_path, [
        reply([("read_file", {"path": "pkg/core.py", "start": 1, "end": 3})]),
        reply([("replace", {"path": "tests/test_core.py", "old": "pass", "new": "assert False"})]),
        reply([("replace", {"path": "pkg/core.py", "old": "response = func(*args, **kwargs)",
                            "new": "response = nemotron_move.call(\"core_run\", func, *args, **kwargs)"}),
               ("replace", {"path": "pkg/core.py", "old": "def run(", "new": "from pkg import nemotron_move\n\n\ndef run("}),
               ("finish", {"summary": "wrapped"})]),
    ])
    edits = b.write_patch(repo, fr, PLAN, "pkg.nemotron_move")
    assert [e["path"] for e in edits] == ["pkg/core.py", "pkg/core.py"]
    assert 'nemotron_move.call("core_run", func' in (repo / "pkg" / "core.py").read_text()
    tool_msgs = [m["content"] for _, msgs, _ in b.client.requests[-1:] for m in msgs if m["role"] == "tool"]
    assert any("frozen behaviour check" in m for m in tool_msgs)
    freeze.verify(repo, fr)


def test_an_edit_that_does_not_compile_is_reverted(tmp_path, repo):
    fr = freeze.freeze(repo, [])
    b = brain(tmp_path, [reply([("replace", {"path": "pkg/core.py", "old": "return response", "new": "return (("}),
                                ("finish", {"summary": "x"})])])
    assert b.write_patch(repo, fr, PLAN, "pkg.nemotron_move") == []
    assert "return response" in (repo / "pkg" / "core.py").read_text()


def test_brain_size_rule(tmp_path):
    (tmp_path / "a.py").write_text("x" * 400)
    assert agent.pick_brain(tmp_path) == agent.SUPER
    (tmp_path / "big.py").write_text("x" * 900_000)
    assert agent.pick_brain(tmp_path) == agent.ULTRA
