FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default to the in-cluster / in-compose MCP service URL.
ENV MCP_SERVER_URL=http://mcp-server:8001/mcp
ENV LLM_PROVIDER=claude

# Interactive by default; override the command to ask a one-shot question.
CMD ["python", "agent.py"]
