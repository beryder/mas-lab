# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
import asyncio
import json
import ssl
import uuid
from types import SimpleNamespace

import httpx
import pytest
from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.types import Message, Part, Task, TaskState, TaskStatus
from library_ioa.plugins.a2a.delivery import DeliveryController
from mas.library.enforcement_proxy.bridge import AuthorizationUnavailable, Bridge, canonical
from mas.library.enforcement_proxy.plugins import ReleasePolicy, RequestPolicy
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.engine.simulated import SimulatedEngine
from mas.runtime.kernel.config import KernelConfig


class _Identity(User):
    is_authenticated = True
    user_name = "worker"


@pytest.fixture
def config(tmp_path, monkeypatch):
    tls = SimpleNamespace(load_cert_chain=lambda *a: None)
    monkeypatch.setattr(ssl, "create_default_context", lambda **kw: tls)
    data = {
        "mas_id": "mas",
        "adapter_url": "https://adapter.test",
        "otlp_url": "https://collector.test/v1/traces",
        "ca_file": "ca.pem",
        "timeout": 1,
        "identities": {
            "planner": {
                "id": "id-planner",
                "certificate_file": "cert.pem",
                "key_file": "key.pem",
                "tools": {"write": {"destination": "worker", "kind": "tool", "tool": "write"}},
            },
            "worker": {"id": "id-worker"},
        },
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return str(path)


def wire(monkeypatch, *, allow=True, missing=False, malformed=False, wait=None):
    calls = []

    async def transport(request):
        body = json.loads(request.content)
        calls.append((request.url.host, body))
        if request.url.host == "collector.test":
            if missing:
                return httpx.Response(200, json={"partialSuccess": {"rejectedSpans": "1"}})
            return httpx.Response(200, json={})
        if wait:
            await wait.wait()
        return httpx.Response(
            200,
            json={
                "evaluation_id": "wrong" if malformed else body["evaluation_id"],
                "decision": "allow" if allow else "deny",
                "authorization_record_id": str(uuid.uuid4()),
            },
        )

    monkeypatch.setattr(Bridge, "client", lambda self: httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [True, False])
async def test_publish_before_reference_only_authorization(config, monkeypatch, allowed):
    calls = wire(monkeypatch, allow=allowed)
    result = await Bridge(config, "planner").authorize(
        destination="worker",
        kind="tool",
        tool="write",
        payload=canonical({"arguments": {"value": "candidate-text"}}),
        context={"session_id": "session"},
    )
    assert result.allowed is allowed
    assert [host for host, body in calls] == ["collector.test", "adapter.test", "collector.test"]
    assert "candidate-text" in json.dumps(calls[0][1])
    assert "candidate-text" not in json.dumps(calls[1][1])
    span = calls[0][1]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert (calls[1][1]["trace_id"], calls[1][1]["span_id"]) == (span["traceId"], span["spanId"])
    assert result.recipient == "worker"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "malformed", "cancel", "timeout", "closed"])
async def test_no_release_on_failure(config, monkeypatch, mode):
    wait = asyncio.Event() if mode in {"cancel", "timeout"} else None
    calls = wire(monkeypatch, missing=mode == "missing", malformed=mode == "malformed", wait=wait)
    bridge = Bridge(config, "planner")
    bridge.timeout = 0.01 if mode == "timeout" else 1
    if mode == "closed":
        bridge.close()
    pending = asyncio.create_task(bridge.authorize(destination="worker", kind="delegation", payload=b"{}", context={}))
    if mode == "cancel":
        await asyncio.sleep(0)
        pending.cancel()
    with pytest.raises((AuthorizationUnavailable, asyncio.CancelledError)):
        await pending
    if mode == "missing":
        assert all(host != "adapter.test" for host, body in calls)


@pytest.mark.parametrize("allowed", [True, False])
def test_native_governance_prevents_write_execution(config, monkeypatch, allowed):
    calls = wire(monkeypatch, allow=allowed)
    policy = RequestPolicy(config, "planner")

    class Engine(SimulatedEngine):
        executions = 0

        def invoke(self, io):
            if io.op == "TOOL_CALL":
                self.executions += 1
            return super().invoke(io)

    engine = Engine(
        llm_next_step=lambda cid: "TOOL_CALL" if cid == 1 else "STOP",
        llm_tool_intent=lambda cid: ("write", {"value": "kept"}),
        stop_text="done",
    )
    instance = RuntimeInstance.from_parts(engine=engine, config=KernelConfig(egress_governance_plugin=policy))
    instance.driver.agent_id = "planner"
    instance.capture_session_baseline()
    trace = instance.run_user_text("save a value")
    assert engine.executions == int(allowed)
    assert any(host == "adapter.test" for host, body in calls)
    assert trace.client_responses


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["send", "get_task", "push"])
async def test_release_binds_recipient_and_exact_content(config, monkeypatch, kind):
    calls = wire(monkeypatch)
    policy = ReleasePolicy(config, "planner")
    policy.bridge.identity["push_destinations"] = {"https://worker.test/callback": "worker"}
    controller = DeliveryController("planner", policy)

    class Identity(User):
        is_authenticated = True
        user_name = "worker"

    identity = Identity()
    task = Task(
        id="task",
        context_id="conversation",
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        history=[Message(message_id="reply", role=2, parts=[Part(text="exact response")])],
    )
    released = await controller.release(
        task,
        kind=kind,
        context=ServerCallContext(user=identity),
        destination="https://worker.test/callback" if kind == "push" else "",
    )
    assert released == task
    assert calls[1][1]["destination"] == "worker"
    await controller.aclose()


@pytest.mark.asyncio
async def test_unauthenticated_release_is_denied_before_publication(config, monkeypatch):
    from a2a.utils.errors import InvalidAgentResponseError

    calls = wire(monkeypatch)
    controller = DeliveryController("planner", ReleasePolicy(config, "planner"))
    with pytest.raises(InvalidAgentResponseError):
        await controller.release(Task(id="task"), kind="get_task", context=ServerCallContext())
    assert not calls
    await controller.aclose()


@pytest.mark.parametrize("patch", [{"timeout": 0}, {"adapter_url": "http://insecure.test"}])
def test_invalid_configuration_fails_before_connecting(config, patch):
    from pathlib import Path

    path = Path(config)
    path.write_text(json.dumps(json.loads(path.read_text()) | patch))
    with pytest.raises(ValueError):
        Bridge(config, "planner")


@pytest.mark.asyncio
async def test_unknown_destination_and_evidence_limit_fail_before_transport(config, monkeypatch):
    calls = wire(monkeypatch)
    bridge = Bridge(config, "planner")
    with pytest.raises(AuthorizationUnavailable, match="destination"):
        await bridge.authorize(destination="unknown", kind="tool", payload=b"{}", context={})
    bridge.config["max_evidence_bytes"] = 1
    with pytest.raises(AuthorizationUnavailable, match="bound"):
        await bridge.authorize(destination="worker", kind="tool", payload=b"{}", context={})
    assert not calls


def test_requests_without_active_context_or_route_do_not_execute(config, monkeypatch):
    from mas.runtime.kernel.coupling import GovDecision

    calls = wire(monkeypatch)
    policy = RequestPolicy(config, "planner")
    intent = SimpleNamespace(op="TOOL_CALL", tool_name="unknown", correlation_id=1, tool_arguments={})
    assert policy.evaluate_egress(intent, config=None)[0] is GovDecision.BLOCK
    policy.on_transition(
        SimpleNamespace(
            agent_id="planner",
            kind="USER_INPUT_RECEIVED",
            session_id="session",
            task_id="task",
            attributes={"text": "request"},
        )
    )
    assert policy.evaluate_egress(intent, config=None)[0] is GovDecision.BLOCK
    with pytest.raises(AuthorizationUnavailable):
        policy.on_transition(SimpleNamespace(agent_id="unexpected"))
    policy.close()
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [True, False])
async def test_notifications_follow_final_delivery_decision(config, monkeypatch, allowed):
    from a2a.utils.errors import InvalidAgentResponseError

    wire(monkeypatch, allow=allowed)
    policy = ReleasePolicy(config, "planner")
    observed = []
    policy.subscribe(observed.append)
    policy.subscribe(observed.append)

    def broken(outcome):
        raise RuntimeError("observer failure")

    policy.subscribe(broken)
    controller = DeliveryController("planner", policy)
    user = _Identity()
    if allowed:
        await controller.release(Task(id="task"), kind="send", context=ServerCallContext(user=user))
    else:
        with pytest.raises(InvalidAgentResponseError):
            await controller.release(Task(id="task"), kind="send", context=ServerCallContext(user=user))
    assert len(observed) == 1
    assert (observed[0].payload is not None) == allowed
    policy.unsubscribe(observed.append)
    policy.unsubscribe(broken)
    await controller.aclose()


@pytest.mark.asyncio
async def test_publication_failure_and_wrong_producer_cannot_release(config, monkeypatch):
    from a2a.utils.errors import InvalidAgentResponseError

    calls = wire(monkeypatch, missing=True)
    policy = ReleasePolicy(config, "planner")
    user = _Identity()
    for name in ("unexpected", "planner"):
        controller = DeliveryController(name, policy)
        with pytest.raises(InvalidAgentResponseError):
            await controller.release(Task(id="task"), kind="send", context=ServerCallContext(user=user))
    assert len(calls) == 1
    await controller.aclose()


def test_example_mcp_write_and_read(tmp_path):
    import sys
    from pathlib import Path

    from library_ioa.plugins.mcp.provider import MCPToolProvider

    example = Path(__file__).resolve().parents[1] / "examples/records.py"
    provider = MCPToolProvider.from_provider_spec(
        {
            "name": "records",
            "kind": "mcp",
            "command": sys.executable,
            "args": [str(example), str(tmp_path / "record.json")],
        }
    )
    assert {tool["name"] for tool in provider.list_tools()} == {"records_read", "records_write"}
    provider.call_tool("records_write", {"value": "saved value"})
    assert "saved value" in str(provider.call_tool("records_read", {}))
