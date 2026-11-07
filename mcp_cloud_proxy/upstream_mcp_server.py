"""
Upstream MCP Server Reference Implementation (JSON-RPC 2.0 over stdio / HTTP / SSE).
Exposes a realistic catalog of 12 enterprise AI agent tools across 4 categories:
  1. Knowledge & RAG Retrieval (deterministic / cacheable read-only)
  2. Database & Analytics Queries (semi-cacheable / parameterized)
  3. Cloud Infrastructure & Observability Inspection (short-TTL telemetry)
  4. Mutating / Transactional Enterprise Actions (non-cacheable, strict RBAC)
"""

import asyncio
import hashlib
import json
import math
import time
from typing import Any, Dict, List, Optional


TOOL_CATALOG: List[Dict[str, Any]] = [
    {
        "name": "kb_vector_search",
        "category": "retrieval",
        "cacheable": True,
        "ttl_seconds": 300,
        "base_exec_ms": 18.5,
        "required_scope": "mcp:tools:read",
        "description": "Search enterprise knowledge base embeddings and return top-k document chunks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "default": 5},
                "namespace": {"type": "string", "default": "eng-docs"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "doc_metadata_lookup",
        "category": "retrieval",
        "cacheable": True,
        "ttl_seconds": 600,
        "base_exec_ms": 8.2,
        "required_scope": "mcp:tools:read",
        "description": "Fetch canonical metadata and access control tags for an enterprise document ID.",
        "inputSchema": {
            "type": "object",
            "properties": {"doc_id": {"type": "string"}},
            "required": ["doc_id"],
        },
    },
    {
        "name": "code_symbol_index",
        "category": "retrieval",
        "cacheable": True,
        "ttl_seconds": 300,
        "base_exec_ms": 14.0,
        "required_scope": "mcp:tools:read",
        "description": "Query cross-repository AST symbol index for function definitions and call sites.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "repo": {"type": "string"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "sql_analytics_query",
        "category": "analytics",
        "cacheable": True,
        "ttl_seconds": 120,
        "base_exec_ms": 34.0,
        "required_scope": "mcp:tools:analytics",
        "description": "Execute read-only parameterized SQL aggregation against the analytics warehouse.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string"},
                "dimension": {"type": "string"},
                "window_hours": {"type": "integer"},
            },
            "required": ["metric", "dimension"],
        },
    },
    {
        "name": "feature_store_fetch",
        "category": "analytics",
        "cacheable": True,
        "ttl_seconds": 60,
        "base_exec_ms": 11.5,
        "required_scope": "mcp:tools:analytics",
        "description": "Retrieve real-time ML feature vector for a given entity key.",
        "inputSchema": {
            "type": "object",
            "properties": {"entity_id": {"type": "string"}, "feature_group": {"type": "string"}},
            "required": ["entity_id"],
        },
    },
    {
        "name": "billing_cost_rollup",
        "category": "analytics",
        "cacheable": True,
        "ttl_seconds": 600,
        "base_exec_ms": 27.5,
        "required_scope": "mcp:tools:analytics",
        "description": "Compute cloud FinOps cost attribution breakdown by service and tenant tag.",
        "inputSchema": {
            "type": "object",
            "properties": {"tenant_id": {"type": "string"}, "period": {"type": "string"}},
            "required": ["tenant_id"],
        },
    },
    {
        "name": "k8s_pod_telemetry",
        "category": "observability",
        "cacheable": True,
        "ttl_seconds": 15,
        "base_exec_ms": 15.0,
        "required_scope": "mcp:tools:ops",
        "description": "Inspect live CPU, memory, and restart telemetry for Kubernetes pods in a namespace.",
        "inputSchema": {
            "type": "object",
            "properties": {"cluster": {"type": "string"}, "namespace": {"type": "string"}},
            "required": ["cluster", "namespace"],
        },
    },
    {
        "name": "log_trace_correlate",
        "category": "observability",
        "cacheable": False,
        "ttl_seconds": 0,
        "base_exec_ms": 29.0,
        "required_scope": "mcp:tools:ops",
        "description": "Correlate distributed OpenTelemetry spans and error logs for a trace ID.",
        "inputSchema": {
            "type": "object",
            "properties": {"trace_id": {"type": "string"}, "service": {"type": "string"}},
            "required": ["trace_id"],
        },
    },
    {
        "name": "slo_burn_rate_check",
        "category": "observability",
        "cacheable": True,
        "ttl_seconds": 30,
        "base_exec_ms": 12.0,
        "required_scope": "mcp:tools:ops",
        "description": "Evaluate multi-window error budget burn rates for a production microservice.",
        "inputSchema": {
            "type": "object",
            "properties": {"service_name": {"type": "string"}},
            "required": ["service_name"],
        },
    },
    {
        "name": "jira_incident_create",
        "category": "mutation",
        "cacheable": False,
        "ttl_seconds": 0,
        "base_exec_ms": 42.0,
        "required_scope": "mcp:tools:write",
        "description": "Create an audited incident ticket and page the on-call rotation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "severity": {"type": "string"},
                "summary": {"type": "string"},
            },
            "required": ["title", "severity"],
        },
    },
    {
        "name": "iam_temp_grant",
        "category": "mutation",
        "cacheable": False,
        "ttl_seconds": 0,
        "base_exec_ms": 38.0,
        "required_scope": "mcp:tools:admin",
        "description": "Request a time-bounded JIT privilege elevation for emergency remediation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "principal": {"type": "string"},
                "role": {"type": "string"},
                "duration_min": {"type": "integer"},
            },
            "required": ["principal", "role"],
        },
    },
    {
        "name": "ci_pipeline_trigger",
        "category": "mutation",
        "cacheable": False,
        "ttl_seconds": 0,
        "base_exec_ms": 31.0,
        "required_scope": "mcp:tools:write",
        "description": "Trigger a canary deployment or rollback workflow on the CI/CD orchestrator.",
        "inputSchema": {
            "type": "object",
            "properties": {"repo": {"type": "string"}, "branch": {"type": "string"}},
            "required": ["repo", "branch"],
        },
    },
]

TOOL_MAP: Dict[str, Dict[str, Any]] = {t["name"]: t for t in TOOL_CATALOG}


def execute_tool_logic(
    tool_name: str, arguments: Dict[str, Any], payload_kb: float = 2.0, tenant: str = "default"
) -> Dict[str, Any]:
    """
    Executes deterministic CPU + payload construction work representing an MCP tool invocation.
    Results are authorization-dependent: the calling tenant is bound into the result, as a real
    ACL-filtered backend would return different documents/rows per tenant.
    Returns a valid MCP CallToolResult structure.
    """
    t0 = time.perf_counter()
    spec = TOOL_MAP.get(tool_name)
    if not spec:
        return {
            "isError": True,
            "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
        }

    # Deterministic hashing + structured result synthesis
    canonical_args = json.dumps(arguments, sort_keys=True)
    digest = hashlib.sha256(f"{tenant}:{tool_name}:{canonical_args}".encode("utf-8")).hexdigest()

    # Scale response text to requested payload_kb
    target_bytes = max(256, int(payload_kb * 1024))
    base_record = (
        f"[MCP-Result tenant={tenant} tool={tool_name} category={spec['category']} digest={digest[:16]}] "
        f"Executed with parameters={canonical_args}. "
        f"Telemetry status=OK confidence=0.984. "
    )
    repeats = max(1, target_bytes // len(base_record))
    synthesized_text = (base_record * repeats)[:target_bytes]

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "isError": False,
        "content": [
            {
                "type": "text",
                "text": synthesized_text,
            }
        ],
        "_meta": {
            "tool": tool_name,
            "tenant": tenant,
            "category": spec["category"],
            "digest": digest[:16],
            "serialization_ms": round(elapsed_ms, 4),
        },
    }


async def handle_jsonrpc_message(
    msg: Dict[str, Any],
    simulate_upstream_io: bool = True,
    io_scale: float = 0.25,
    payload_kb: float = 2.0,
) -> Dict[str, Any]:
    """
    Processes a single JSON-RPC 2.0 MCP request (`initialize`, `tools/list`, `tools/call`, `ping`).
    Backend service time is emulated with `asyncio.sleep(base_exec_ms * io_scale)`; this is the only
    synthetic delay in the testbed and is identical across all transports.
    The gateway forwards the authenticated tenant in `params._meta.tenant`.
    """
    req_id = msg.get("id", 1)
    method = msg.get("method", "")
    params = msg.get("params", {}) or {}
    tenant = str((params.get("_meta") or {}).get("tenant", "default"))

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "ServerCapabilities": {
                    "tools": {"listChanged": True},
                    "resources": {"subscribe": True},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": {"name": "enterprise-mcp-upstream", "version": "1.4.0"},
            },
        }

    if method == "tools/list":
        if simulate_upstream_io:
            await asyncio.sleep(0.002 * io_scale)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOL_CATALOG},
        }

    if method == "tools/call":
        tool_name = params.get("name", "kb_vector_search")
        arguments = params.get("arguments", {}) or {}
        spec = TOOL_MAP.get(tool_name, TOOL_CATALOG[0])
        if simulate_upstream_io:
            await asyncio.sleep((spec["base_exec_ms"] / 1000.0) * io_scale)
        result = execute_tool_logic(tool_name, arguments, payload_kb=payload_kb, tenant=tenant)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result,
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"status": "pong"}}

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


async def stdio_server_main(io_scale: float, payload_kb: float) -> None:
    """
    Standalone stdio MCP server loop for measuring real process-spawn + stdio pipe overhead.
    Reads newline-delimited JSON-RPC messages from stdin and writes responses to stdout.
    """
    import sys

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            resp = await handle_jsonrpc_message(msg, io_scale=io_scale, payload_kb=payload_kb)
            sys.stdout.write(json.dumps(resp, separators=(",", ":")) + "\n")
            sys.stdout.flush()
        except Exception as exc:
            err = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
            sys.stdout.write(json.dumps(err) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    import argparse

    from hires_timer import enable_high_res_timer

    ap = argparse.ArgumentParser()
    ap.add_argument("--io-scale", type=float, default=0.25)
    ap.add_argument("--payload-kb", type=float, default=2.0)
    a = ap.parse_args()
    enable_high_res_timer()
    asyncio.run(stdio_server_main(a.io_scale, a.payload_kb))
