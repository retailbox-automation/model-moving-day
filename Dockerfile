# Model Moving Day demo for judges: REPLAY only, from demo/data/census.json (built from the evidence by
# `python -m demo.build`). No key, no model calls, nothing written at run time. Built here for a later deployment; not
# deployed from this repository.
FROM python:3.12.7-slim-bookworm

WORKDIR /app
COPY demo/__init__.py demo/server.py demo/
COPY demo/data/census.json demo/data/census.json
COPY LICENSE README.md CENSUS.md ./

RUN useradd --create-home app
USER app

ENV HOST=0.0.0.0 \
    PORT=8080 \
    PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "demo.server"]
