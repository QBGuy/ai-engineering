# API Gateway Terminology

This guide groups the main concepts by the question they answer. The same component can appear in multiple groups because placement, behavior, and responsibility are different dimensions.

## 1. System Boundaries and Placement

These terms describe **where something sits** relative to users and the rest of the system.

| Term | Meaning | In this example |
| --- | --- | --- |
| **Edge** | The outer boundary or layer where external traffic enters infrastructure serving your system. It can contain several components. | Locally, NGINX is the edge. In the scaled design, the WAF and NGINX are both part of the edge path. |
| **Public / internet-facing** | Reachable by clients outside the private application network. | `localhost:8080` represents the public listener locally. |
| **Private / internal** | Reachable only from trusted infrastructure or a private network. | FastAPI, embeddings, and inference services. |
| **Ingress** | Traffic entering a system or network. It can also refer to the component controlling that traffic. | Client requests entering through NGINX. |
| **Egress** | Traffic leaving a system or network. | A private service calling an external model provider. |
| **Upstream** | A destination to which a proxy forwards requests. | FastAPI is NGINX's upstream. |
| **Downstream** | A service called by the current component. | Embeddings is downstream of FastAPI. |

The terms **upstream** and **downstream** are relative. FastAPI is downstream from NGINX, but embeddings is downstream from FastAPI.

## 2. Gateway and Proxy Roles

These terms describe **what a traffic-handling component does**.

| Term | Meaning | In this example |
| --- | --- | --- |
| **Reverse proxy** | Receives requests on behalf of servers and forwards them to those servers. Clients know the proxy address, not the private backend address. | NGINX proxies to FastAPI. |
| **Forward proxy** | Sends outbound requests on behalf of clients. Servers see the proxy rather than the original client. | Not used here. |
| **Edge proxy** | A reverse proxy positioned in the edge layer, often behind DNS, CDN, DDoS protection, or a WAF. | NGINX. |
| **API gateway** | A public API entry point that applies API-specific routing, authentication, policy, and response behavior. | The combined NGINX and FastAPI gateway path. |
| **Application gateway** | Gateway code that understands application concepts such as tenants, models, and JSON schemas. | FastAPI. |
| **Managed API gateway** | A cloud service that provides gateway capabilities without operating the proxy software yourself. | Azure API Management. |
| **Ingress controller / Gateway API implementation** | Kubernetes components that configure incoming traffic routing to services. | Possible production replacement for standalone NGINX. |

In casual discussion, people may call NGINX "the gateway." More precisely in this repository, NGINX is the **edge reverse proxy**, FastAPI is the **application gateway**, and together they form the API gateway architecture.

## 3. Connections and Transport

These concepts concern **moving bytes reliably and efficiently**.

| Term | Meaning | Why it matters |
| --- | --- | --- |
| **Connection** | A communication channel between two endpoints, usually TCP for HTTP/1.1 and HTTP/2. | Every API request needs a connection somewhere in its path. |
| **Listener** | A process waiting for connections on an IP address and port. | NGINX listens on port `8080`. |
| **Port** | A numbered network endpoint on a machine or container. | NGINX uses `8080`; embeddings uses `8001`. |
| **TLS termination** | Decrypting HTTPS traffic at a proxy before forwarding it internally. | Usually handled by NGINX or a cloud edge service in production. |
| **Keep-alive** | Reusing an existing connection for multiple requests. | Reduces repeated connection setup cost. |
| **Connection pool** | A reusable collection of open downstream connections. | FastAPI's shared `httpx.AsyncClient` pools service connections. |
| **Timeout** | Maximum time allowed for connection, sending, or receiving work. | Prevents stalled downstream services from holding resources forever. |
| **Buffering** | Temporarily storing request or response data before forwarding it. | Disabled for streamed model responses so tokens reach clients promptly. |
| **Streaming** | Sending a response incrementally instead of waiting for the whole result. | Common for LLM token output. |

## 4. Addressing and Reachability

These concepts answer **how one component finds and reaches another**.

| Term | Meaning | In this example |
| --- | --- | --- |
| **DNS** | Maps a hostname to an IP address. | Docker DNS resolves `gateway` and `embeddings-service`. |
| **Hostname** | Human-readable name used to address a machine or service. | `embeddings-service`. |
| **Service discovery** | Mechanism for finding available service instances. | Docker Compose service names provide basic discovery. |
| **Published port** | Maps a container port onto the host so host clients can reach it. | `8080:8080` publishes NGINX. |
| **Exposed port** | Documents an intended container port without publishing it to the host. Containers on the same Docker network can communicate independently of `expose`. | Embeddings exposes `8001`. |
| **Private network** | Network reachable only by participating internal components. | The Docker Compose network. |

`localhost` always means "this machine or container." Inside the gateway container, `localhost:8001` means port `8001` inside the gateway container, not the embeddings container.

## 5. HTTP Requests, Responses, and Headers

These concepts describe **the messages moving through the gateway**.

| Term | Meaning | Example |
| --- | --- | --- |
| **HTTP method** | The requested operation. | `GET`, `POST`, `DELETE`. |
| **Path / route** | The API location being requested. | `/v1/embeddings`. |
| **Request body** | Data sent by the client. | `{"input": "hello"}`. |
| **Response body** | Data returned to the client. | The embedding response JSON. |
| **Header** | Request or response metadata separate from the body. | `X-API-Key`, `Content-Type`. |
| **Status code** | Numeric outcome of the request. | `200`, `401`, `413`, `422`, `502`. |
| **Forwarding headers** | Headers describing the original client request after a proxy forwards it. | `X-Forwarded-For`, `X-Forwarded-Proto`. |
| **Request ID / correlation ID** | Identifier propagated through components so logs for one request can be joined. | `X-Request-ID: worked-example-123`. |

Common status codes in this example:

| Code | Meaning | Rejected by |
| --- | --- | --- |
| `200 OK` | Request succeeded. | N/A |
| `401 Unauthorized` | Missing or invalid credentials. | FastAPI |
| `413 Content Too Large` | Request body exceeds the edge limit. | NGINX |
| `422 Unprocessable Content` | JSON shape or field constraints are invalid. | FastAPI |
| `502 Bad Gateway` | A downstream service failed or was unavailable. | FastAPI gateway |
| `504 Gateway Timeout` | A downstream service took too long. | FastAPI gateway |

## 6. Protection and Policy

These concepts answer **whether a request should be accepted**.

| Term | Meaning | Typical layer |
| --- | --- | --- |
| **WAF** | Web Application Firewall that detects common web attacks using managed or custom rules. | Before NGINX in the scaled architecture. |
| **Rate limit** | Restricts how quickly requests may arrive. | NGINX for coarse per-IP limits; Redis-backed policy for tenant limits. |
| **Quota** | Restricts total usage over a longer period. | Application gateway or billing system. |
| **Authentication** | Determines who the caller is. | FastAPI validates the API key. |
| **Authorization** | Determines what an authenticated caller may do. | FastAPI checks model or route permissions. |
| **Validation** | Checks whether request data has the expected shape and constraints. | Pydantic in FastAPI. |
| **Body-size limit** | Rejects requests larger than an allowed size. | NGINX. |
| **Policy enforcement point** | Component where a rule is evaluated and enforced. | NGINX and FastAPI enforce different policies. |

Coarse policies depend only on transport information such as IP address or body size. Application policies depend on context such as tenant, model, plan, or token budget.

## 7. Routing and Load Balancing

These concepts answer **where an accepted request should go**.

| Term | Meaning | In this example |
| --- | --- | --- |
| **Routing** | Selecting a destination based on path, host, headers, or application logic. | FastAPI routes embeddings and chat requests to different services. |
| **Load balancing** | Distributing requests across multiple equivalent service instances. | NGINX can distribute traffic across FastAPI replicas. |
| **Upstream pool** | Group of equivalent backend instances available to a proxy. | NGINX's `fastapi_gateway` upstream. |
| **Least connections** | Sends new work to the instance with the fewest active connections. | Configured by NGINX with `least_conn`. |
| **Health check** | Test used to decide whether a service instance can receive traffic. | Compose waits for the FastAPI health endpoint. |
| **Failover** | Sending work elsewhere when one destination is unavailable. | Possible when multiple replicas exist. |
| **Single point of failure** | One component whose failure can make the whole path unavailable. | One NGINX instance would be a single point of failure in production. |

Routing selects a **type of service**. Load balancing selects an **instance of that service**.

## 8. Scaling and State

These concepts answer **how the system handles more traffic**.

| Term | Meaning | Example |
| --- | --- | --- |
| **Vertical scaling** | Giving one instance more CPU, memory, or other resources. | A larger gateway VM. |
| **Horizontal scaling** | Running more equivalent instances. | Three FastAPI gateway replicas. |
| **Replica** | One instance of a service among several equivalent instances. | `gateway-1`, `gateway-2`, `gateway-3`. |
| **Stateless service** | Does not require local memory from previous requests to handle the next request. | FastAPI gateways should be stateless. |
| **Shared state** | Data all replicas must access consistently. | API keys in Postgres and rate-limit counters in Redis. |
| **Autoscaling** | Automatically adding or removing replicas based on metrics. | Scale gateways based on latency or in-flight requests. |
| **Bottleneck** | Component whose capacity limits the whole request path. | Often model inference rather than the gateway. |

## 9. Reliability and Observability

These concepts answer **how failures are controlled and understood**.

| Term | Meaning | Example |
| --- | --- | --- |
| **Health endpoint** | Lightweight route showing whether a service is running. | `GET /health`. |
| **Logs** | Records of discrete events and requests. | NGINX access logs and FastAPI logs. |
| **Metrics** | Numeric measurements over time. | Request rate, latency, error count. |
| **Trace** | End-to-end record of one request across components. | NGINX -> FastAPI -> embeddings. |
| **Observability** | Ability to understand system behavior using logs, metrics, and traces. | Centralized monitoring in the scaled architecture. |
| **Retry** | Repeating a failed operation. | Must be used carefully for expensive or non-idempotent requests. |
| **Circuit breaker** | Stops calling a failing dependency temporarily. | Protects the gateway when a model provider is degraded. |
| **Idempotency** | Property that repeating an operation has the same effect as doing it once. | Important before automatically retrying requests. |

## A Structured Way to Analyze a Request

When reading an architecture, ask these questions in order:

1. **Boundary:** Where does external traffic first enter?
2. **Reachability:** Which components are public and which are private?
3. **Connection:** Who terminates TLS and manages client connections?
4. **Protection:** Which layer rejects abuse, oversized bodies, or invalid callers?
5. **Identity:** Where are authentication and authorization performed?
6. **Routing:** Which service type and service instance receive the request?
7. **State:** What data must be shared across replicas?
8. **Failure:** What happens when a downstream service is slow or unavailable?
9. **Observation:** How can one request be followed through logs, metrics, and traces?

For the local example, the short answer is:

```text
Client
  -> NGINX edge reverse proxy
     -> FastAPI application gateway
        -> private application service
```
