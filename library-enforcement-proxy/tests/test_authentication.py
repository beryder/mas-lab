# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Authenticated identity reaches the native policy over real ASGI routes."""

import json

import pytest
from fastapi.testclient import TestClient
from library_ioa.plugins.a2a.delivery import DeliveryController, DeliveryDecision
from library_ioa.plugins.a2a.server.app import build_app
from library_ioa.plugins.a2a.server.executor import MasLabAgentExecutor
from mas.library.enforcement_proxy.webserver import AuthenticatedWebServer


def test_authentication_precedes_agent_execution_and_release(tmp_path, monkeypatch):
    config = tmp_path / "client.json"
    config.write_text(json.dumps({"identities": {"caller": {"a2a_credential_env": "TEST_CALLER"}}}))
    monkeypatch.setenv("TEST_CALLER", "a" * 32)
    calls, recipients = [], []

    class Policy:
        async def authorize(self, candidate):
            recipients.append(candidate.recipient_id)
            return DeliveryDecision.ALLOW

        async def aclose(self):
            pass

    def handler(*args, **kwargs):
        calls.append(args)
        return {"text": "approved"}

    app = build_app(
        {
            "name": "worker",
            "description": "Test",
            "url": "http://testserver",
            "version": "1.0",
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [],
            "capabilities": {},
        },
        MasLabAgentExecutor(handler),
        delivery=DeliveryController("worker", Policy()),
    )
    AuthenticatedWebServer(str(config))._app(app)
    request = {
        "jsonrpc": "2.0",
        "id": "test",
        "method": "SendMessage",
        "params": {"message": {"messageId": "input", "role": "ROLE_USER", "parts": [{"text": "hello"}]}},
    }
    with TestClient(app) as client:
        assert client.get("/.well-known/agent-card.json").status_code == 200
        assert client.post("/", json=request, headers={"A2A-Version": "1.0"}).status_code == 400
        assert not calls and not recipients
        response = client.post("/", json=request, headers={"A2A-Version": "1.0", "Authorization": "Bearer " + "a" * 32})
        assert "approved" in response.text
        assert len(calls) == 1 and recipients == ["caller"]


@pytest.mark.parametrize("credentials", [{}, {"A": "short"}, {"A": "x" * 32, "B": "x" * 32}])
def test_invalid_credentials_fail_startup(tmp_path, monkeypatch, credentials):
    for key, value in credentials.items():
        monkeypatch.setenv(key, value)
    config = tmp_path / "client.json"
    names = list(credentials) or ["MISSING_TEST_CREDENTIAL"]
    config.write_text(json.dumps({"identities": {name: {"a2a_credential_env": name} for name in names}}))
    with pytest.raises(ValueError):
        AuthenticatedWebServer(str(config))
