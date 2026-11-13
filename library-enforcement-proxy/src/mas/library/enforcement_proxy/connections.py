# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Protected destination mappings for native resolved operation bindings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from mas.runtime.boundary.connection import ConnectionInfo, ConnectionTarget


@dataclass(frozen=True)
class _Destination:
    protocol: str
    transport: str
    target: ConnectionTarget


class DestinationBindings:
    def __init__(self, entries: list[dict[str, Any]], identities: dict[str, Any]) -> None:
        self._entries: dict[tuple[str, str], _Destination] = {}
        if not isinstance(entries, list) or not entries:
            raise ValueError("Protected connection bindings are required")
        fields = {"ref", "destination", "protocol", "transport", "endpoint", "command", "cwd", "principal"}
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) - fields:
                raise ValueError("Invalid protected connection binding fields")
            ref, destination = entry.get("ref"), entry.get("destination")
            protocol, transport = entry.get("protocol"), entry.get("transport")
            if (
                not isinstance(ref, str)
                or not isinstance(destination, str)
                or not isinstance(protocol, str)
                or not isinstance(transport, str)
                or protocol not in {"a2a", "mcp"}
                or not ref.startswith(protocol + ":")
                or destination not in identities
                or (ref, destination) in self._entries
                or transport not in ({"http", "stdio"} if protocol == "mcp" else {"http"})
            ):
                raise ValueError("Invalid or duplicate protected connection binding")
            endpoint, command = entry.get("endpoint", ""), entry.get("command", [])
            cwd, principal = entry.get("cwd", ""), entry.get("principal", "")
            if (
                not all(isinstance(value, str) for value in (endpoint, cwd, principal))
                or not isinstance(command, list)
                or any(not isinstance(arg, str) for arg in command)
            ):
                raise ValueError("Invalid protected target selector types")
            if transport == "stdio":
                if endpoint or principal or not command or not command[0] or not Path(cwd).is_absolute():
                    raise ValueError("Stdio bindings require a command vector and absolute working directory")
            elif ref == "a2a:caller":
                if not principal or endpoint or command or cwd:
                    raise ValueError("Caller bindings require an authenticated principal")
            else:
                url = urlsplit(endpoint)
                if (
                    url.scheme not in {"http", "https"}
                    or not url.hostname
                    or url.username
                    or url.password
                    or url.fragment
                    or url.query
                    or command
                    or cwd
                    or principal
                ):
                    raise ValueError("HTTP bindings require an exact endpoint")
            self._entries[(ref, destination)] = _Destination(
                protocol,
                transport,
                ConnectionTarget(endpoint=endpoint, command=tuple(command), cwd=cwd, principal=principal),
            )

    def validate(self, connection: ConnectionInfo | None, destination: str, kind: str) -> dict[str, Any]:
        if not isinstance(connection, ConnectionInfo):
            raise ValueError("Native resolved connection metadata is required")
        binding = self._entries.get((connection.binding_ref, destination))
        protocol = "mcp" if kind == "tool" else "a2a"
        if (
            binding is None
            or binding.protocol != protocol
            or connection.protocol != binding.protocol
            or connection.transport != binding.transport
            or not connection.matches_target(binding.target)
        ):
            raise ValueError("Resolved connection does not match its protected destination binding")
        # Export only this allow-list. Targets and connection credentials remain
        # private even when the candidate itself is authorized for publication.
        return {
            "protocol": connection.protocol,
            "transport": connection.transport,
            "binding_ref": connection.binding_ref,
            "binding_id": connection.binding_id,
            "generation": connection.generation,
        }
