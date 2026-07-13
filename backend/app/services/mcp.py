"""Generic MCP (Model Context Protocol) tool adapter.

Runner.ai can be configured with any number of remote MCP servers via the
``MCP_SERVERS`` env var, formatted as a comma-separated list of
``label=url`` pairs:

    MCP_SERVERS=github=https://mcp.example.com/github,notion=https://mcp.example.com/notion

At boot the registry pings each server's ``tools/list`` endpoint (JSON-RPC
2.0 over HTTP) and registers every discovered *read-only* tool with:

* ``kind = "mcp"``
* ``risk_level = "read"``          (write MCP tools are gated by approval —
                                     stub-only in the preview)
* ``executor`` = a callable that forwards to the MCP server's
                 ``tools/call`` endpoint.

If a server declines the discovery call, its tools are simply left out —
the app boots regardless. Discovery is best-effort and logged.

This adapter deliberately avoids the full stdio-based MCP transport
because Runner.ai only speaks to HTTP MCP servers today.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

import httpx

from app.models import ToolBadge
from app.tools.registry import ToolSpec, get_registry

log = logging.getLogger("runner.mcp")

_TIMEOUT = 20.0


def _servers_from_env() -> list[tuple[str, str]]:
    raw = os.environ.get("MCP_SERVERS", "").strip()
    if not raw:
        return []
    out: list[tuple[str, str]] = []
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        label, url = pair.split("=", 1)
        out.append((label.strip(), url.strip()))
    return out


async def _jsonrpc(url: str, method: str, params: dict | None = None) -> Any:
    payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method,
               "params": params or {}}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(url, json=payload,
                                 headers={"Content-Type": "application/json"})
        resp.raise_for_status()
        body = resp.json()
    if "error" in body:
        raise RuntimeError(f"MCP error: {body['error']}")
    return body.get("result")


def _make_executor(server_label: str, server_url: str, tool_name: str):
    """Build an executor callable bound to a specific MCP server + tool."""
    async def _exec(*, user_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG001
        # Strip user_id from args before forwarding — the MCP server should
        # never see our internal user identifiers.
        args = {k: v for k, v in kwargs.items() if k != "user_id"}
        try:
            result = await _jsonrpc(server_url, "tools/call",
                                    {"name": tool_name, "arguments": args})
        except Exception as exc:  # noqa: BLE001
            return {"summary": f"MCP call failed: {exc}", "evidence": [], "error": True}

        content = result.get("content") if isinstance(result, dict) else result
        # MCP tools_call returns a list of `content` blocks. Convert each
        # text block into a synthetic evidence item so the synthesizer can
        # cite it just like any other tool output.
        evidence = []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    evidence.append({
                        "source_type": ToolBadge.CONTEXT.value,
                        "title": f"MCP · {server_label}:{tool_name}",
                        "snippet": str(block.get("text", ""))[:600],
                    })
        elif isinstance(content, str):
            evidence.append({
                "source_type": ToolBadge.CONTEXT.value,
                "title": f"MCP · {server_label}:{tool_name}",
                "snippet": content[:600],
            })
        return {
            "summary": f"MCP {server_label}:{tool_name} returned {len(evidence)} item(s).",
            "evidence": evidence,
        }
    return _exec


async def discover_and_register() -> int:
    """Boot-time discovery. Returns the number of MCP tools registered."""
    servers = _servers_from_env()
    if not servers:
        log.info("MCP: no servers configured (MCP_SERVERS env empty)")
        return 0

    reg = get_registry()
    registered = 0
    for label, url in servers:
        try:
            result = await _jsonrpc(url, "tools/list")
        except Exception as exc:  # noqa: BLE001
            log.warning("MCP: discovery failed for %s (%s): %s", label, url, exc)
            continue

        tools = (result or {}).get("tools") or []
        for t in tools:
            name = t.get("name")
            if not name:
                continue
            description = t.get("description") or f"MCP tool {label}:{name}"
            tool_id = f"mcp_{label}_{name}"
            reg.register(ToolSpec(
                id=tool_id,
                name=f"MCP · {label} · {name}",
                description=description,
                kind="mcp",
                risk_level="read",  # write MCP tools would flip to "sensitive"
                requires_approval=False,
                keywords=[label, name] + (t.get("keywords") or []),
                badge=ToolBadge.CONTEXT,
                typical_questions=[],
                executor=_make_executor(label, url, name),
                is_available=lambda: True,
            ))
            registered += 1
        log.info("MCP: discovered %s tool(s) from %s", len(tools), label)
    return registered
