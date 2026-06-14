# NGINX and FastAPI Gateway Architecture

The gateway is split into two layers:

- **NGINX edge proxy:** the only public service. It handles connections, coarse protection, forwarding headers, and load balancing.
- **FastAPI application gateway:** a private service. It handles API authentication, JSON validation, application policy, routing, and response shaping.

This split keeps cheap transport-level work out of Python while preserving normal application code for policies that need data models, databases, or business context.

Here, **edge** means the outer boundary or layer where external traffic enters infrastructure serving your system. NGINX is the local edge proxy because it is the first reachable component, not because "edge" is a special type of server.

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
        "activationBorderColor": "#60a5fa",
        "noteBkgColor": "#111827",
        "noteBorderColor": "#334155",
        "noteTextColor": "#e5edf7"
    },
    "sequence": {
        "diagramMarginX": 24,
        "diagramMarginY": 16,
        "actorMargin": 48,
        "width": 170,
        "height": 54,
        "messageMargin": 16
    }
}}%%
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
        nginx("<b>NGINX</b><br/>localhost:8080")
    end

    subgraph private[" "]
        gateway("<b>FastAPI gateway</b><br/>gateway:8080")
        embed("<b>Embeddings service</b><br/>embeddings-service:8001")
        infer("<b>Inference service</b><br/>inference-service:8002")
    end

    client -->|HTTP| nginx
    nginx -->|proxy_pass| gateway
    gateway --> embed
    gateway --> infer

    classDef defaultNode fill:#182235,stroke:#64748b,stroke-width:1.5px,color:#e5edf7
    classDef edgeNode fill:#132340,stroke:#60a5fa,stroke-width:1.75px,color:#dbeafe
    classDef focusNode fill:#2563eb,stroke:#93c5fd,stroke-width:2px,color:#ffffff

    class client defaultNode
    class nginx edgeNode
    class gateway focusNode
    class embed,infer defaultNode

    style public fill:#0f1d35,stroke:#315a91,stroke-width:1.5px
    style private fill:#111827,stroke:#334155,stroke-width:1.5px

    linkStyle 0,1 stroke:#60a5fa,stroke-width:2.5px,color:#bfdbfe
    linkStyle 2,3 stroke:#64748b,stroke-width:1.5px,color:#cbd5e1
```

Only NGINX publishes a host port. The FastAPI gateway and backend services are reachable only inside the Docker network.

## Responsibility boundary

| Component | Owns |
| --- | --- |
| **NGINX edge** | Public listener, TLS, connection handling, per-IP rate limit, load balancing |
| **FastAPI gateway** | API key auth, JSON validation, routing, response normalization, billing policy |

NGINX rejects clearly invalid or excessive traffic before it reaches Python. FastAPI handles anything that requires understanding the caller or request body.

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
