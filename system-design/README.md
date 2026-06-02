# System Design for AI Engineers

This folder is a dynamic learning workspace for seven production system-design concepts used in AI engineering.

We will work through the concepts one at a time. Each numbered folder will eventually contain the explanation, architecture diagrams, runnable code, deployment notes, and tradeoff discussions for that concept.

## Learning path

1. `01-api-gateway/` — single entry point for client traffic, authentication, routing, validation, and policy enforcement.
2. `02-rate-limiting/` — protects model APIs from abuse, runaway cost, and overload.
3. `03-caching/` — avoids repeated expensive work such as embedding generation or repeated LLM calls.
4. `04-message-queues/` — decouples async AI workloads such as document processing, batch summarization, and eval jobs.
5. `05-circuit-breakers/` — prevents cascading failures when a downstream service such as a vector database or model provider degrades.
6. `06-load-balancing/` — distributes traffic across backend services or inference nodes.
7. `07-auto-scaling/` — adds and removes compute based on demand, latency, queue depth, or GPU-aware metrics.

## How we will use this

For each concept, we can add materials incrementally:

- `README.md` — conceptual explanation and request flow.
- `architecture.md` — system diagrams and deployment topology.
- `docker-compose.yml` — local runnable stack when useful.
- `src/` or `app/` — Python or JavaScript implementation.
- `deploy/` — deployment notes for open-source or free-tier-friendly infrastructure.

Start with `01-api-gateway/` when you are ready.
