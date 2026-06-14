# Caching Architecture

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
    participant G as Gateway
    participant R as Redis
    participant V as Vector DB
    participant E as Embedding API

    C->>G: POST /v1/embeddings {query}
    G->>R: GET exact:{sha256(query)}
    alt exact hit
        R-->>G: cached embedding
        G-->>C: 200 {cache: "hit", cache_type: "exact", latency_ms: ~1}
    else exact miss
        R-->>G: nil
        G->>E: POST /embed {query}
        E-->>G: embedding vector (500 ms)
        G->>V: ANN search (cosine similarity)
        alt similarity ≥ threshold
            V-->>G: matched embedding
            G->>R: write exact key (promote)
            G-->>C: 200 {cache: "hit", cache_type: "semantic", similarity: 0.98}
        else similarity < threshold
            G->>R: SET exact key
            G->>V: upsert embedding
            G-->>C: 200 {cache: "miss", latency_ms: ~510}
        end
    end
```

Redis handles exact lookups (O(1) key-value). The vector DB handles semantic search — Redis cannot compute similarity over stored vectors without the RediSearch module.

## Why two cache levels?

A single exact-match cache handles repeated identical queries. For a support chatbot or search interface, many users ask the same intent with different words. Semantic matching extends coverage to near-duplicates without another API call.

```text
User A: "how to reset my password"           → MISS → API call → stored
User B: "steps to reset password"            → exact MISS → semantic HIT (0.98)
User C: "forgot my password, what do I do?"  → exact MISS → semantic HIT (0.97)
User D: "how do I cancel my subscription?"   → exact MISS → semantic MISS → API call
```

## Semantic cache promotion

On a semantic hit, the gateway also writes an exact key for the new query string. The next identical request from that user resolves in O(1) without touching the vector DB.

```text
First:  exact MISS → semantic HIT → write exact key
Second: exact HIT
```

## Cache key design

```text
Exact key:  exact:{sha256(strip(lower(query)))}
Vector DB:  embedding stored with query text as metadata
```

## What the response looks like

Exact hit:

```json
{ "cache": "hit", "cache_type": "exact", "latency_ms": 1.2 }
```

Semantic hit:

```json
{ "cache": "hit", "cache_type": "semantic", "similarity": 0.9812, "matched_query": "how to reset my password", "latency_ms": 8.4 }
```

Miss:

```json
{ "cache": "miss", "latency_ms": 513.7 }
```
