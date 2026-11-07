"""
Control agent for multi-host runs. Runs on the upstream host and launches or stops
`upstream_http_server.py` instances on behalf of the benchmark harness on the gateway host:

  POST /start  body {"io_scale": .., "payload_kb": .., "max_workers": ..}  -> {"port": n}
  POST /stop   body {"port": n}                                            -> {"stopped": n}

Usage: python upstream_agent.py --port 7000
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from typing import Dict

from http11 import build_response, read_request

HERE = os.path.dirname(os.path.abspath(__file__))
PROCS: Dict[int, asyncio.subprocess.Process] = {}


def tls_files() -> list:
    """Self-signed P-256 certificate for TLS runs, created once per host."""
    cert, key = os.path.join(HERE, "tls_cert.pem"), os.path.join(HERE, "tls_key.pem")
    if not os.path.exists(cert):
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-keyout", key, "-out", cert, "-days", "7", "-subj", "/CN=upstream"],
                       check=True, capture_output=True)
    return ["--tls-cert", cert, "--tls-key", key]


async def start_upstream(cfg: dict) -> int:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, os.path.join(HERE, "upstream_http_server.py"),
        "--io-scale", str(cfg["io_scale"]), "--payload-kb", str(cfg["payload_kb"]),
        "--max-workers", str(cfg.get("max_workers", 256)), "--host", "0.0.0.0",
        *(tls_files() if cfg.get("tls") else []),
        stdout=asyncio.subprocess.PIPE, cwd=HERE,
    )
    assert proc.stdout is not None
    line = (await proc.stdout.readline()).decode().strip()
    if not line.startswith("PORT "):
        raise RuntimeError(f"upstream failed to start: {line!r}")
    port = int(line.split()[1])
    PROCS[port] = proc
    return port


async def stop_upstream(port: int) -> None:
    proc = PROCS.pop(port, None)
    if proc and proc.returncode is None:
        proc.kill()
        await proc.wait()


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        req = await read_request(reader)
        if req is None:
            return
        method, target, _, body = req
        cfg = json.loads(body or b"{}")
        if method == "POST" and target == "/start":
            out = {"port": await start_upstream(cfg)}
        elif method == "POST" and target == "/stop":
            await stop_upstream(int(cfg["port"]))
            out = {"stopped": int(cfg["port"])}
        else:
            writer.write(build_response(404, "Not Found", b"{}", keep_alive=False))
            return
        writer.write(build_response(200, "OK", json.dumps(out).encode(), keep_alive=False))
    except Exception as e:  # report failures to the harness instead of hanging it
        writer.write(build_response(500, "Error", json.dumps({"error": str(e)}).encode(), keep_alive=False))
    finally:
        await writer.drain()
        writer.close()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7000)
    a = ap.parse_args()
    server = await asyncio.start_server(handle, "0.0.0.0", a.port)
    print(f"AGENT {a.port}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
