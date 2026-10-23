import anthropic

c = anthropic.Anthropic()


def chat(msgs):
    return c.messages.create(model="claude-sonnet-4-5-20250929", max_tokens=512, messages=msgs)
