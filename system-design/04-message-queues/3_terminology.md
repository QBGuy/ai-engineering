# Message Queue Terminology

## Queue fundamentals

| Term | Meaning | AI example |
| --- | --- | --- |
| **Message queue** | A buffer that holds work items (messages) between producers and consumers. | LLM inference jobs queued between the API and GPU workers. |
| **Producer** | A process that adds messages to the queue. | The FastAPI endpoint that receives job submissions. |
| **Consumer** | A process that reads and processes messages from the queue. | The worker that calls the LLM and writes results. |
| **Message** | The unit of work placed in the queue. | `{job_id, document, submitted_at}` |
| **Queue depth** | Number of messages currently waiting to be processed. | 47 pending summarisation jobs. |
| **Throughput** | Messages processed per unit time. | 12 jobs/minute with 3 workers. |
| **Backlog** | Queue depth that has grown beyond what workers can drain quickly. | Burst of 200 document uploads filling the ingestion queue. |

## Delivery guarantees

| Term | Meaning |
| --- | --- |
| **At-most-once** | Each message is delivered zero or one times. Fastest; messages can be lost on failure. |
| **At-least-once** | Each message is delivered one or more times. Requires idempotent consumers; duplicates are possible. |
| **Exactly-once** | Each message is processed exactly once. Hardest guarantee; requires transactional coordination between queue and consumer. |
| **Idempotent consumer** | A consumer that produces the same result when it processes the same message twice. Prerequisite for safe at-least-once delivery. |
| **Acknowledgement (ack)** | The consumer signals to the queue that a message has been successfully processed and can be removed. |
| **Negative acknowledgement (nack)** | The consumer signals failure; the queue may re-queue the message or route it to a DLQ. |
| **Visibility timeout** | Duration a message stays invisible to other consumers after being claimed. If not acked within this window, it becomes visible again for re-delivery. |

## Queue patterns

| Term | Meaning |
| --- | --- |
| **Point-to-point** | One producer, one consumer per message. Each job is processed by exactly one worker. Most AI task queues use this pattern. |
| **Publish/subscribe (pub/sub)** | One producer, multiple subscribers each receive a copy. Used to fan out events (e.g., "new document ingested" → embed queue + notification service). |
| **Fan-out** | A single message triggers work in multiple downstream systems via pub/sub. |
| **Consumer group** | A set of consumers that share consumption of a queue; each message is delivered to one member of the group. Enables horizontal scaling. |
| **Competing consumers** | Multiple worker replicas reading from the same queue; whichever claims a message processes it. |
| **Priority queue** | A queue where high-priority messages are delivered before lower-priority ones regardless of arrival order. |
| **Dead letter queue (DLQ)** | A secondary queue where messages are moved after exhausting retry attempts. Used for inspection, alerting, and manual replay. |

## Redis Stream terms

| Term | Meaning |
| --- | --- |
| **Stream** | A Redis data structure that acts as an append-only log of messages. Each entry has an auto-generated ID. |
| **Entry ID** | `<millisecond-timestamp>-<sequence>` — unique, monotonically increasing per stream. |
| **XADD** | Redis command to append a message to a stream. |
| **XREADGROUP** | Redis command to read messages from a stream as a consumer group member. Claimed messages are tracked as pending. |
| **XACK** | Redis command to acknowledge that a message has been processed. Removes it from the pending entries list. |
| **Pending entries list (PEL)** | Per-consumer-group list of messages that have been delivered but not yet acknowledged. Enables re-delivery after worker crash. |
| **XCLAIM / XAUTOCLAIM** | Commands to reassign pending messages from a crashed or slow consumer to another. |

## Async request patterns

| Term | Meaning |
| --- | --- |
| **202 Accepted** | HTTP status code meaning "the request has been received and queued, but not yet processed." The correct response for async job submission. |
| **Job ID** | Opaque identifier returned to the client at submission time; used to poll for the result. |
| **Polling** | Client repeatedly calls `GET /jobs/{id}` until the status is `done`. Simple but generates extra requests. |
| **Long polling** | The polling endpoint holds the connection open until the result is ready (up to a timeout). Reduces round trips versus short polling. |
| **Webhook / callback** | The server calls a client-provided URL when the job completes. Eliminates polling entirely but requires the client to expose an endpoint. |
| **Server-sent events (SSE)** | Server pushes progress updates to the client over a persistent HTTP connection. Used for streaming LLM token output. |

## Scaling and operations

| Term | Meaning |
| --- | --- |
| **Back-pressure** | Signal from a downstream system that it cannot accept more work; causes the upstream to slow down or reject new submissions. |
| **Load leveling** | Queues absorb burst traffic so workers process at a steady rate rather than spiking and crashing. |
| **Worker starvation** | A consumer that cannot claim messages because higher-priority consumers are always consuming first. |
| **Poison message** | A message that consistently causes consumers to fail. If not caught by a DLQ, it cycles through retries forever and blocks other work. |
| **Message TTL** | Time after which an unprocessed message is discarded or moved to a DLQ. Prevents stale work from being processed. |
| **KEDA** | Kubernetes Event-Driven Autoscaling — scales worker deployments based on queue depth rather than CPU/memory. |
| **Fan-in** | Multiple producers writing to a single queue; consumed by one or more workers. Common in AI pipelines where many upstream services generate work. |
