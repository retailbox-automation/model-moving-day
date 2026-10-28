from openai import OpenAI

OpenAI().chat.completions.create(model="gpt-4o", messages=[])
