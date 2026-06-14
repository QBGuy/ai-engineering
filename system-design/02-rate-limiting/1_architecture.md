# Rate-Limiting Architecture

## Request flow

```mermaid
%%{init: {
    "theme": "base",
    "themeVariables": {
        "fontFamily": "Geist, ui-sans-serif, system-ui, sans-serif",
        "fontSize": "14px",
        "background": "#0b1220",
        "primaryColor": "#182235",
        "primaryBorderColor": "#64748b",
        "primaryTextColor": "#e5edf7",
        "lineColor": "#64748b",
        "actorBkg": "#182235",
        "actorBorder": "#64748b",
        "actorTextColor": "#e5edf7",
        "actorLineColor": "#475569",
        "signalColor": "#60a5fa",
        "signalTextColor": "#94a3b8",
        "labelBoxBkgColor": "#132340",
        "labelBoxBorderColor": "#60a5fa",
        "labelTextColor": "#dbeafe",
        "loopTextColor": "#94a3b8",
        "activationBkgColor": "#132340",
        "activationBorderColor": "#60a5fa"
    },
    "sequence": {
        "diagramMarginX": 24,
        "diagramMarginY": 16,
        "actorMargin": 48,
        "width": 170,
        "height": 54,
        "messageMargin": 32
    }
}}%%
sequenceDiagram
    participant C as Client
    participant N as NGINX Edge
    participant G as FastAPI Gateway
    participant R as Redis
    participant I as Inference Service

    C->>N: POST /v1/chat/completions + X-API-Key
    N->>N: Apply coarse per-IP request limit
    N->>G: Forward accepted request
    G->>G: Authenticate tenant and estimate token cost
    G->>R: Atomically check request and token buckets
    alt both buckets have capacity
        R-->>G: allowed + remaining capacity
        G->>I: Forward expensive model request
        I-->>G: Mock completion
        G-->>C: 200 + rate-limit headers
    else either bucket is exhausted
        R-->>G: rejected + retry delay
        G-->>C: 429 + Retry-After
    end
```

## Local deployment topology

**Layers:** Public edge → Docker network only

```mermaid
%%{init: {
    "theme": "base",
    "themeVariables": {
        "fontFamily": "Geist, ui-sans-serif, system-ui, sans-serif",
        "fontSize": "14px",
        "background": "#0b1220",
        "lineColor": "#64748b",
        "textColor": "#e5edf7",
        "primaryTextColor": "#e5edf7",
        "edgeLabelBackground": "#0b1220",
        "clusterBkg": "#111827",
        "clusterBorder": "#334155"
    },
    "flowchart": {
        "curve": "bumpX",
        "htmlLabels": true,
        "nodeSpacing": 36,
        "rankSpacing": 58,
        "padding": 18
    }
}}%%
flowchart TB
    client("<b>Client</b><br/>public caller")

    subgraph public[" "]
        nginx("<b>NGINX</b><br/>localhost:8081")
    end

    subgraph private[" "]
        gateway("<b>FastAPI gateway</b><br/>policy enforcement")
        redis("<b>Redis</b><br/>shared bucket state")
        inference("<b>Mock inference service</b><br/>expensive work")
    end

    client --> nginx --> gateway
    gateway --> redis
    gateway -->|only when allowed| inference

    classDef defaultNode fill:#182235,stroke:#64748b,stroke-width:1.5px,color:#e5edf7
    classDef edgeNode fill:#132340,stroke:#60a5fa,stroke-width:1.75px,color:#dbeafe
    classDef focusNode fill:#2563eb,stroke:#93c5fd,stroke-width:2px,color:#ffffff
    classDef stateNode fill:#111827,stroke:#60a5fa,stroke-width:1.5px,color:#dbeafe

    class client,inference defaultNode
    class nginx edgeNode
    class gateway focusNode
    class redis stateNode

    style public fill:#0f1d35,stroke:#315a91,stroke-width:1.5px
    style private fill:#111827,stroke:#334155,stroke-width:1.5px

    linkStyle 0,1 stroke:#60a5fa,stroke-width:2.5px,color:#bfdbfe
    linkStyle 2 stroke:#64748b,stroke-width:1.5px,color:#cbd5e1
    linkStyle 3 stroke:#64748b,stroke-width:1.5px,color:#cbd5e1
```

Only NGINX publishes a host port. Redis, the gateway, and the inference service remain private.

## Responsibility boundary

| Component | Owns |
| --- | --- |
| **NGINX edge** | Coarse per-IP flood protection before any Python runs |
| **FastAPI gateway** | Auth, token cost estimation, limit header responses |
| **Redis** | Shared bucket state; executes the atomic allow/reject decision |

## Why two limiting layers?

An IP address is available before authentication and is cheap to inspect, but it is a weak identity. Many legitimate users may share one IP, and an abusive caller may rotate IPs.

An authenticated tenant is a stronger policy key, but checking it requires application work and shared state. The two layers protect different resources:

```text
Internet flood
  -> coarse IP limiter protects edge and gateway capacity
     -> tenant request/token limiter protects fairness and AI spend
        -> inference service performs allowed work
```

## Why Redis?

An in-memory limiter inside FastAPI would work with one process, but every additional process or replica would have an independent view of remaining capacity. Redis provides shared state and executes the decision script atomically, so concurrent gateway replicas cannot spend the same remaining tokens.

Redis is a practical default for this example because the limiter state is small, frequently updated, latency-sensitive, and temporary. It is not the only valid choice:

| Option | When it fits |
| --- | --- |
| **Redis** | Low-latency shared counters or buckets with atomic scripts and automatic expiry. |
| **DynamoDB** | Serverless AWS systems that prefer managed durability and can design around conditional updates and partition limits. |
| **PostgreSQL** | Lower-throughput systems that want to reuse an existing database and value transactional integration over minimum latency. |
| **Managed API gateway** | Teams that want common edge limits handled by their cloud or gateway provider with minimal custom infrastructure. |
| **Dedicated rate-limiting service** | Larger platforms that need centralized policy, multiple algorithms, consistent enforcement, and specialized operations. |

The detailed trade-offs are covered in `4_detailed_concepts.md`.

## Response behavior

Allowed responses include remaining request and token capacity:

```http
X-RateLimit-Request-Remaining: 4
X-RateLimit-Token-Remaining: 63
```

Rejected responses include the binding limit and an approximate delay:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 2
X-RateLimit-Limit-Type: tokens
```

The client should wait before retrying and add jitter when many workers may retry together.
