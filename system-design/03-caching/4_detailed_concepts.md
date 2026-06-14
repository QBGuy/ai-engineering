# Detailed Caching Concepts

## 1. The caching hierarchy for RAG pipelines

A RAG pipeline has several expensive steps, each of which can be cached independently:

```text
Step                    Cost       Pure function?   Cache target
──────────────────────────────────────────────────────────────────────
1. Embed query          Low        Yes              Query embedding cache
2. ANN search           Medium     Yes (given emb)  Retrieval cache
3. Fetch chunks         Low        Yes (given hits) Retrieval cache
4. Generate response    High       Roughly yes*     Full response cache
```

*LLM generation is not strictly pure — it is probabilistic and may be personalised — but for deterministic or FAQ-style workloads, the same question reliably produces the same useful answer.

**Embedding caching is the easiest layer to cache** (pure function, small output, low staleness risk) but it also saves the least. Embedding APIs are cheap — OpenAI `text-embedding-3-small` costs $0.02 per million tokens, and a typical query is under 50 tokens. The per-call savings are fractions of a cent and 50–500 ms of latency.

**Full response caching is the highest-value target.** Skipping retrieval and generation saves 5–30 seconds and the full LLM token cost. GPTCache, the most widely used open-source library for this (8 k GitHub stars), caches LLM responses — not embeddings — using the same two-level exact/semantic mechanism this module teaches. The embedding is used as the lookup key; the cached value is the complete answer.

**The pattern is the same at every layer.** Whether you are caching an embedding, a chunk list, or a full answer, the mechanics are identical: normalize the key, check exact hash, check semantic similarity, fall through to compute. Learning this module prepares you to apply it where the ROI is larger.

## 2. Provider-level prefix caching

Before building any caching infrastructure, check what your model provider already handles for free.

**Anthropic prompt caching** caches KV attention states server-side for prompt prefixes you mark with `cache_control`. A cached prefix costs 10 % of the normal input price to read and 25 % to write. For a RAG system that prepends a large system prompt or document block to every request, this alone cuts token costs by 50–90 %.

```python
# Mark the expensive prefix — Anthropic caches it for 5 minutes
{
    "role": "user",
    "content": [
        {
            "type": "text",
            "text": large_system_prompt_or_retrieved_docs,
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": user_query},
    ],
}
```

**OpenAI implicit caching** caches repeated prompt prefixes automatically with no configuration. As of late 2024, prompt prefixes over 1,024 tokens that are reused within a short window are served at 50 % discount with no API changes required.

These are not the same as semantic caching — they do not match similar queries — but they address the most common repeated-work pattern (large shared context) with no infrastructure and no correctness risk.

**When provider caching is not enough:**
- Queries are identical or near-identical at the application level (FAQ bot with thousands of users asking the same question)
- You want to skip inference entirely, not just the context processing
- You are self-hosting a model and provider caching is not available

## 3. When embedding caching is worth building

Embedding caching has a positive ROI in a narrow set of conditions:

| Condition | Why it matters |
| --- | --- |
| **High query repetition** | Cache hit rate is low for diverse queries; the infrastructure overhead exceeds the savings |
| **High request volume** | Small per-request savings only add up at scale (thousands of requests per hour) |
| **Expensive embedding model** | Cost and latency vary — self-hosted large models or third-party APIs with per-token pricing |
| **Downstream steps are fast** | If retrieval and generation are slow, caching the embedding makes little difference to end-user latency |

**Workloads where it pays:**
- Public-facing support bots (same password-reset question from thousands of users)
- Search autocomplete (same prefix typed repeatedly)
- Re-indexing pipelines (don't re-embed unchanged documents)
- High-throughput internal tools with a small, stable query vocabulary

**Workloads where it probably does not pay:**
- Internal enterprise RAG over a diverse document corpus (query repetition is low)
- Personalised assistants where query context varies by user
- Low-volume deployments where infrastructure cost exceeds API savings
- Any workload where full response caching already covers the common cases

Before building: measure your actual query repetition rate. If fewer than 10 % of queries repeat within your TTL window, the embedding cache will have a low hit rate and may not justify the operational cost of Redis and a vector database.

## 4. Exact-match caching

Exact matching normalises the query before hashing it:

```text
"How to reset my password"  →  "how to reset my password"  →  sha256(...)  →  Redis GET
"how to reset my password"  →  "how to reset my password"  →  sha256(...)  →  same key → HIT
```

Normalisation handles case variation. Stripping leading and trailing whitespace handles copy-paste artifacts. More aggressive normalisation (removing punctuation, expanding contractions) increases the hit rate but risks matching strings that should be considered distinct.

The cache value is the JSON-serialized embedding vector. Redis stores it as a plain string with a TTL:

```text
SET cache:embedding:exact:{sha256} '[0.71, 0.03, ...]' EX 3600
```

Lookup is O(1) — a single Redis GET command. This adds roughly 1 ms of latency for a local Redis instance.

## 5. Semantic caching

Semantic caching handles natural-language variation. Two strings are semantically similar when they express the same intent:

```text
"how to reset my password"        similarity 0.98 →  HIT
"steps to reset password"         similarity 0.97 →  HIT
"I forgot my password"            similarity 0.96 →  HIT
"how to update my billing info"   similarity 0.41 →  MISS
```

The mechanism:

1. Compute the query embedding (or retrieve it from a lightweight local model).
2. Compare it against all stored embeddings using cosine similarity.
3. If the best match exceeds the threshold, return the stored embedding.

### Cosine similarity

```text
similarity(A, B) = (A · B) / (|A| × |B|)
```

Cosine similarity measures the angle between two vectors and ignores their magnitude. Two embeddings pointing in the same direction in semantic space have a similarity close to 1, regardless of absolute vector length.

For unit-normalised embeddings (length 1), cosine similarity equals the dot product, which reduces the computation to a simple sum of products.

### The threshold trade-off

| Threshold | Hit condition | Risk |
| --- | --- | --- |
| 0.99 | Near-identical phrasing | Few semantic hits; not much improvement over exact matching |
| 0.97 | Paraphrases of the same intent | Good coverage; occasional edge-case semantic drift |
| 0.90 | Broadly related queries | High hit rate but real risk of wrong embeddings for different intents |

Choose a threshold that matches your acceptable error rate. Validate it on a labelled evaluation set before deploying to production.

## 6. Semantic cache as a linear scan

The local example stores all cached embeddings in Redis and scans them in Python. The number of comparisons equals the number of cached entries:

```text
n = 10        →    negligible
n = 1,000     →    ~1 ms in Python
n = 100,000   →    ~100 ms  ← starting to hurt
n = 10,000,000 →   ~100 s   ← completely unusable
```

For small caches (< 1,000 entries) the linear scan is practical in a learning context. For production, replace the scan with an approximate-nearest-neighbour (ANN) index provided by a vector database (see `2_architecture_scaled.md`).

## 7. Semantic promotion

When a semantic hit is found, the gateway writes an exact-match entry for the new query string:

```text
User first sends:  "steps to reset password"
  → exact MISS
  → semantic HIT on "how to reset my password" (similarity 0.98)
  → write: SET cache:embedding:exact:{sha256("steps to reset password")} [embedding] EX 3600
  → return cached embedding

User sends same string again:  "steps to reset password"
  → exact HIT  (O(1), no semantic scan)
```

This promotion means that each unique phrasing only triggers the O(n) semantic scan once. Subsequent identical messages are resolved immediately from the exact cache.

## 8. What the Redis state looks like

The local example maintains three kinds of entries:

### Exact cache entries

```text
cache:embedding:exact:{sha256}  →  "[0.71, 0.03, 0.0, ...]"  (string, with TTL)
```

One entry per unique normalised query string. Expiry is set individually per entry.

### Semantic cache entries

```text
cache:embedding:semantic:{id}  →  hash
    query:      "how to reset my password"
    embedding:  "[0.71, 0.03, 0.0, ...]"
    created_at: "1780985031.42"
```

A Redis hash groups the query text and embedding under one key. The `id` is the first 16 hex characters of the query SHA-256 — compact but effectively unique at typical cache sizes.

### Semantic index

```text
cache:semantic:index  →  set { "a3f8b2c1...", "9d4e7a2f...", ... }
```

A Redis set of all semantic entry IDs. Used to enumerate entries during the similarity scan. When an entry's TTL expires, its ID remains in the set until the next scan discovers the key no longer exists.

### Hit/miss counters

```text
stats:hits:exact      →  integer
stats:hits:semantic   →  integer
stats:misses          →  integer
```

Simple counters incremented with `INCR`. The `/cache/stats` endpoint reads all three.

Inspect the state directly:

```bash
docker compose exec redis redis-cli KEYS 'cache:*'
docker compose exec redis redis-cli SMEMBERS cache:semantic:index
docker compose exec redis redis-cli HGETALL cache:embedding:semantic:<id>
docker compose exec redis redis-cli TTL cache:embedding:exact:<sha256>
```

## 9. TTL strategy

A TTL that is too short wastes cache capacity — entries expire before they accumulate enough hits to justify their storage cost. A TTL that is too long risks returning embeddings for queries whose meaning has shifted (e.g., a product name that changed).

Common approaches:

| Approach | Mechanism | When to use |
| --- | --- | --- |
| **Fixed TTL** | All entries expire after a set duration. | Most use cases. Choose 1–24 hours for support queries. |
| **Sliding TTL** | Reset TTL on each access. | When frequently accessed entries should stay warm indefinitely. |
| **Model-version TTL** | Expire all entries when a new embedding model is deployed. | Prevents stale vectors from a superseded model version. |
| **Event-driven invalidation** | Purge specific entries when underlying data changes. | Document corpus updates that affect retrieval quality. |

## 10. Cache aside pattern

This module uses the cache-aside pattern (also called lazy population):

```text
1. Check cache.
2. On hit: return cached value.
3. On miss: compute value, write to cache, return value.
```

The alternative is write-through, where every write to the backend also updates the cache. For embedding caches, write-through does not apply — the backend is a third-party API, not a database that the application writes to.

## 11. Failure modes

### Redis unavailable

If Redis is down and the gateway fails on cache lookups, it can:

- **Fail open:** skip cache, pass all requests to the embedding API. Latency rises and cost spikes but the service stays available.
- **Fail closed:** return a 503. Appropriate if the embedding API cannot absorb the full load alone.
- **Circuit breaker:** detect Redis failure, open a circuit breaker, route directly to the embedding API for a bounded period, then retry Redis.

The local example raises a 503 if Redis is unreachable. For production, implement a fallback to the embedding API so a cache outage does not take down the RAG pipeline entirely.

### Embedding API unavailable

Cache hits are unaffected. Cache misses fail. Apply:

- Retry with exponential backoff for transient errors.
- A circuit breaker to stop hammering a failing API.
- A fallback to a different embedding provider if available.

### Stale entries

If the document corpus is updated and existing cache entries reference outdated semantic representations, downstream retrieval quality degrades silently. The safest mitigation is a short TTL combined with monitoring on retrieval quality metrics.

## 12. What caching does not solve

- **Personalisation:** cached embeddings are reused across users. If the query or embedding should vary by user context, caching the raw embedding is incorrect.
- **Time-sensitive queries:** "What happened today?" cannot safely return a cached embedding from last week.
- **Long-tail queries:** if every query is unique, the cache miss rate will be close to 100 % and caching adds overhead without benefit. Measure the hit rate before investing in caching infrastructure.
- **LLM response caching:** caching generated text requires higher confidence that the cached response is still appropriate. The risks of stale or incorrect responses are higher than for embedding vectors.

## 13. Interview checklist

When asked "how would you add caching to a RAG system?", the strongest answers start by choosing the right layer — not by immediately describing a Redis implementation.

1. **Identify the highest-value layer first.** Is the answer the same for every user? Cache the full LLM response. Does the system prompt repeat? Use provider prefix caching. Is the corpus stable with repeated queries? Cache retrieved chunks. Only then consider embedding caching.
2. **Check what the provider already handles.** Anthropic prompt caching and OpenAI implicit caching cover a large fraction of repeated-work scenarios for free.
3. **Measure query repetition before building.** A cache with a 5 % hit rate costs more in infrastructure and complexity than it saves.
4. **Explain the two-level cache mechanism.** Exact hash (O(1)) as the primary path; semantic ANN search (O(log n) with a vector DB) as the secondary path for near-duplicate queries.
5. **Describe the cache key and normalisation.** `sha256(strip(lower(query)))` for exact; the embedding vector itself as the ANN query for semantic.
6. **State your TTL strategy and invalidation triggers.** Fixed TTL for most cases; event-driven invalidation when the document corpus updates; model-versioned namespaces when switching embedding models.
7. **Address correctness risk.** Semantic caching can return a cached answer that is close-but-wrong for the new query. Describe the threshold choice and how you validate it.
8. **Describe failure behaviour.** Fail open (route to origin on cache miss) is usually correct — a cache outage should degrade performance, not take down the service.
9. **Name your success metric.** Hit rate, latency reduction at p50/p99, and API cost saved per day are the three numbers that matter.
