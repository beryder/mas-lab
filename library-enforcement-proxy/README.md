# Native MasLab integration

This experimental package authorizes delegation and MCP requests through MasLab's governance API and authorizes A2A replies through its delivery-policy extension. The Go adapter retains CFN communication and audit ownership. Proposed activities go to SCOPE through OTLP before the adapter receives their references. There is no local policy engine or automatic retry.

## Compatibility and installation

Use Python 3.11 or newer and MasLab 0.2.0 with the native A2A delivery-policy extension and configurable A2A webserver binding. The integration checks those capabilities at startup. It does not patch installed packages. This library depends on the native release-policy change in [#242](https://github.com/ioc-project/mas-lab/pull/242), including its final `on_release` callback, [webserver transport validation](https://github.com/ioc-project/mas-lab/pull/245), and [resolved operation connections](https://github.com/ioc-project/mas-lab/pull/258). It is not compatible with earlier 0.2.0 builds. Install reviewed MasLab wheels first, then `pip install ./library-enforcement-proxy`. For execution-wide telemetry also install `mas-lab-library-telemetry[convert]` from the same reviewed source.

## Configuration

The native library entry point registers `proxy_requests`, `proxy_release`, and `proxy_authenticated_http`. Attach request governance through a Control document:

```yaml
apiVersion: control/v1
kind: Control
metadata: {name: authorization}
spec:
  agents: planner
  governance:
    - proxy_requests:
        config_file: ./client.json
        agent: planner
```

Attach that Control through `Flavour.spec.control`. Configure the A2A Application endpoint separately:

```yaml
a2a:
  delivery_policy:
    type: proxy_release
    params: {config_file: /protected/client.json, agent: planner}
  delivery_timeout: 30
  webserver:
    type: proxy_authenticated_http
    params: {config_file: /protected/client.json}
```

`client.json` contains a registered `mas_id`, HTTPS `adapter_url`, `otlp_url`, `ca_file`, and `identities` keyed by local aliases. Each producing agent needs its canonical `id`, `certificate_file`, `key_file`, and exact `tools` mapping. A mapping selects `destination`, `kind` (`delegation` or `tool`) and, for MCP, the exact `tool` name. Relative credential paths resolve against the configuration file. Connection credentials never go into activity evidence.

Each producing identity also requires protected `bindings`. A binding ties a native `ref` to a registered destination and the exact selected endpoint, stdio invocation, or authenticated recipient. For example, an HTTP MCP connection can declare:

```json
{
  "ref": "mcp:records",
  "destination": "records",
  "protocol": "mcp",
  "transport": "http",
  "endpoint": "https://records.example/mcp"
}
```

Put entries in the identity's `bindings` array. Stdio MCP entries use `command` (the command and arguments as one array) and a resolved absolute `cwd` instead of `endpoint`. Native A2A routes use `ref: a2a:<peer>` and their SDK-selected endpoint. Immediate replies and task retrieval use `ref: a2a:caller` with an authenticated `principal`. Push uses `ref: a2a:push` with the exact callback endpoint. A `(ref, destination)` pair must be unique.

A2A delegation and release support HTTP only. MCP calls support HTTP and stdio. The integration compares the native connection with this protected mapping before publishing evidence or submitting authorization. An opaque binding ID alone never establishes a destination. Evidence includes only the protocol, carrier, binding reference, identity and generation; it omits target selectors and credentials. The reference-only authorization request declares the native carrier separately from its own HTTPS/mTLS connection.

Native dispatch uses the same prepared operation that supplied the metadata. Route/provider replacement invalidates pending operations, even when the carrier stays unchanged. An already admitted call can finish against its original target. No code re-resolves an authorized operation to a new destination. MCP initialization and discovery can start a subprocess before authorization, but a denied `tools/call` does not execute the tool.

The example shows how to bind an existing adapter configuration. Its standard ASGI authentication plugin resolves bearer credentials from each identity's `a2a_credential_env`. Credentials must be distinct and at least 32 characters. AgentCards remain publicly discoverable. Protocol methods require authentication. Production deployments may use their own native webserver authentication plugin. Anonymous response recipients are denied. Push callbacks additionally require an exact protected `push_destinations` URL-to-recipient mapping.

Enable native MasLab `otel` observability separately, with `realtime: true`, `extensions: true`, `governance: true`, the registered MAS ID and `OTEL_EXPORTER_OTLP_ENDPOINT`. The integration publishes only proposed-activity and authorization correlation evidence. Native telemetry retains execution events independently of whether a gate was reached. Concurrent export requires the native telemetry ownership fix proposed in #236. Complete assembled prompts require #235. These are separate telemetry improvements and do not change authorization policy.

## Failure and lifecycle behavior

The default authorization deadline is 20 seconds and covers publication, mediation and receipt validation. Missing evidence, invalid receipts, deadline expiry, cancellation and closed integrations block release. The adapter authenticates each source with mTLS and admits only configured routes. ALLOW applies to the frozen candidate and recipient. Denials never rewrite responses or replay writes. Blocking a reply does not undo an operation already executed.

Each operation owns its HTTP clients. Native exposure cleanup closes delivery policies. Embedders close request policies when shutting down their runtimes. In-process adoption requires trusted runtime code and configuration. It provides no external isolation guarantee.

The compatible reference-based adapter belongs to enforcement-proxy. Its proposed MasLab `stdio` contract extension requires maintainer, CF and CE agreement before merge or adoption. Local contract fixtures do not establish compatibility with deployed consumers. Full CFN/SCOPE deployment is separate from this library. See [the minimal configuration example](examples/README.md). Unit tests use the native runtime and delivery controller. Live example results are deterministic transport/enforcement evidence, not semantic-evaluation results.

## Optional release notifications

`ReleasePolicy.subscribe(callback)` receives the native controller's final `DeliveryOutcome`. Only an allowed outcome includes response content. `unsubscribe(callback)` and policy shutdown remove listeners. Notifications are separate from raw execution telemetry, and callback failures cannot authorize or alter a response. Consumers choose their own presentation and duplicate handling.

Authenticated serving is HTTP-only. Native exposure rejects both `a2a.grpc_port` and `a2a.grpc_url` before constructing AgentCards, clients or listeners. The package requires native webserver selection and transport validation in addition to the delivery-policy extension. Authentication middleware protects HTTP requests only and does not provide gRPC authentication.
