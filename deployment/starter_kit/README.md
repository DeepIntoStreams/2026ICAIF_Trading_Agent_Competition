# Minimal participant Starter Kit

The participant owns and runs this process. It polls the competition server, requests the
currently available observation and private team status, calls the local agent, and submits target
weights. The server never imports or runs `agent.py`.

```text
client.status()
  -> no session: wait
  -> already submitted: wait
  -> observation available:
       client.observation()
       Agent.decide(observation)
       client.submit(target weights)
```

Run one interaction after receiving a team API key:

```bash
COMPETITION_BASE_URL=http://127.0.0.1:8080 \
COMPETITION_API_KEY='<team-api-key>' \
python -m deployment.starter_kit.client
```

The included `Agent` deliberately allocates 5% to the first asset. Teams replace only its
`decide()` implementation. `client.py` uses only the Python standard library.

