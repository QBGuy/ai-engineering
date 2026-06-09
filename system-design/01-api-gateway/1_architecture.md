# NGINX and FastAPI Gateway Architecture

The gateway is split into two layers:

- **NGINX edge proxy:** the only public service. It handles connections, coarse protection, forwarding headers, and load balancing.
- **FastAPI application gateway:** a private service. It handles API authentication, JSON validation, application policy, routing, and response shaping.

This split keeps cheap transport-level work out of Python while preserving normal application code for policies that need data models, databases, or business context.

Here, **edge** means the outer boundary or layer where external traffic enters infrastructure serving your system. NGINX is the local edge proxy because it is the first reachable component, not because "edge" is a special type of server.

## Request flow

```mermaid
sequenceDiagram
    participant C as Client
    participant N as NGINX Edge
    participant G as FastAPI Gateway
    participant E as Embeddings Service
    participant I as Inference Service

    C->>N: POST /v1/...<br/>X-API-Key + optional X-Request-ID
    N->>N: Enforce body-size and per-IP rate limits
    N->>N: Create or preserve X-Request-ID
    N->>G: Proxy request with forwarding headers
    G->>G: Authenticate API key
    G->>G: Validate payload with Pydantic
    alt /v1/embeddings
        G->>E: POST /internal/embed
        E-->>G: Toy embedding vector
    else /v1/chat/completions
        G->>I: POST /internal/chat
        I-->>G: Toy model response
    end
    G-->>N: Normalized JSON response
    N-->>C: Response + X-Request-ID header
```

## Local deployment topology

```mermaid
flowchart LR
    client[Client]

    subgraph public[Public edge]
        nginx[NGINX<br/>localhost:8080]
    end

    subgraph private[Docker network only]
        gateway[FastAPI Gateway<br/>gateway:8080]
        embed[Embeddings Service<br/>embeddings-service:8001]
        infer[Inference Service<br/>inference-service:8002]
    end

    client -->|HTTP| nginx
    nginx -->|proxy_pass| gateway
    gateway --> embed
    gateway --> infer
```

Only NGINX publishes a host port. The FastAPI gateway and backend services are reachable only inside the Docker network.

## Responsibility boundary

| Concern | NGINX edge | FastAPI gateway |
| --- | --- | --- |
| Public listener | Yes | No |
| TLS termination in production | Yes | No |
| Connection and keep-alive handling | Yes | No |
| Request body-size limit | Yes | Optional second check |
| Coarse per-IP rate limit | Yes | No |
| Load balancing gateway replicas | Yes | No |
| API key or JWT authentication | No | Yes |
| Tenant and model authorization | No | Yes |
| JSON schema validation | No | Yes |
| Route to application services | No | Yes |
| Normalize API responses | No | Yes |
| Token quotas and billing policy | No | Yes, usually with shared state |

NGINX rejects clearly invalid or excessive traffic before it consumes a Python worker. FastAPI performs checks that require understanding the caller or request body.

## Connections and upstreams

Each arrow in the architecture represents a separate network connection. NGINX groups possible FastAPI destinations into an **upstream pool**, while NGINX and FastAPI maintain **connection pools** that reuse open connections.

These concepts, along with health checks and failover behavior, are explained in `4_detailed_concepts.md`.

## Scaling the gateway

NGINX does not make one FastAPI process execute Python faster. It makes horizontal scaling practical by distributing traffic across multiple gateway replicas in an upstream pool.

For that to work safely, gateway replicas must remain stateless:

- Store API keys, tenants, and permissions in a shared database.
- Store distributed rate-limit counters, idempotency keys, and caches in Redis or another shared store.
- Send logs, metrics, and traces to external observability systems.
- Do not rely on one replica's memory for user-visible state.

For streamed AI responses, NGINX buffering must remain disabled and scaling should consider open connections and request duration, not only requests per second.

## Production evolution

On a VM or Docker host, NGINX can remain the public edge and proxy to several FastAPI replicas. On Kubernetes, an ingress or Gateway API implementation usually fills the NGINX role. In Azure, Azure API Management or Container Apps ingress may replace the self-managed NGINX layer.
