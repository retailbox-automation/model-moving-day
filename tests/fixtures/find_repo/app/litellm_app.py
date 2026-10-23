import litellm
from litellm import acompletion


def plan(cfg, msgs):
    return litellm.completion(model=cfg.model, messages=msgs, top_k=40)


async def critique(msgs):
    return await acompletion(model="gpt-4o-mini", messages=msgs)
