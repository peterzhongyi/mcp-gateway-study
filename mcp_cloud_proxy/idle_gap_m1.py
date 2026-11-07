"""
Sensitivity of M1 (identity) cost to idle time between calls.

Replays the ablation's A1 configuration (M1 only, T4 transport, 4 KB responses, five seeds of 150 calls)
with three upstream service-time scales: the ablation's (2.1-10.5 ms), the middleware experiment's
(one fifth of that), and zero. The M1 code path is identical in all three; only the time the gateway
idles between calls changes. Results go to results/idle_gap_m1.json.
"""

import asyncio
import json
import os

import numpy as np

from hires_timer import enable_high_res_timer
from proxy_transports import UpstreamProcess
from run_benchmarks import ABLATION, HERE, IO_SCALE, TRIAL_SEEDS, generate_trace, make_gateway, make_transport

OUT = os.path.join(HERE, "results", "idle_gap_m1.json")
SETTINGS = [("ablation", IO_SCALE), ("middleware", IO_SCALE * 0.2), ("none", 0.0)]


async def run(scale: float) -> float:
    up = UpstreamProcess(scale, 4.0)
    port = await up.start()
    transport = await make_transport("T4", port, 0, 4.0)
    flags = ABLATION[1][2]
    means = []
    for seed in TRIAL_SEEDS:
        trace = generate_trace(150, zipf_alpha=1.0, seed=seed)
        gw, tokens = make_gateway("none", **flags)
        auth = []
        for msg in trace:
            _, m = await gw.process_request(tokens[msg["_tenant"]], msg, transport.send, simulated_clock=msg["_sim_time"])
            auth.append(m.auth_exchange_ms)
        means.append(float(np.mean(auth)))
    await transport.stop()
    await up.stop()
    return float(np.mean(means))


async def main() -> None:
    enable_high_res_timer()
    out = {"io_scales": {}, "m1_ms": {}}
    for name, scale in SETTINGS:
        out["io_scales"][name] = scale
        out["m1_ms"][name] = round(await run(scale), 4)
        print(name, out["m1_ms"][name])
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(f"-> {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
