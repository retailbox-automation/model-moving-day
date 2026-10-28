"""Synthetic app for the scan control: two literal models, one from the environment."""
import os

from openai import OpenAI

MODEL = "openai/gpt-4.1-mini-2025-04-14"
VOICE = "gpt-4o-mini-tts"
SMALL = "gpt-4.1-nano"
OLD = "gpt-4o-2024-05-13"
SUFFIX = "-x"
DYNAMIC = f"gpt-4o{SUFFIX}"
SHOUT = "GPT-4O"
client = OpenAI()


def summarize(text):
    return client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": text}])


def extract(text):
    return client.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": text}])


def answer(question):
    return client.responses.create(model=os.environ["APP_MODEL"], input=question)
