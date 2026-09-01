FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/competition/src:/opt/competition
RUN useradd --create-home --uid 10001 live
WORKDIR /opt/competition
COPY src ./src
COPY deployment/__init__.py ./deployment/__init__.py
COPY deployment/live_server ./deployment/live_server
RUN pip install --no-cache-dir numpy==2.1.3 pandas==2.2.3 pyyaml==6.0.2 \
    && mkdir /data && chown live:live /data
USER live
EXPOSE 8080
ENTRYPOINT ["python", "-m", "deployment.live_server.app", "--host", "0.0.0.0", "--db", "/data/competition.sqlite3"]
