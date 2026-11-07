"""
Minimal HTTP/1.1 message framing over asyncio streams (request line, headers, Content-Length
bodies, `Connection: keep-alive|close`, and `text/event-stream` events). Used by both the upstream
server and the gateway transports so that every topology pays the same parsing cost.
"""

import asyncio
from typing import Dict, Optional, Tuple

CRLF = b"\r\n"


async def _read_headers(reader: asyncio.StreamReader) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    while True:
        line = await reader.readline()
        if not line:
            raise asyncio.IncompleteReadError(b"", None)
        if line in (b"\r\n", b"\n"):
            return headers
        k, _, v = line.decode("latin-1").partition(":")
        headers[k.strip().lower()] = v.strip()


async def read_request(reader: asyncio.StreamReader) -> Optional[Tuple[str, str, Dict[str, str], bytes]]:
    line = await reader.readline()
    if not line:
        return None
    method, target, _ = line.decode("latin-1").rstrip("\r\n").split(" ", 2)
    headers = await _read_headers(reader)
    n = int(headers.get("content-length", "0"))
    body = await reader.readexactly(n) if n else b""
    return method, target, headers, body


async def read_response(reader: asyncio.StreamReader) -> Tuple[int, Dict[str, str], bytes]:
    line = await reader.readline()
    if not line:
        raise ConnectionResetError("connection closed before response")
    status = int(line.split(b" ", 2)[1])
    headers = await _read_headers(reader)
    n = int(headers.get("content-length", "0"))
    body = await reader.readexactly(n) if n else b""
    return status, headers, body


def build_request(method: str, target: str, body: bytes, keep_alive: bool, accept: str = "application/json") -> bytes:
    head = (
        f"{method} {target} HTTP/1.1\r\n"
        f"Host: upstream\r\n"
        f"Content-Type: application/json\r\n"
        f"Accept: {accept}\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: {'keep-alive' if keep_alive else 'close'}\r\n\r\n"
    )
    return head.encode("latin-1") + body


def build_response(status: int, reason: str, body: bytes, keep_alive: bool, content_type: str = "application/json") -> bytes:
    head = (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: {'keep-alive' if keep_alive else 'close'}\r\n\r\n"
    )
    return head.encode("latin-1") + body


def build_sse_event(event: str, data: str) -> bytes:
    return f"event: {event}\ndata: {data}\n\n".encode("utf-8")


async def read_sse_event(reader: asyncio.StreamReader) -> Tuple[str, str]:
    event, data = "message", []
    while True:
        line = await reader.readline()
        if not line:
            raise ConnectionResetError("SSE stream closed")
        s = line.decode("utf-8").rstrip("\n")
        if s == "":
            if data:
                return event, "\n".join(data)
            continue
        if s.startswith("event: "):
            event = s[7:]
        elif s.startswith("data: "):
            data.append(s[6:])
