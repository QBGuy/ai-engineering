# Scaled NGINX and FastAPI Gateway Architecture

This version extends the local NGINX and FastAPI architecture with production infrastructure for many users and bursty API traffic.

The main scaling idea is simple: run multiple stateless FastAPI gateway replicas behind NGINX, and move shared state into external systems.

## Topology

```mermaid
flowchart TB
    users((Users))
    dns[DNS]
    waf[Managed edge / WAF<br/>distributes traffic]

    subgraph proxy[NGINX edge proxy replicas]
        n1[NGINX 1]
        n2[NGINX 2]
    end

    subgraph gateways[Private FastAPI gateway replicas]
        g1[Gateway 1]
        g2[Gateway 2]
        g3[Gateway 3]
    end

    subgraph state[State]
        auth[(Auth DB)]
        redis[(Redis)]
    end

    subgraph services[Services]
        embed[Embeddings]
        infer[Inference]
        retrieval[Retrieval]
    end

    obs[Observability]

    users --> dns --> waf
    waf --> n1
    waf --> n2

    n1 --> g1
    n1 --> g2
    n1 --> g3
    n2 --> g1
    n2 --> g2
    n2 --> g3

    g1 --> auth
    g2 --> auth
    g3 --> auth

    g1 --> redis
    g2 --> redis
    g3 --> redis

    g1 --> embed
    g1 --> infer
    g1 --> retrieval

    g2 --> embed
    g2 --> infer
    g2 --> retrieval

    g3 --> embed
    g3 --> infer
    g3 --> retrieval

    g1 -.-> obs
    g2 -.-> obs
    g3 -.-> obs
```

## Components

- **DNS**: Resolves the public API hostname, such as `api.example.com`, to the edge infrastructure.
- **Managed edge / WAF**: Inspects requests for common web attacks, blocks malicious traffic, and distributes accepted requests across healthy NGINX replicas.
- **NGINX edge proxy replicas**: Part of the edge layer behind the WAF. They manage proxy connections, apply coarse limits, and load balance across healthy FastAPI gateway replicas.
- **FastAPI gateway replicas**: Run the same stateless application-gateway code. Any request should be able to land on any replica.
- **Auth DB**: Stores API keys, tenants, plans, model permissions, and account status.
- **Redis**: Stores fast shared state such as rate-limit counters, short-lived cache entries, idempotency keys, and request coordination locks.
- **Embeddings**: Private service for embedding requests. It can scale independently from the gateway.
- **Inference**: Private service for chat or completion requests. This is usually the expensive bottleneck in AI apps.
- **Retrieval**: Private service for search, vector lookup, or RAG context assembly.
- **Observability**: Receives logs, metrics, traces, request ids, latency, error rates, and per-tenant usage.

## Scaling Notes

- NGINX handles proxy connections, coarse request limits, forwarding headers, and load balancing.
- FastAPI gateways validate, authorize, route, apply application policy, and record metadata.
- Neither NGINX nor FastAPI gateways should perform heavy retrieval, long-running jobs, or model execution.
- Autoscale FastAPI gateway replicas on CPU, memory, latency, request rate, or in-flight requests.
- Run multiple NGINX replicas so one instance is not a single point of failure, and scale them separately from FastAPI.
- Set strict timeouts between NGINX, gateways, and every downstream service.
- Use connection pools so each gateway replica does not create excessive downstream connections.
- Apply coarse limits at NGINX and tenant-aware distributed limits using shared state such as Redis.
- Keep internal services private so clients can only reach them through the gateway.
- For streaming model responses, track in-flight connections as a scaling signal, not just request count.

## Why keep this separate from the local architecture?

The local architecture teaches the request path and responsibility split. This scaled version adds concepts that are unnecessary locally but important in production:

- A WAF before the edge proxy.
- Multiple FastAPI gateway replicas.
- Shared authentication and rate-limit state.
- Independent scaling of NGINX, gateways, and application services.
- Centralized observability.

## Connection pool capacity at scale

Each NGINX and FastAPI replica has its own connection pools. Adding replicas therefore increases the total number of possible downstream connections.

For example:

```text
3 FastAPI replicas x 50 connections per replica = up to 150 downstream connections
```

This is useful when downstream services can handle the concurrency, but dangerous when a database, model server, or external provider has a lower connection limit. Pool sizes should be planned across all replicas, not considered one replica at a time.

The trade-off is:

- Pools that are too small cause requests to wait and increase latency.
- Pools that are too large consume resources and can overload downstream services.
- Idle and request timeouts release connections that are no longer useful.
