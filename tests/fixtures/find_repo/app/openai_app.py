import os
from openai import AsyncOpenAI, OpenAI

client = OpenAI()
DEFAULT_MODEL = "gpt-4.1-mini"


def summarize(text):
    resp = client.chat.completions.create(model=DEFAULT_MODEL, messages=[{"role": "user", "content": text}],
                                          seed=7, temperature=0)
    return resp.choices[0].message.content


class Extractor:
    def __init__(self):
        self.client = AsyncOpenAI()
        self.model = os.getenv("EXTRACT_MODEL", "gpt-4o-mini")

    async def extract(self, text):
        return await self.client.chat.completions.create(model=self.model, messages=[], tools=[], parallel_tool_calls=False)


def answer(q):
    return client.responses.create(model=os.environ.get("ANSWER_MODEL", "o4-mini"), input=q)


def embed(texts):
    return client.embeddings.create(model="text-embedding-3-small", input=texts)


def not_an_llm(db):
    return db.records.create(name="gpt-4o")  # a model-looking string outside an LLM call
