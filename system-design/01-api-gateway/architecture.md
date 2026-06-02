# API Gateway Architecture

## Request flow

```mermaid
sequenceDiagram
    participant C as Client
    participant G as API Gateway
    participant E as Embeddings Service
    participant I as Inference Service

    C->>G: POST /v1/embeddings or /v1/chat/completions\nX-API-Key: dev-key
    G->>G: Create or read request_id
    G->>G: Authenticate API key
    G->>G: Validate payload with Pydantic
    alt /v1/embeddings
        G->>E: POST /internal/embed
        E-->>G: Toy embedding vector
    else /v1/chat/completions
        G->>I: POST /internal/chat
        I-->>G: Toy model response
    end
    G-->>C: Normalized JSON response + request_id
```

## Deployment topology

```mermaid
flowchart TB
    internet((Internet))
    dns[DNS]
    tls[TLS termination\nload balancer / reverse proxy]

    subgraph public[Public subnet or public container app]
        gateway[API Gateway]
    end

    subgraph private[Private network]
        embed[Embeddings service]
        infer[Inference service]
        db[(Postgres\nAPI keys / tenants)]
        redis[(Redis\nrate limits / cache)]
    end

    internet --> dns --> tls --> gateway
    gateway --> embed
    gateway --> infer
    gateway -. future .-> db
    gateway -. future .-> redis
```

## What belongs in the gateway?

Good gateway responsibilities:

- Authentication and caller identification.
- Basic authorization such as whether a caller can access a model family.
- Request body validation and maximum size checks.
- Routing to internal services.
- Request ids, logging, metrics, and trace propagation.
- Consistent error responses.
- Handoff to dedicated rate-limit, cache, and billing systems.

Responsibilities to avoid putting in the gateway:

- Long-running model jobs.
- Heavy retrieval or vector search logic.
- Complex business workflows.
- Training jobs or batch processing.
- Anything that makes the gateway a bottleneck or single giant application.

## Why internal services are private

The embeddings and inference services are intentionally not exposed to the host in `docker-compose.yml`. Only the gateway binds a host port. This mirrors production: internal services should usually be reachable only from trusted infrastructure, not from arbitrary clients.

## Why use barebones FastAPI here?

FastAPI is not the only way to build a gateway. It is useful for learning because you can see each gateway mechanism as normal Python code:

- Header parsing for API keys.
- Pydantic models for validation.
- `httpx` calls for service-to-service proxying.
- Consistent response wrapping.

Once the mechanics are clear, the same ideas transfer to Kong, Envoy, NGINX, Traefik, cloud API gateways, or Kubernetes ingress controllers.
