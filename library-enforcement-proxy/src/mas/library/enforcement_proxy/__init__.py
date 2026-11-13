# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
from dataclasses import fields
from pathlib import Path


def package_root() -> Path:
    return Path(__file__).resolve().parent


def require_native_hooks() -> None:
    try:
        from library_ioa.plugins.a2a.client import A2AClient
        from library_ioa.plugins.a2a.delivery import DeliveryCandidate, DeliveryController, DeliveryPolicy
        from library_ioa.plugins.a2a.exposure import A2AExposure
        from library_ioa.plugins.mcp.provider import MCPToolProvider
        from mas.runtime.boundary.connection import PreparedOperationProvider
        from mas.runtime.boundary.gov.policy import EgressIntentView
        from mas.runtime.boundary.gov.transition import GovTransition
    except ImportError as error:
        raise RuntimeError("MasLab with the native A2A delivery-policy extension is required") from error
    if not callable(getattr(DeliveryController, "release", None)):
        raise RuntimeError("Unsupported MasLab A2A delivery-policy interface")
    if not callable(getattr(A2AExposure, "validate_transports", None)):
        raise RuntimeError("MasLab with native A2A transport validation is required")
    if any(
        "connection" not in {field.name for field in fields(view)} for view in (EgressIntentView, DeliveryCandidate)
    ):
        raise RuntimeError("MasLab with native resolved operation connections is required")
    if not callable(getattr(A2AClient, "prepare_message", None)) or not callable(
        getattr(MCPToolProvider, "prepare_call", None)
    ):
        raise RuntimeError("MasLab IOA providers with resolved operation connections are required")
    del DeliveryPolicy, GovTransition, PreparedOperationProvider
