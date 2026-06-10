# Kubernetes Tutorial — deploying the stack

This deploys the same three components to Kubernetes:

- **API** → `Deployment` + `Service` + `PersistentVolumeClaim` (SQLite file).
- **MCP server** → `Deployment` (2 replicas, stateless) + `Service`.
- **Agent** → a `Job` (one-shot query) reading the LLM key from a `Secret`.

Manifests live in [`k8s/`](../k8s). They assume a namespace `mcp-poc`.

```
                 ┌─────────────── Namespace: mcp-poc ───────────────┐
   kubectl ▶ Job │ agent ──▶ Service mcp-server ──▶ Deployment       │
   (logs)        │                 (2 pods)         mcp-server       │
                 │                                     │             │
                 │                                     ▼             │
                 │                 Service api ──▶ Deployment api ──▶ PVC (SQLite)
                 │                                                   │
                 │  Secret: llm-secrets (ANTHROPIC_API_KEY, ...)     │
                 └───────────────────────────────────────────────────┘
```

## 0. Prerequisites

- A cluster: `minikube`, `kind`, Docker Desktop, or a cloud cluster.
- `kubectl` pointed at it.
- The three images available to the cluster (built locally for minikube/kind,
  or pushed to a registry for a cloud cluster).

## 1. Make the images available

**minikube** — build straight into the cluster's Docker daemon:

```bash
eval $(minikube docker-env)
docker build -t mcp-poc/api:latest        ./api
docker build -t mcp-poc/mcp-server:latest ./mcp_server
docker build -t mcp-poc/agent:latest      ./agent
```

**kind** — build locally, then load:

```bash
docker build -t mcp-poc/api:latest ./api          # repeat for the other two
kind load docker-image mcp-poc/api:latest mcp-poc/mcp-server:latest mcp-poc/agent:latest
```

**Cloud** — push to a registry and replace `mcp-poc/<name>:latest` in the
manifests with your registry path (and set `imagePullPolicy: Always`).

The manifests use `imagePullPolicy: IfNotPresent` so locally-built images are
used without a registry.

## 2. Create the namespace

```bash
kubectl apply -f k8s/namespace.yaml
```

## 3. Deploy the API (with persistent SQLite)

```bash
kubectl apply -f k8s/api.yaml
kubectl -n mcp-poc rollout status deploy/api
```

`k8s/api.yaml` contains:

- a **PVC** `api-data` (1Gi) mounted at `/data` for the SQLite file;
- a **Deployment** with `replicas: 1` (SQLite + a ReadWriteOnce volume means a
  single writer — don't scale this);
- readiness/liveness **probes** on `/health`;
- a **Service** `api` on port 8000 (in-cluster DNS: `http://api:8000`).

## 4. Deploy the MCP server

```bash
kubectl apply -f k8s/mcp-server.yaml
kubectl -n mcp-poc rollout status deploy/mcp-server
```

It's stateless, so `replicas: 2`. `API_BASE_URL=http://api:8000` points it at
the API Service. Exposed in-cluster as `http://mcp-server:8001` (MCP endpoint
at `/mcp`).

## 5. Create the LLM Secret

Never commit real keys. Create the Secret imperatively:

```bash
kubectl -n mcp-poc create secret generic llm-secrets \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-...
# (add OPENAI_API_KEY / MISTRAL_API_KEY if you use those providers)
```

Or copy `k8s/secret.example.yaml` to `k8s/secret.yaml`, fill it in, and
`kubectl apply -f k8s/secret.yaml` (keep `secret.yaml` out of git).

## 6. Run the agent as a Job

`k8s/agent-job.yaml` runs one query and exits. It reads the key via
`envFrom: secretRef: llm-secrets` and reaches the MCP server at
`http://mcp-server:8001/mcp`.

```bash
kubectl apply -f k8s/agent-job.yaml
kubectl -n mcp-poc wait --for=condition=complete job/agent-query --timeout=120s
kubectl -n mcp-poc logs job/agent-query
```

To ask a different question, edit `args` in the Job (or use a different name)
and re-apply. To re-run the same job:

```bash
kubectl -n mcp-poc delete job agent-query && kubectl apply -f k8s/agent-job.yaml
```

## 7. Verify the pieces

```bash
kubectl -n mcp-poc get pods,svc,pvc,job

# Port-forward the API to your laptop and curl it:
kubectl -n mcp-poc port-forward svc/api 8000:8000 &
curl localhost:8000/sales/summary

# Exec into the MCP pod to confirm it can reach the API:
kubectl -n mcp-poc exec deploy/mcp-server -- \
  python -c "import httpx; print(httpx.get('http://api:8000/health').json())"
```

## 8. Production hardening checklist

- **External access to MCP:** add an `Ingress` (TLS via cert-manager) plus
  authentication if clients live outside the cluster. Keep it internal
  otherwise.
- **Database:** replace SQLite+PVC with a managed Postgres for real workloads;
  then the API Deployment can scale past 1 replica.
- **Probes & limits:** set CPU/memory `requests`/`limits` on each container;
  the API and MCP server already have probes — add them to the agent if you run
  it as a long-lived service instead of a Job.
- **Secrets:** prefer an external secrets manager (External Secrets Operator,
  cloud secret store) over raw `Secret` objects.
- **Autoscaling:** add an `HorizontalPodAutoscaler` on `mcp-server` (it's
  stateless and CPU-bound on JSON forwarding).
- **NetworkPolicy:** restrict traffic so only `mcp-server` can reach `api`, and
  only the agent can reach `mcp-server`.
- **Long-running agent:** if you want a chat *service* rather than a Job, wrap
  `agent.py`'s loop in a small FastAPI/WebSocket server, make it a Deployment +
  Service, and front it with an Ingress.

## 9. Clean up

```bash
kubectl delete namespace mcp-poc
```

That removes every resource (Deployments, Services, PVC, Secret, Job) in one
shot.
