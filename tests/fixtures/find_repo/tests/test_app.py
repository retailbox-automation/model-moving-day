from openai import OpenAI


def test_live():
    OpenAI().chat.completions.create(model="gpt-4.1-mini", messages=[])
