"""
Gateway-to-upstream transport topologies, measured over real OS pipes and loopback TCP sockets.
The upstream MCP server always runs in a separate OS process.

  T1: stdio bridge         Persistent child process; newline-delimited JSON-RPC over stdin/stdout.
  T2: legacy HTTP+SSE      Long-lived GET /sse event stream per session; each call is a POST to
                           /messages (202 Accepted) and the response arrives on the event stream.
  T3: Streamable HTTP,     One TCP connection per call (`Connection: close`), as in a stateless
      unpooled             or serverless gateway with no connection reuse.
  T4: Streamable HTTP,     Fixed pool of persistent keep-alive connections shared by all agent
      pooled               sessions.

Network delay emulation (`NetDelay`): loopback has near-zero RTT, which hides the cost of
connection setup. When `rtt_ms > 0`, each request/response exchange incurs one RTT and each new
connection incurs two extra RTTs (TCP handshake + TLS 1.3 handshake). Delay is injected at the
application layer with `asyncio.sleep` while the connection is held, because an HTTP/1.1
connection stays busy until its response arrives; T1 is host-local and never incurs network delay.

Every network transport accumulates `hold_s`/`hold_n`: the time a pooled connection (or in-flight
slot) is occupied per call, which is the service time of the connection-pool station in the
queueing model.
"""

import asyncio
import collections
import itertools
import json
import os
import ssl
import sys
import time
from typing import Any, Dict, Optional, Tuple

from http11 import build_request, read_response, read_sse_event

HERE = os.path.dirname(os.path.abspath(__file__))
TLS = os.environ.get("MCP_TLS") == "1"

Conn = Tuple[asyncio.StreamReader, asyncio.StreamWriter]


def _client_tls_context() -> ssl.SSLContext:
    # Benchmark upstream uses a throwaway self-signed certificate, so the peer is not verified;
    # handshake and record-layer costs are the same as with verification.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    return ctx


_TLS_CTX = _client_tls_context() if TLS else None


async def _connect(host: str, port: int) -> Conn:
    """Upstream data connection: plain TCP, or TLS 1.3 with a full handshake when MCP_TLS=1."""
    return await asyncio.open_connection(host, port, ssl=_TLS_CTX)


class NetDelay:
    def __init__(self, rtt_ms: float = 0.0):
        self.rtt_s = rtt_ms / 1000.0

    async def round_trip(self) -> None:
        if self.rtt_s > 0:
            await asyncio.sleep(self.rtt_s)

    async def handshake(self) -> None:
        if self.rtt_s > 0:
            await asyncio.sleep(2 * self.rtt_s)


class FifoPool:
    """
    Connection pool that serves waiters in arrival order. A returned connection is handed directly
    to the longest-waiting caller, so a newly arriving call cannot take it first. asyncio.Queue does
    not guarantee this: a woken getter can find the item already taken and re-queue at the back,
    which starves some sessions under saturation and distorts mean and tail latency.
    """

    def __init__(self) -> None:
        self._items: "collections.deque[Conn]" = collections.deque()
        self._waiters: "collections.deque[asyncio.Future]" = collections.deque()

    def put_nowait(self, item: Conn) -> None:
        while self._waiters:
            fut = self._waiters.popleft()
            if not fut.done():
                fut.set_result(item)
                return
        self._items.append(item)

    async def put(self, item: Conn) -> None:
        self.put_nowait(item)

    async def get(self) -> Conn:
        if self._items and not self._waiters:
            return self._items.popleft()
        fut = asyncio.get_running_loop().create_future()
        self._waiters.append(fut)
        try:
            return await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                self.put_nowait(fut.result())
            raise

    def empty(self) -> bool:
        return not self._items

    def get_nowait(self) -> Conn:
        return self._items.popleft()


class HoldStats:
    hold_s = 0.0
    hold_n = 0

    def reset_hold(self) -> None:
        self.hold_s, self.hold_n = 0.0, 0

    def mean_hold_ms(self) -> float:
        return 1000.0 * self.hold_s / self.hold_n if self.hold_n else 0.0


async def _close(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass


async def _agent_call(path: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    host, port = os.environ["MCP_UPSTREAM_AGENT"].rsplit(":", 1)
    reader, writer = await asyncio.open_connection(host, int(port))
    writer.write(build_request("POST", path, json.dumps(cfg).encode(), keep_alive=False))
    await writer.drain()
    status, _, body = await read_response(reader)
    await _close(writer)
    if status != 200:
        raise RuntimeError(f"upstream agent {path} failed: {body!r}")
    return json.loads(body)


class UpstreamProcess:
    """Launches `upstream_http_server.py` and waits for its port: as a local child process, or on
    a remote host through `upstream_agent.py` when MCP_UPSTREAM_AGENT=host:port is set."""

    def __init__(self, io_scale: float, payload_kb: float, max_workers: int = 256):
        self.cfg = {"io_scale": io_scale, "payload_kb": payload_kb, "max_workers": max_workers, "tls": TLS}
        self.args = ["--io-scale", str(io_scale), "--payload-kb", str(payload_kb), "--max-workers", str(max_workers)]
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.port = 0
        self.remote = bool(os.environ.get("MCP_UPSTREAM_AGENT"))
        if TLS and not self.remote:
            raise RuntimeError("MCP_TLS=1 requires a remote upstream (MCP_UPSTREAM_AGENT)")

    async def start(self) -> int:
        if self.remote:
            self.port = (await _agent_call("/start", self.cfg))["port"]
            return self.port
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable, os.path.join(HERE, "upstream_http_server.py"), *self.args,
            stdout=asyncio.subprocess.PIPE, cwd=HERE,
        )
        assert self.proc.stdout is not None
        line = (await self.proc.stdout.readline()).decode().strip()
        if not line.startswith("PORT "):
            raise RuntimeError(f"upstream failed to start: {line!r}")
        self.port = int(line.split()[1])
        return self.port

    async def stop(self) -> None:
        if self.remote:
            if self.port:
                await _agent_call("/stop", {"port": self.port})
            return
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()


class T1StdioBridge:
    name = "T1"

    def __init__(self, io_scale: float, payload_kb: float):
        self.args = ["--io-scale", str(io_scale), "--payload-kb", str(payload_kb)]
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable, os.path.join(HERE, "upstream_mcp_server.py"), *self.args,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, cwd=HERE,
        )
        await self.send({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()

    async def send(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        assert self.proc is not None and self.proc.stdin is not None and self.proc.stdout is not None
        async with self._lock:
            self.proc.stdin.write(json.dumps(msg, separators=(",", ":")).encode() + b"\n")
            await self.proc.stdin.drain()
            line = await self.proc.stdout.readline()
        return json.loads(line)


class T2LegacySSE(HoldStats):
    name = "T2"

    def __init__(self, host: str, port: int, pool_size: int, net: NetDelay):
        self.host, self.port, self.pool_size, self.net = host, port, pool_size, net
        self._pool = FifoPool()
        self._pending: Dict[int, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self._endpoint = ""
        self._sse: Optional[Conn] = None
        self._reader_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        await self.net.handshake()
        reader, writer = await _connect(self.host, self.port)
        writer.write(b"GET /sse HTTP/1.1\r\nHost: upstream\r\nAccept: text/event-stream\r\n\r\n")
        await writer.drain()
        while (await reader.readline()) not in (b"\r\n", b"\n"):
            pass
        event, data = await read_sse_event(reader)
        assert event == "endpoint"
        self._endpoint = data
        await self.net.round_trip()
        self._sse = (reader, writer)
        self._reader_task = asyncio.ensure_future(self._dispatch_events())
        for _ in range(self.pool_size):
            await self._pool.put(await _connect(self.host, self.port))

    async def _dispatch_events(self) -> None:
        assert self._sse is not None
        reader = self._sse[0]
        try:
            while True:
                _, data = await read_sse_event(reader)
                resp = json.loads(data)
                fut = self._pending.pop(resp.get("id"), None)
                if fut is not None and not fut.done():
                    fut.set_result((resp, time.perf_counter()))
        except (ConnectionResetError, asyncio.IncompleteReadError, asyncio.CancelledError):
            pass

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self._sse:
            await _close(self._sse[1])
        while not self._pool.empty():
            await _close(self._pool.get_nowait()[1])

    async def send(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        internal_id = next(self._ids)
        orig_id = msg.get("id")
        fut = asyncio.get_running_loop().create_future()
        self._pending[internal_id] = fut
        body = json.dumps({**msg, "id": internal_id}, separators=(",", ":")).encode()

        reader, writer = await self._pool.get()
        t_hold = time.perf_counter()
        try:
            writer.write(build_request("POST", self._endpoint, body, keep_alive=True))
            await writer.drain()
            status, _, _ = await read_response(reader)
            assert status == 202
            await self.net.round_trip()
        finally:
            self.hold_s += time.perf_counter() - t_hold
            self.hold_n += 1
            self._pool.put_nowait((reader, writer))
        resp, t_emitted = await fut
        # Over a real network the event reaches the client one RTT after the server received the
        # POST plus its processing time; on loopback that instant is t_emitted + RTT.
        remaining = t_emitted + self.net.rtt_s - time.perf_counter()
        if remaining > 0:
            await asyncio.sleep(remaining)
        resp["id"] = orig_id
        return resp


class T3StreamableUnpooled(HoldStats):
    name = "T3"

    def __init__(self, host: str, port: int, max_inflight: int, net: NetDelay):
        self.host, self.port, self.net = host, port, net
        self._sem = asyncio.Semaphore(max_inflight)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(msg, separators=(",", ":")).encode()
        async with self._sem:
            t_hold = time.perf_counter()
            await self.net.handshake()
            reader, writer = await _connect(self.host, self.port)
            try:
                writer.write(build_request("POST", "/mcp", body, keep_alive=False, accept="application/json, text/event-stream"))
                await writer.drain()
                _, _, resp_body = await read_response(reader)
            finally:
                await _close(writer)
            await self.net.round_trip()
            self.hold_s += time.perf_counter() - t_hold
            self.hold_n += 1
        return json.loads(resp_body)


class T4StreamablePooled(HoldStats):
    name = "T4"

    def __init__(self, host: str, port: int, pool_size: int, net: NetDelay):
        self.host, self.port, self.pool_size, self.net = host, port, pool_size, net
        self._pool = FifoPool()

    async def start(self) -> None:
        for _ in range(self.pool_size):
            await self.net.handshake()
            await self._pool.put(await _connect(self.host, self.port))

    async def stop(self) -> None:
        while not self._pool.empty():
            await _close(self._pool.get_nowait()[1])

    async def send(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(msg, separators=(",", ":")).encode()
        req = build_request("POST", "/mcp", body, keep_alive=True, accept="application/json, text/event-stream")
        reader, writer = await self._pool.get()
        t_hold = time.perf_counter()
        try:
            writer.write(req)
            await writer.drain()
            _, _, resp_body = await read_response(reader)
        except (ConnectionResetError, asyncio.IncompleteReadError):
            await _close(writer)
            await self.net.handshake()
            reader, writer = await _connect(self.host, self.port)
            writer.write(req)
            await writer.drain()
            _, _, resp_body = await read_response(reader)
        try:
            await self.net.round_trip()
        finally:
            self.hold_s += time.perf_counter() - t_hold
            self.hold_n += 1
            self._pool.put_nowait((reader, writer))
        return json.loads(resp_body)
