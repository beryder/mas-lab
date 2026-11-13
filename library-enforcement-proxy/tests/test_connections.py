# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from mas.library.enforcement_proxy.connections import DestinationBindings
from mas.runtime.boundary.connection import ConnectionBinding, ConnectionTarget, Invocation


def entry(**patch):
    return {
        "ref": "mcp:records",
        "destination": "worker",
        "protocol": "mcp",
        "transport": "http",
        "endpoint": "https://records.test/mcp",
        **patch,
    }


@pytest.mark.parametrize(
    "patch",
    [
        {"transport": "grpc"},
        {"protocol": "other"},
        {"ref": "unknown"},
        {"destination": "unregistered"},
        {"endpoint": ""},
        {"endpoint": "file:///records"},
        {"endpoint": "https://user:secret@records.test/mcp"},
        {"endpoint": "https://records.test/mcp?token=secret"},
        {"endpoint": "https://records.test/mcp#fragment"},
        {"command": ["server"]},
        {"principal": "worker"},
        {"cwd": "/tmp"},
        {"unknown": "value"},
        {"protocol": "a2a", "ref": "a2a:worker", "transport": "stdio"},
    ],
)
def test_invalid_http_bindings_are_rejected(patch):
    with pytest.raises(ValueError):
        DestinationBindings([entry(**patch)], {"worker": {}})


@pytest.mark.parametrize("command,cwd", [([], "/tmp"), ([""], "/tmp"), ("server", "/tmp"), (["server"], "relative")])
def test_stdio_requires_exact_invocation_and_working_directory(command, cwd):
    with pytest.raises(ValueError):
        DestinationBindings(
            [
                entry(endpoint="", transport="stdio", command=command, cwd=cwd),
            ],
            {"worker": {}},
        )


def test_stdio_mapping_checks_actual_command_not_only_transport(tmp_path):
    bindings = DestinationBindings(
        [
            entry(endpoint="", transport="stdio", command=["server-a", "--records"], cwd=str(tmp_path)),
        ],
        {"worker": {}},
    )
    for command, accepted in [(("server-a", "--records"), True), (("server-b", "--records"), False)]:
        owner = ConnectionBinding(
            "mcp",
            "stdio",
            ConnectionTarget(command=command, cwd=str(tmp_path)),
            lambda *_: Invocation(lambda: None),
            binding_ref="mcp:records",
        )
        operation = owner.prepare("write", {})
        if accepted:
            assert bindings.validate(operation.connection, "worker", "tool")["transport"] == "stdio"
        else:
            with pytest.raises(ValueError, match="destination"):
                bindings.validate(operation.connection, "worker", "tool")
        assert operation.connection.transport == "stdio"


def test_duplicate_destination_binding_is_not_ambiguous():
    with pytest.raises(ValueError, match="duplicate"):
        DestinationBindings([entry(), entry()], {"worker": {}})


@pytest.mark.parametrize("principal,endpoint", [("", ""), ("worker", "https://worker.test")])
def test_inbound_recipient_requires_principal_only(principal, endpoint):
    with pytest.raises(ValueError, match="principal"):
        DestinationBindings(
            [
                entry(ref="a2a:caller", protocol="a2a", endpoint=endpoint, principal=principal),
            ],
            {"worker": {}},
        )
