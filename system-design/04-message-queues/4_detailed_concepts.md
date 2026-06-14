# Detailed Message Queue Concepts

## 1. Why queues matter more for AI than for typical web services

Standard web requests complete in milliseconds. LLM inference takes 1–30 seconds, document ingestion 5–60 seconds, and batch embedding jobs hours. This changes the architecture requirements fundamentally:

```text
Fast request (< 200 ms):   synchronous is fine — hold the connection
Slow request (1–30 s):     queue it — connection would timeout at load balancers
Very slow (> 30 s):        must be async — no HTTP client will wait
Batch (hours):             offline — submit once, retrieve results later
```

Anthropic's batch API, OpenAI's batch API, and Gemini's batch inference all expose this pattern at the provider level. Learning to build it locally prepares you to design the same pattern in your own systems.

## 2. At-least-once delivery and idempotency

Most production queues guarantee at-least-once delivery — every message is delivered one or more times. The "or more" part matters:

```text
Scenarios that cause re-delivery:
- Worker crashes after claiming the message but before acknowledging
- Network failure between worker and queue during ack
- Visibility timeout expires before a slow job completes
- Manual replay from a DLQ
```

Re-delivery means your worker may process the same job twice. Two strategies for handling this:

**Idempotent writes** — the result store write is a no-op if the job_id already exists:

```python
# Write only if not already done
result = redis.hsetnx(f"jobs:{job_id}", "result", summary)
if not result:
    pass  # already written by a previous attempt — safe to ignore
```

**Check-before-process** — skip processing if the result already exists:

```python
if redis.hexists(f"jobs:{job_id}", "result"):
    redis.xack(STREAM, GROUP, message_id)
    return  # already processed — just ack and move on
```

Both approaches require a stable job_id in the message that is consistent across re-deliveries. The producer must generate the ID before enqueuing and not regenerate it on retry.

## 3. The visibility timeout

A visibility timeout is the window a consumer has to process and acknowledge a message before it becomes visible again for re-delivery. This is the mechanism that makes at-least-once delivery reliable:

```text
Worker A claims message → visibility timer starts (e.g., 30 s)
  → Worker A processes in 20 s → XACK → message gone
  
Worker B claims message → visibility timer starts
  → Worker B crashes at 15 s → timer expires at 30 s
  → Message reappears → Worker C claims and completes it
```

Set the visibility timeout to longer than your p99 job processing time. If a typical LLM job takes 5 s and p99 is 25 s, set the timeout to 60 s. Too short causes spurious re-deliveries; too long delays recovery after crashes.

In Redis Streams, the visibility equivalent is the pending entries list (PEL). Use `XAUTOCLAIM` on a timer to reclaim messages that have been pending longer than the expected processing time.

## 4. Back-pressure

Back-pressure is what happens when the queue grows faster than workers can drain it. Without it, the queue grows without bound until memory is exhausted.

Strategies:

| Strategy | Mechanism | Trade-off |
| --- | --- | --- |
| **Reject at submission** | Return 429 when queue depth exceeds a threshold | Callers must handle rejection; provides clear signal |
| **Slow down producers** | Block or delay submissions when the queue is full | Transparent to callers; risks holding HTTP connections |
| **Auto-scale workers** | Add more workers when queue depth rises | Handles bursts automatically; requires provisioning headroom |
| **Prioritise and shed** | Drop low-priority jobs when at capacity; preserve high-priority | Complex; requires explicit job priority |

For AI backends, rejecting at submission (429) is usually the right default — it gives callers a clear signal to retry later rather than silently accepting work that will take minutes to process.

## 5. Dead letter queues

A poison message is one that consistently causes worker failures. Without a DLQ, it cycles:

```text
Enqueue → worker fails → re-deliver → worker fails → re-deliver → ...
```

This consumes worker capacity and may block legitimate work. A DLQ stops the cycle after a fixed number of retries (typically 3–5):

```text
Attempt 1 → fail
Attempt 2 → fail
Attempt 3 → fail
→ move to DLQ

Worker processes other messages normally.
DLQ message waits for human inspection.
```

The DLQ itself should trigger an alert. DLQ depth > 0 means work is being lost, and the root cause needs investigation before replaying.

Common causes of poison messages in AI systems:
- Malformed documents that crash the parser
- Documents exceeding the LLM context window
- Unhandled edge cases in worker code
- LLM API responses in unexpected formats

## 6. Queue vs stream

The terms are often used interchangeably, but they imply different access patterns:

| Property | Queue (SQS, RabbitMQ) | Stream (Kafka, Redis Streams) |
| --- | --- | --- |
| **Retention** | Message deleted after ack | Message retained in log (configurable window) |
| **Replay** | No — consumed messages are gone | Yes — rewind and re-consume from any offset |
| **Consumer tracking** | Queue tracks what's delivered | Consumer tracks its own offset |
| **Fan-out** | Requires SNS/exchange binding | Native — multiple consumer groups each get all messages |
| **Ordering** | FIFO (SQS FIFO) or best-effort | Ordered within a partition |
| **Fit for task queues** | Yes — SQS is the standard choice | Yes — Kafka or Redis Streams also work |
| **Fit for event log** | No — events are lost after consumption | Yes — designed for this |

For AI task queues (submit job, process once, discard), either model works. For AI event pipelines (model served a prediction → downstream systems need to react), a stream's replay and fan-out capabilities are valuable.

## 7. Async result retrieval patterns

After submitting a job, clients need the result. Three patterns, in increasing complexity:

**Short polling** — client calls `GET /jobs/{id}` repeatedly on a timer:

```text
Submit → 202
Poll 1 s later → 202 pending
Poll 2 s later → 202 pending
Poll 4 s later → 200 done
```

Simple to implement. Generates unnecessary requests when jobs are slow. Adequate for low-traffic internal tools.

**Long polling** — server holds the connection until the result is ready (up to a timeout):

```text
Submit → 202
GET /jobs/{id}?timeout=30s → server blocks → result ready → 200 done
```

Fewer round trips. Requires the server to hold threads or use async I/O. Use when you want to reduce polling overhead without building webhook infrastructure.

**Webhook callback** — client provides a URL at submission time; server calls it when done:

```text
POST /jobs {document, callback_url: "https://client.example.com/results"}
→ 202 job_id

[job completes]

POST https://client.example.com/results {job_id, result}
```

Zero polling. Requires the client to expose a publicly reachable endpoint and handle delivery retries. The right choice for server-to-server AI pipeline integrations.

## 8. Ordering guarantees

Most AI task queues do not require strict ordering. Each document can be processed independently. But some workloads do:

```text
Ordered: process document chunks in sequence so references to "previous section" resolve correctly
Ordered: apply edits to a user's draft in the order they were made
Unordered: embed 10,000 documents in parallel — order of completion does not matter
```

FIFO queues (SQS FIFO, Redis Streams with a single consumer) preserve order within a partition. Adding more consumer partitions restores parallelism at the cost of cross-partition ordering — a common trade-off.

## 9. What the Redis state looks like

The local example maintains three kinds of Redis structures:

**Stream entries** — pending jobs:

```text
Stream key: jobs:pending
Entry: {
  id: "1780985031420-0",
  job_id: "a3f8b2c1-...",
  document: "The transformer architecture..."
}
```

**Pending entries list** — jobs claimed but not yet acknowledged:

```text
Group: workers
Consumer: worker-1
Pending: ["1780985031420-0"]  → delivered 14 s ago
```

**Result hashes** — completed jobs:

```text
Key: jobs:a3f8b2c1-...
Fields:
  status:      "done"
  result:      "Summary: The transformer architecture..."
  submitted_at: "1780985031.42"
  completed_at: "1780985033.89"
```

Inspect directly:

```bash
docker compose exec redis redis-cli XLEN jobs:pending
docker compose exec redis redis-cli XINFO GROUPS jobs:pending
docker compose exec redis redis-cli XPENDING jobs:pending workers - + 10
docker compose exec redis redis-cli HGETALL jobs:<job_id>
```

## 10. Retry and backoff in workers

When the LLM API returns an error, the worker should not immediately re-enqueue — it should retry with backoff before giving up:

```python
for attempt in range(max_retries):
    try:
        result = llm_api.summarise(document)
        break
    except RateLimitError:
        time.sleep(2 ** attempt + random.uniform(0, 1))
    except FatalError:
        # Don't retry — write failure result and ack
        write_failure(job_id, error)
        return
else:
    # Exhausted retries — nack; queue will route to DLQ
    nack(message_id)
    return

write_result(job_id, result)
xack(message_id)
```

Distinguish retriable errors (429, 503) from fatal errors (invalid input, context window exceeded). Retrying a fatal error wastes retries and delays DLQ routing.

## 11. Failure modes

### Worker crashes mid-job

Message remains in the pending entries list. After the visibility timeout, it becomes reclaimable. Another worker picks it up. If the result was already written (from a previous attempt), the idempotency check skips re-processing and just acknowledges.

### Queue service unavailable

Job submissions fail. The API should return 503 with a `Retry-After` header. Do not buffer submissions in the API process — in-memory buffering loses data on API restart.

### Result store unavailable

Workers complete processing but cannot write results. Do not acknowledge the message until the write succeeds. The message will be re-delivered and re-processed after recovery.

### Queue grows unbounded

Queue depth exceeds memory. Managed queues (SQS, Pub/Sub) have very large or unlimited capacity; Redis Streams need a `MAXLEN` argument to trim old entries and cap memory:

```text
XADD jobs:pending MAXLEN ~ 100000 * {fields}
```

The `~` makes trimming approximate (faster). For task queues, trimming is safe — processed messages are already acknowledged and their results are in the result store.

## 12. Interview checklist

When asked "how would you handle slow LLM inference at scale?", the strongest answers identify the async queue pattern first, then address the details:

1. **Identify the bottleneck.** LLM inference takes 1–30 s. HTTP connections cannot wait that long at scale. The fix is to return a job ID immediately and process asynchronously.
2. **Describe the async flow.** POST returns 202 + job_id. Client polls `GET /jobs/{id}` until `status: done`. Alternatively, callback URL for server-to-server integrations.
3. **Choose a queue.** Redis Streams for simplicity if Redis is already in the stack. SQS on AWS, Pub/Sub on GCP for managed durability and auto-scaling.
4. **Explain delivery guarantees.** At-least-once is standard. Requires idempotent workers keyed on job_id.
5. **Handle the visibility timeout.** Set longer than p99 processing time. Use XAUTOCLAIM or equivalent to recover messages from crashed workers.
6. **Design the DLQ.** Move messages after 3–5 failed attempts. Alert on DLQ depth > 0. Replay after fixing the root cause.
7. **Scale workers.** Scale on queue depth, not CPU. KEDA for Kubernetes. Provision GPU workers proactively — they take minutes to add.
8. **Separate queues by job type.** Inference (high priority, GPU) and embedding/ingestion (lower priority, CPU) should not share a queue — slow jobs block fast ones.
9. **Describe back-pressure.** Return 429 when the queue depth exceeds a threshold. Callers should back off and retry rather than hammering a full queue.
10. **Name your success metric.** End-to-end job latency (p50, p99), queue depth trend, DLQ size, and worker utilisation are the four numbers that matter.
