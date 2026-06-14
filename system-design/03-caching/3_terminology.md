# Caching Terminology

## Cache fundamentals

| Term | Meaning | AI example |
| --- | --- | --- |
| **Cache** | Storage layer that holds pre-computed results for fast retrieval. | Redis holding embedding vectors keyed on query hash. |
| **Cache hit** | The requested key is found in the cache. | Query hash matches; return stored embedding without calling the API. |
| **Cache miss** | The requested key is not found; compute and store the result. | Novel query; call the embedding API, then write to Redis. |
| **Hit rate** | Fraction of requests served from cache. | 80 % hit rate means 4 in 5 queries avoid the embedding API. |
| **Cache key** | Identifier used to look up a stored result. | `sha256(normalize(query))` |
| **Cache value** | The stored result. | JSON-serialized embedding vector. |
| **TTL (time-to-live)** | Duration after which a cache entry is automatically deleted. | 3600 s — entries expire after one hour. |
| **Eviction** | Removal of entries to free space when the cache is full. | LRU eviction drops the least recently used entry first. |
| **Cache warming** | Pre-populating the cache before live traffic arrives. | Load embeddings for the top-100 support queries at startup. |
| **Cache invalidation** | Deliberately removing or replacing a stale entry. | After a document update, purge cached embeddings that referenced it. |

## Similarity and semantic caching

| Term | Meaning |
| --- | --- |
| **Embedding** | A dense vector representing text in a high-dimensional semantic space. Similar meaning → nearby vectors. |
| **Cosine similarity** | Measures the angle between two vectors, ignoring magnitude. Range: −1 (opposite) to 1 (identical direction). |
| **Semantic cache** | A cache that matches queries by meaning rather than exact text. |
| **Similarity threshold** | Minimum cosine similarity score required to return a cached embedding. Controls the precision-recall trade-off. |
| **Approximate nearest neighbour (ANN)** | Algorithm that finds similar vectors quickly without exhaustive comparison. Used by vector databases at scale. |
| **Semantic promotion** | Writing an exact-match entry for a new query that received a semantic hit, so future identical requests are O(1). |

## Eviction policies

| Policy | Behaviour | When to use |
| --- | --- | --- |
| **LRU** (least recently used) | Evict the entry accessed least recently. | Most caches — keeps frequently used entries warm. |
| **LFU** (least frequently used) | Evict the entry accessed fewest times. | Workloads with a stable hot set and many one-off queries. |
| **TTL only** | Keep all entries until they expire; no size limit. | When the cache can hold all working-set entries comfortably. |
| **No eviction** | Reject writes when full; return error. | Useful if stale reads are unacceptable and the cache must be authoritative. |

## Cache topology

| Term | Meaning |
| --- | --- |
| **L1 cache** | Fast local cache inside a process or on the same host. Lower latency, not shared across replicas. |
| **L2 cache** | Shared remote cache (e.g., Redis). Slower than L1 but benefits all replicas. |
| **Write-through** | Write to cache and backend simultaneously. Cache is always consistent but writes are slower. |
| **Write-around** | Write only to backend; cache is populated on the next read miss. Avoids caching rarely re-read data. |
| **Write-back** | Write to cache first; flush to backend asynchronously. Lower write latency but risk of loss on crash. |
| **Cache-aside** | Application reads the cache first; on miss, reads from backend and populates the cache. Most common pattern. |

## AI-specific terms

| Term | Meaning |
| --- | --- |
| **Embedding cache** | Cache specifically for vector representations of text, images, or audio. |
| **Query normalisation** | Stripping whitespace, lowercasing, or otherwise standardising a query before hashing it. Increases exact-hit rate for minor variations. |
| **Semantic drift** | Accumulated error from returning a cached embedding that is close but not identical to what the current query deserves. |
| **Model-versioned cache** | Cache namespace scoped to an embedding model version to prevent returning embeddings from a deprecated model. |
| **Retrieval cache** | Cache for retrieved document chunks, not just embeddings. Useful when the retrieval step itself is expensive. |
| **Generation cache** | Cache for full LLM responses. Effective for highly repeated prompts; risky for personalised or time-sensitive answers. |
