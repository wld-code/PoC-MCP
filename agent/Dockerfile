FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh

ENV LLM_PROVIDER=mock

ENTRYPOINT ["./entrypoint.sh"]

# Interactive REPL by default (the `agent` CLI service); docker-compose
# overrides the command for the `backend` API service (uvicorn) and for
# one-shot headless runs (`python headless.py "..."`).
CMD ["python", "agent.py"]
