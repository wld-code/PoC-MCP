# Docker Tutorial — building and running the stack

Three images, one Compose file. This covers building, running, and what to
change for a real deployment.

## 1. The images

Each component has its own `Dockerfile`, all based on `python:3.12-slim`:

| Image | Build context | Listens on | Notes |
| --- | --- | --- | --- |
| `api` | `./api` | 8000 | SQLite at `/data/data.db` (volume-backed) |
| `mcp-server` | `./mcp_server` | 8001 | Stateless; talks to `api` over HTTP |
| `agent` | `./agent` | — | One-shot/interactive client |

Each Dockerfile copies `requirements.txt` first and installs before copying the
source, so dependency layers cache across code changes.

## 2. Compose

`docker-compose.yml` wires them on a shared network where services resolve each
other by name (`api`, `mcp-server`):

- `api` mounts a named volume `api-data` at `/data` so the SQLite file survives
  restarts, and has a healthcheck on `/health`.
- `mcp-server` waits for `api` to be healthy (`depends_on: condition:
  service_healthy`) and points at it via `API_BASE_URL=http://api:8000`.
- `agent` is in the `manual` profile and has `stdin_open`/`tty`, so it does
  **not** start with `up` — you run it on demand.

### Run it

```bash
cp .env.example .env          # set LLM_PROVIDER + the matching API key
docker compose up -d --build api mcp-server
docker compose ps             # both healthy?

# One-shot question:
docker compose run --rm agent python agent.py "Top 3 products by revenue?"

# Interactive chat:
docker compose run --rm agent python agent.py
```

The agent reads `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `MISTRAL_API_KEY` from
your `.env` (Compose passes them through).

### Inspect / debug

```bash
docker compose logs -f api          # see requests hit the API
docker compose logs -f mcp-server   # see the StreamableHTTP session manager
curl localhost:8000/docs            # OpenAPI UI for the raw API
```

### Tear down

```bash
docker compose down            # keep the data volume
docker compose down -v         # also delete the SQLite volume
```

## 3. Data seeding

The API auto-seeds on first boot when the DB is empty (`AUTO_SEED=1`, the
default). To reseed from scratch, delete the volume (`docker compose down -v`)
or run `python seed.py` inside the api container:

```bash
docker compose exec api python seed.py
```

## 4. Taking it toward production

This Compose setup is a PoC. For a real deployment:

- **Don't rely on `AUTO_SEED`.** Seed as an explicit one-off job; run the API
  read-mostly.
- **SQLite is single-writer.** Fine for a demo. For real load, move the API to
  Postgres/MySQL and drop the volume. The MCP server and agent don't change.
- **Pin and scan images.** Pin base image digests; run `docker scout` / Trivy.
- **Run as non-root.** Add a non-root `USER` to each Dockerfile.
- **Secrets stay in the environment**, never baked into images. The agent's
  API key is injected at runtime; the example `.env` is git-ignored.
- **Front the MCP server with TLS + auth** if it's reachable beyond localhost
  (a reverse proxy or API gateway terminating TLS and checking a token).
- **Resource limits & healthchecks.** The API already has a healthcheck; add
  `deploy.resources` limits or move to the K8s probes/limits in the next doc.

## 5. Multi-arch / registry

To push to a registry for Kubernetes:

```bash
docker build -t <registry>/mcp-poc-api:1.0 ./api
docker build -t <registry>/mcp-poc-mcp:1.0 ./mcp_server
docker build -t <registry>/mcp-poc-agent:1.0 ./agent
docker push <registry>/mcp-poc-api:1.0   # etc.
```

Then set those image names in the K8s manifests.

Next: [`03-kubernetes.md`](03-kubernetes.md).
