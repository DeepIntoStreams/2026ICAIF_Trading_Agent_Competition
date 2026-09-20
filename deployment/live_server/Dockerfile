FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/competition/src:/opt/competition
RUN useradd --create-home --uid 10001 live
WORKDIR /opt/competition
COPY src ./src
COPY data ./data
COPY competition ./competition
COPY deployment/__init__.py ./deployment/__init__.py
COPY deployment/bootstrap/__init__.py ./deployment/bootstrap/__init__.py
COPY deployment/bootstrap/common.py ./deployment/bootstrap/common.py
COPY deployment/bootstrap/bootstrap_config.json ./deployment/bootstrap/bootstrap_config.json
COPY deployment/bootstrap/init_database.py ./deployment/bootstrap/init_database.py
COPY deployment/bootstrap/publish_initial_observations.py ./deployment/bootstrap/publish_initial_observations.py
COPY deployment/bootstrap/verify_setup.py ./deployment/bootstrap/verify_setup.py
COPY deployment/live_server ./deployment/live_server
RUN pip install --no-cache-dir numpy==2.1.3 pandas==2.2.3 pyyaml==6.0.2 \
        "yfinance>=0.2.40" "exchange-calendars>=4.5" \
        fastapi==0.116.1 uvicorn==0.35.0 "psycopg[binary]==3.2.10"
USER live
EXPOSE 8080
