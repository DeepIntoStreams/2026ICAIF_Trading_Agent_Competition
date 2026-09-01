FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ICAIF_REPO_ROOT=/opt/competition

RUN useradd --create-home --uid 10001 evaluator
WORKDIR /opt/competition
COPY pyproject.toml ./
COPY src ./src
COPY competition/code ./competition/code
COPY deployment/codabench ./deployment/codabench
RUN pip install --no-cache-dir . jsonschema==4.23.0

USER evaluator
