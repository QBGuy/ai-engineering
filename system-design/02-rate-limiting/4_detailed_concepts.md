# Detailed Rate-Limiting Concepts

## 1. Why request counting is insufficient

Two HTTP requests can create radically different amounts of work:

```text
Request A: 20 prompt tokens + 20 output tokens
Request B: 8,000 prompt tokens + 4,000 output tokens
```

An RPM-only policy charges both requests equally. This protects some gateway overhead but does not protect model spend or GPU time. The example therefore requires capacity in both a request bucket and a token bucket.

## 2. Token-bucket algorithm

A token bucket has a maximum capacity and continuously refills at a configured rate:

```text
refilled = min(capacity, previous_tokens + elapsed_seconds * refill_rate)
```

If `refilled >= request_cost`, the request is allowed and spends its cost. Otherwise, the request is rejected and the missing capacity determines the retry delay.

Token buckets allow short bursts up to their capacity while enforcing an average rate over time.

A request whose cost exceeds the bucket's maximum capacity can never succeed, regardless of how long it waits. Reject that as an invalid or unsupported request rather than returning a retryable rate-limit response.

## 3. Weighted AI requests

The local example estimates:

```text
estimated cost = ceil(prompt characters / 4) + max_tokens
```

This is useful for learning but not accurate enough for billing. Production options include:

- Use the exact tokenizer for the selected model.
- Reserve the worst-case output allowance before inference.
- Reconcile the reservation against actual provider-reported usage.
- Apply different weights for expensive models, tools, image inputs, or long context.
- Limit concurrent requests separately because token counts do not fully predict latency.

## 4. Atomic multi-bucket decisions

The gateway must not check and update the request and token buckets with separate client-side commands:

```text
gateway-1 reads remaining=1
gateway-2 reads remaining=1
gateway-1 allows and writes remaining=0
gateway-2 allows and writes remaining=0
```

Both gateways spent the same final unit. The Lua script executes inside Redis as one atomic operation. It calculates both buckets first and only commits either bucket when both have enough capacity.

## 5. What the Redis state looks like

Redis is an in-memory key-value store that runs as a separate server process. Think of it as a shared dictionary that all gateway replicas can read and write simultaneously. Because it lives in RAM rather than on disk, reads and writes are very fast — typically under a millisecond.

The gateway connects to it via a URL (`redis://localhost:6379/0`). In production, Redis runs on a dedicated host or managed service; locally it is usually a Docker container.

Redis does not store one row for every accepted request in this example. It stores the **current state of each enforced bucket**.

```text
rate_limit:tenant:tenant-dev:requests
rate_limit:tenant:tenant-dev:tokens
```

Each key is a small Redis hash:

```text
rate_limit:tenant:tenant-dev:requests
  tokens      = 4
  updated_at  = 1780985031.42

rate_limit:tenant:tenant-dev:tokens
  tokens      = 72
  updated_at  = 1780985031.42
```

`tokens` means remaining bucket capacity at the last update, not historical model-token usage. `updated_at` allows the next request to calculate how much capacity has refilled since then.

Redis has several data types (string, list, set, sorted set, hash). A hash stores multiple named fields under one key, similar to a small row in a table. Using a hash here groups `tokens` and `updated_at` together so both fields can be read in a single `HMGET` call rather than two separate lookups.

Each bucket hash is approximately 200–300 bytes. Ten thousand tenants with two buckets each is around 3–5 MB — negligible. Redis itself idles at roughly 5–10 MB of process overhead. Inactive keys also auto-delete via TTL, so tenants who stop sending requests do not accumulate state indefinitely.

You can inspect the local example with:

```bash
docker compose exec redis redis-cli KEYS 'rate_limit:*'
docker compose exec redis redis-cli HGETALL rate_limit:tenant:tenant-dev:requests
docker compose exec redis redis-cli HGETALL rate_limit:tenant:tenant-dev:tokens
docker compose exec redis redis-cli TTL rate_limit:tenant:tenant-dev:tokens
```

`KEYS` is acceptable for this tiny learning environment but should not be used for broad scans in a busy production Redis deployment. Use `SCAN` or known-key lookups instead.

## 6. Buckets and policy dimensions

A bucket exists at the lowest granularity that the policy explicitly enforces. In the local example, that granularity is:

```text
tenant + limit unit
```

Therefore, each tenant receives two independent buckets:

```text
tenant-dev
  request bucket
  token bucket

tenant-team
  request bucket
  token bucket
```

An allowed request estimated to cost 300 model tokens consumes both:

```text
request bucket: -1
token bucket:   -300
```

It is rejected if either bucket lacks capacity.

The key can include additional dimensions when the policy requires them:

```text
rate_limit:user:user-42:requests
rate_limit:tenant:tenant-dev:model:gpt-x:tokens
rate_limit:tenant:tenant-dev:endpoint:embeddings:requests
rate_limit:provider:provider-account-7:model:gpt-x:tokens
```

Common policy scopes include:

- Per user.
- Per tenant or organization.
- Per API key, although stable internal identities are preferable.
- Per model.
- Per endpoint or route class.
- Provider-wide or system-wide.

These are separate enforcement buckets, not aggregations calculated from one lowest-grain counter. For example, enforcing both a per-user token limit and a tenant-wide token limit requires checking and updating both buckets atomically.

```text
Request from user-42 in tenant-dev
  -> spend 300 from user-42 token bucket
  -> spend 300 from tenant-dev token bucket
  -> spend 300 from provider-wide token bucket
  -> allow only if every required bucket has capacity
```

For a hierarchy such as user, group, and organization, Redis may therefore hold one bucket per metric at every enforced level:

```text
rate_limit:user:user-42:requests
rate_limit:user:user-42:tokens
rate_limit:group:group-7:requests
rate_limit:group:group-7:tokens
rate_limit:organisation:org-3:requests
rate_limit:organisation:org-3:tokens
```

If a request costs one request unit and 300 token units, the limiter checks all six buckets. When every bucket has sufficient capacity, one atomic operation deducts:

```text
user-42 request bucket: -1
group-7 request bucket: -1
org-3 request bucket:   -1

user-42 token bucket: -300
group-7 token bucket: -300
org-3 token bucket:   -300
```

If any required bucket lacks capacity, the request is rejected and none of the buckets should be deducted. This prevents partial charging when, for example, the user still has capacity but the organization-wide allowance is exhausted.

Every extra dimension combination creates more keys and more checks. Only create buckets for limits the system genuinely enforces.

The hierarchy mapping itself (which user belongs to which team, which team belongs to which organisation) does not live in Redis. Redis only stores the bucket state. The application layer — Python code, a database, or a config file — defines the membership and decides which bucket keys to include in each atomic check. Redis is dumb storage; the structure is in your application.

## 7. Lazy replenishment

Token buckets usually do not require a background process that updates every Redis record each second or minute. Updating millions of inactive buckets continuously would waste resources.

Instead, the limiter replenishes a bucket lazily when a request next uses it:

```text
available =
  min(
    capacity,
    stored_tokens + elapsed_seconds * refill_rate
  )
```

For example:

```text
stored tokens:     72
elapsed time:      10 seconds
refill rate:       20 tokens/second
maximum capacity: 100

available = min(100, 72 + 10 * 20)
available = 100
```

The limiter then checks the request cost, deducts it if allowed, and stores the new remaining capacity and current timestamp:

```text
tokens      = available - request_cost
updated_at  = current Redis server time
```

In the local example, the Lua script uses Redis's server-side `TIME` command. This gives concurrent gateway replicas a shared clock source and avoids relying on every application server having exactly synchronized clocks.

## 8. Current state versus historical aggregation

The bucket hashes answer a synchronous question:

> May this request proceed right now?

They do not naturally answer historical questions such as:

- How many tokens did each tenant consume yesterday?
- Which model generated the most rejected requests?
- What should be invoiced this month?

For history, emit one durable usage or decision event per request:

```json
{
  "request_id": "req-123",
  "tenant_id": "tenant-dev",
  "user_id": "user-42",
  "model": "gpt-x",
  "endpoint": "chat",
  "allowed": true,
  "estimated_tokens": 300,
  "actual_tokens": 241,
  "timestamp": "2026-06-09T06:03:51Z"
}
```

Store those events in PostgreSQL, ClickHouse, Kafka plus an analytical store, or a data warehouse. Aggregate that durable event history across user, tenant, model, endpoint, provider, and time dimensions.

Redis Streams can retain events for short-lived processing pipelines, but the expiring limiter buckets should not be treated as an audit log or billing source of truth.

## 9. Expiration

Inactive buckets should expire so Redis does not retain state forever. The script sets a TTL long enough for an empty bucket to refill, with additional margin.

Expiration is a storage-management mechanism, not a quota reset. Bucket capacity still governs the maximum burst after the key expires.

## 10. Storage options and trade-offs

### Redis

**Pros**

- Very low latency for frequently updated operational state.
- Atomic Lua scripts make multi-bucket decisions possible in one operation.
- Native TTLs clean up inactive buckets.
- Widely understood and available as a managed service.

**Cons**

- Adds infrastructure and operational cost.
- Redis availability becomes part of the request path.
- Persistence and replication settings require deliberate configuration.
- Excellent for current limiter state, but poor as the authoritative usage-history or billing store.

**Cost and licensing**

- Redis can be downloaded and self-hosted without paying Redis Ltd., including the `redis:7.4-alpine` Docker image used by this example.
- Self-hosting still costs compute, memory, backups, monitoring, maintenance, and engineering time.
- Redis 7.4 is source-available under RSALv2 or SSPLv1. Redis 8 and later additionally offer the OSI-approved AGPLv3 option. Review the selected license before distributing modifications or offering Redis functionality as a service.
- Redis Cloud is a paid managed service with a small free Essentials plan intended for learning and test projects. Managed production capacity costs money.

### DynamoDB

**Pros**

- Managed, durable, and horizontally scalable on AWS.
- Conditional writes and transactions can implement atomic counter policies.
- TTL support can remove inactive limiter records.
- No Redis cluster to operate.

**Cons**

- Higher and less predictable per-request latency than an in-memory datastore.
- Hot partition keys can throttle heavily used global or provider-wide buckets.
- Weighted token-bucket calculations and multi-item decisions are more cumbersome.
- Cost scales with every limiter read and write.

### PostgreSQL

**Pros**

- Strong transactions and durable state.
- Can combine policy state with existing tenant, plan, and quota data.
- Reuses infrastructure many applications already operate.
- Suitable for lower-rate limits and longer-period quotas.

**Cons**

- Row locking and frequent updates can become bottlenecks at high request rates.
- Adds load to a database usually needed for more durable business data.
- Expiring large numbers of temporary bucket rows requires cleanup.
- Usually slower than Redis for synchronous high-throughput enforcement.

### Managed API gateway

**Pros**

- Minimal custom infrastructure and common edge limits available out of the box.
- Integrates with authentication, routing, logging, and cloud operations.
- Provider handles availability and scaling.

**Cons**

- Built-in policies may focus on requests rather than AI-specific weighted token cost.
- Tenant, model, and provider-wide multi-bucket policies may be limited.
- Behavior, pricing, and portability are provider-specific.
- Durable usage reconciliation still needs a separate system.

### Dedicated rate-limiting service

Examples include a self-hosted service or managed product whose only responsibility is centralized policy enforcement.

**Pros**

- Centralizes policy across many gateways, services, languages, and regions.
- Can support multiple algorithms, hierarchical limits, policy configuration, and observability.
- Keeps complex limiter logic out of each application.

**Cons**

- Adds another network hop and critical service to operate.
- Requires a clear failure policy when the service is unavailable.
- More complex than needed for small systems.
- May still use Redis or another datastore internally.

Choose based on required latency, throughput, consistency, durability, existing infrastructure, and how sophisticated the policy dimensions must be.

## 11. Pre-inference reservation and reconciliation

Rate limiting must happen before expensive work, but actual output-token usage is known only afterward. Charging `max_tokens` before inference prevents overspend but may be conservative.

A more complete flow is:

```text
1. Estimate and reserve maximum cost.
2. Perform inference.
3. Read actual input/output usage.
4. Refund unused reservation or record the difference.
5. Write durable usage to the billing ledger.
```

Reconciliation must be idempotent so retries do not refund or charge usage twice.

## 12. Limits versus queues

A rate limiter rejects work that exceeds policy. A queue accepts work for later processing. They solve different problems:

- Use a limiter when the caller must stay within a fairness, capacity, or cost boundary.
- Use a queue when delayed execution is acceptable and the system has bounded backlog capacity.
- Use both when submission itself must be limited and accepted jobs should drain asynchronously.

## 13. Interview checklist

When designing an AI rate limiter, explain:

1. Which resource is being protected.
2. The limit key and unit.
3. The algorithm and why bursts are or are not allowed.
4. Where shared state lives and how decisions remain atomic.
5. How variable-cost requests are weighted.
6. What the client receives after rejection.
7. Whether failure is fail open, fail closed, or locally degraded.
8. How short-window enforcement relates to durable usage accounting.
