# Detailed Caching Concepts

## 1. Why embedding generation is the most cacheable layer

A RAG pipeline has several expensive steps:

```text
1. Embed query               ← repeated, identical results for identical input
2. ANN search (retrieval)    ← depends on the embedding
3. Fetch retrieved chunks     ← depends on retrieval result
4. Generate LLM response     ← depends on chunks + system prompt
```

The embedding function is a pure function: the same input always produces the same output, and it has no side effects. This makes it an ideal cache target. Unlike LLM generation (which may be personalised, time-sensitive, or probabilistic), embedding vectors are stable and reusable across users and sessions.

## 2. Exact-match caching

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

## 3. Semantic caching

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

## 4. Semantic cache as a linear scan

The local example stores all cached embeddings in Redis and scans them in Python. The number of comparisons equals the number of cached entries:

```text
n = 10        →    negligible
n = 1,000     →    ~1 ms in Python
n = 100,000   →    ~100 ms  ← starting to hurt
n = 10,000,000 →   ~100 s   ← completely unusable
```

For small caches (< 1,000 entries) the linear scan is practical in a learning context. For production, replace the scan with an approximate-nearest-neighbour (ANN) index provided by a vector database (see `2_architecture_scaled.md`).

## 5. Semantic promotion

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

## 6. What the Redis state looks like

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

## 7. TTL strategy

A TTL that is too short wastes cache capacity — entries expire before they accumulate enough hits to justify their storage cost. A TTL that is too long risks returning embeddings for queries whose meaning has shifted (e.g., a product name that changed).

Common approaches:

| Approach | Mechanism | When to use |
| --- | --- | --- |
| **Fixed TTL** | All entries expire after a set duration. | Most use cases. Choose 1–24 hours for support queries. |
| **Sliding TTL** | Reset TTL on each access. | When frequently accessed entries should stay warm indefinitely. |
| **Model-version TTL** | Expire all entries when a new embedding model is deployed. | Prevents stale vectors from a superseded model version. |
| **Event-driven invalidation** | Purge specific entries when underlying data changes. | Document corpus updates that affect retrieval quality. |

## 8. Cache aside pattern

This module uses the cache-aside pattern (also called lazy population):

```text
1. Check cache.
2. On hit: return cached value.
3. On miss: compute value, write to cache, return value.
```

The alternative is write-through, where every write to the backend also updates the cache. For embedding caches, write-through does not apply — the backend is a third-party API, not a database that the application writes to.

## 9. Failure modes

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

## 10. What caching does not solve

- **Personalisation:** cached embeddings are reused across users. If the query or embedding should vary by user context, caching the raw embedding is incorrect.
- **Time-sensitive queries:** "What happened today?" cannot safely return a cached embedding from last week.
- **Long-tail queries:** if every query is unique, the cache miss rate will be close to 100 % and caching adds overhead without benefit. Measure the hit rate before investing in caching infrastructure.
- **LLM response caching:** caching generated text requires higher confidence that the cached response is still appropriate. The risks of stale or incorrect responses are higher than for embedding vectors.

## 11. Interview checklist

When designing a caching layer for a RAG system, explain:

1. Which layer is being cached and why (embedding generation: pure function, expensive, stable).
2. The cache key design and normalisation strategy.
3. Exact-match vs. semantic-match and the threshold choice.
4. The data structure used in the cache and why (string for exact, hash for semantic).
5. TTL strategy and cache invalidation triggers.
6. Failure behaviour when the cache is unavailable.
7. How you would scale the semantic lookup beyond a linear scan.
8. How you measure whether the cache is helping (hit rate, latency reduction, API cost saved).
