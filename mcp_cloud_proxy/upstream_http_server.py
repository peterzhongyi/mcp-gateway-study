"""
Upstream MCP server over HTTP/1.1, run as a separate OS process from the gateway.

Endpoints:
  POST /mcp                     Streamable HTTP transport (MCP 2025-03-26): JSON-RPC request in the
                                body, JSON-RPC response in the body.
  GET  /sse                     Legacy HTTP+SSE transport (MCP 2024-11-05): opens a long-lived
                                event stream and announces the per-session POST endpoint.
  POST /messages?session_id=ID  Legacy transport POST leg: answered with 202 Accepted; the JSON-RPC
                                response is pushed asynchronously on the session's event stream.

Prints `PORT <n>` on stdout once listening.
"""

import argparse
import asyncio
import itertools
import json
import ssl
from typing import Dict

from hires_timer import enable_high_res_timer
from http11 import build_response, build_sse_event, read_request
from upstream_mcp_server import handle_jsonrpc_message


class UpstreamHTTPServer:
    def __init__(self, io_scale: float, payload_kb: float, max_workers: int):
        self.io_scale = io_scale
        self.payload_kb = payload_kb
        self._workers = asyncio.Semaphore(max_workers)
        self._sse_sessions: Dict[str, asyncio.StreamWriter] = {}
        self._sse_locks: Dict[str, asyncio.Lock] = {}
        self._ids = itertools.count(1)

    async def _process(self, body: bytes) -> bytes:
        msg = json.loads(body)
        async with self._workers:
            resp = await handle_jsonrpc_message(msg, io_scale=self.io_scale, payload_kb=self.payload_kb)
        return json.dumps(resp, separators=(",", ":")).encode("utf-8")

    async def _push_sse(self, session_id: str, body: bytes) -> None:
        out = await self._process(body)
        writer = self._sse_sessions.get(session_id)
        if writer is None:
            return
        async with self._sse_locks[session_id]:
            writer.write(build_sse_event("message", out.decode("utf-8")))
            await writer.drain()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                req = await read_request(reader)
                if req is None:
                    break
                method, target, headers, body = req
                keep_alive = headers.get("connection", "keep-alive").lower() != "close"

                if method == "POST" and target == "/mcp":
                    out = await self._process(body)
                    writer.write(build_response(200, "OK", out, keep_alive))
                    await writer.drain()

                elif method == "GET" and target == "/sse":
                    sid = f"s{next(self._ids)}"
                    self._sse_sessions[sid] = writer
                    self._sse_locks[sid] = asyncio.Lock()
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                        b"Cache-Control: no-cache\r\nConnection: keep-alive\r\n\r\n"
                    )
                    writer.write(build_sse_event("endpoint", f"/messages?session_id={sid}"))
                    await writer.drain()
                    try:
                        await reader.read()
                    finally:
                        self._sse_sessions.pop(sid, None)
                        self._sse_locks.pop(sid, None)
                    break

                elif method == "POST" and target.startswith("/messages?session_id="):
                    sid = target.split("=", 1)[1]
                    if sid not in self._sse_sessions:
                        writer.write(build_response(404, "Not Found", b"", keep_alive))
                    else:
                        asyncio.ensure_future(self._push_sse(sid, body))
                        writer.write(build_response(202, "Accepted", b"", keep_alive))
                    await writer.drain()

                else:
                    writer.write(build_response(404, "Not Found", b"", keep_alive))
                    await writer.drain()

                if not keep_alive:
                    break
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--io-scale", type=float, default=0.25)
    ap.add_argument("--payload-kb", type=float, default=2.0)
    ap.add_argument("--max-workers", type=int, default=256)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--tls-cert")
    ap.add_argument("--tls-key")
    a = ap.parse_args()
    enable_high_res_timer()

    tls = None
    if a.tls_cert:
        tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        tls.minimum_version = ssl.TLSVersion.TLSv1_3
        tls.load_cert_chain(a.tls_cert, a.tls_key)
    srv = UpstreamHTTPServer(a.io_scale, a.payload_kb, a.max_workers)
    server = await asyncio.start_server(srv.handle, a.host, a.port, backlog=4096, ssl=tls)
    print(f"PORT {server.sockets[0].getsockname()[1]}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
