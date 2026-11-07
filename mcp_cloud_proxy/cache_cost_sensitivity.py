"""
Cost-sensitivity of cost-aware eviction (pre-registered grid).

Compares `tenant_ttl_lru` (tenant-scoped keys, per-tool TTLs, plain LRU) with `proposed_tiered`
(same keys and TTLs, windowed cost-aware eviction, w = 4) at alpha = 1.0 while varying
  - the spread of nominal per-tool costs (rank-preserving, log-spaced for the stretched levels), and
  - cache capacity.
Both policies replay the identical trace for each seed. The primary metric is the share of nominal
upstream service time avoided; costs are bookkeeping only (the upstream server still sleeps for its
normal service time), so latency is not reported. Results go to results/cache_cost_sensitivity.json.
"""

import asyncio
import json
import math
import os
from typing import Any, Dict, List

import numpy as np
from scipy import stats

from hires_timer import enable_high_res_timer
from run_benchmarks import HERE, IO_SCALE, TRIAL_SEEDS, generate_trace, make_gateway, make_transport
from proxy_transports import UpstreamProcess
from upstream_mcp_server import TOOL_CATALOG

ALPHA = 1.0
CAPACITIES = [16, 64, 256]
POLICIES = ["tenant_ttl_lru", "proposed_tiered"]
SPREADS = {"current": None, "moderate": (10.0, 300.0), "wide": (10.0, 2000.0)}
N_SEEDS = int(os.environ.get("MCP_SENS_SEEDS", str(len(TRIAL_SEEDS))))
SEEDS = TRIAL_SEEDS if N_SEEDS <= len(TRIAL_SEEDS) else [101 * k for k in range(1, N_SEEDS + 1)]
OUT = os.path.join(HERE, "results", f"cache_cost_sensitivity_{len(SEEDS)}seeds.json")


def cost_table(spread) -> Dict[str, float]:
    tools = sorted((t for t in TOOL_CATALOG if t["cacheable"]), key=lambda t: t["base_exec_ms"])
    if spread is None:
        return {t["name"]: float(t["base_exec_ms"]) for t in tools}
    lo, hi = spread
    vals = np.geomspace(lo, hi, len(tools))
    return {t["name"]: round(float(v), 2) for t, v in zip(tools, vals)}


def paired_ci(diffs: List[float]) -> Dict[str, float]:
    a = np.asarray(diffs, dtype=np.float64)
    half = float(stats.t.ppf(0.975, len(a) - 1) * a.std(ddof=1) / math.sqrt(len(a))) if len(a) > 1 else 0.0
    m = float(a.mean())
    p = float(stats.ttest_1samp(a, 0.0).pvalue) if len(a) > 1 and a.std(ddof=1) > 0 else 1.0
    return {"mean": round(m, 3), "ci_lo": round(m - half, 3), "ci_hi": round(m + half, 3), "p": round(p, 5)}


async def run_trace(policy: str, capacity: int, costs: Dict[str, float], trace, transport) -> Dict[str, float]:
    gw, tokens = make_gateway(policy, cache_max_entries=capacity, cache_cost_ms=costs)
    saved = total = 0.0
    leaks = 0
    for msg in trace:
        resp, m = await gw.process_request(tokens[msg["_tenant"]], msg, transport.send, simulated_clock=msg["_sim_time"])
        served = ((resp.get("result") or {}).get("_meta") or {}).get("tenant")
        if served is not None and served != msg["_tenant"]:
            leaks += 1
        if msg["method"] == "tools/call" and msg["params"]["name"] in costs:
            c = costs[msg["params"]["name"]]
            total += c
            saved += c if m.cache_hit else 0.0
    n = len(trace)
    return {
        "saved_pct": 100.0 * saved / total,
        "hit_pct": 100.0 * gw.cache_engine.hits / n,
        "leak_pct": 100.0 * leaks / n,
        "stale_pct": 100.0 * gw.cache_engine.stale_reads / n,
        "evictions": gw.cache_engine.evictions,
    }


async def main() -> None:
    enable_high_res_timer()
    up = UpstreamProcess(IO_SCALE, 2.0)
    port = await up.start()
    transport = await make_transport("T4", port, 0)
    out: Dict[str, Any] = {"alpha": ALPHA, "window": 4, "trace_len": 400, "seeds": SEEDS, "cells": []}
    for sname, spread in SPREADS.items():
        costs = cost_table(spread)
        for cap in CAPACITIES:
            per = {p: [] for p in POLICIES}
            for seed in SEEDS:
                trace = generate_trace(400, zipf_alpha=ALPHA, read_ratio=0.8, num_distinct_queries=40, num_tenants=4,
                                       seed=seed + int(ALPHA * 100))
                for p in POLICIES:
                    per[p].append(await run_trace(p, cap, costs, trace, transport))
            cell = {"spread": sname, "costs_ms": costs, "capacity": cap}
            for p in POLICIES:
                cell[p] = {k: round(float(np.mean([r[k] for r in per[p]])), 3) for k in per[p][0]}
            cell["diff_saved"] = paired_ci([b["saved_pct"] - a["saved_pct"] for a, b in zip(per[POLICIES[0]], per[POLICIES[1]])])
            cell["diff_hit"] = paired_ci([b["hit_pct"] - a["hit_pct"] for a, b in zip(per[POLICIES[0]], per[POLICIES[1]])])
            cell["helps"] = cell["diff_saved"]["ci_lo"] > 0
            out["cells"].append(cell)
            d = cell["diff_saved"]
            print(f"{sname:8s} cap={cap:3d}  LRU saved={cell[POLICIES[0]]['saved_pct']:.2f}%  "
                  f"proposed saved={cell[POLICIES[1]]['saved_pct']:.2f}%  diff={d['mean']:+.2f} "
                  f"[{d['ci_lo']:+.2f}, {d['ci_hi']:+.2f}] p={d['p']:.4f}  helps={cell['helps']}  "
                  f"leak={cell[POLICIES[1]]['leak_pct']:.1f} stale={cell[POLICIES[1]]['stale_pct']:.1f} "
                  f"evict={cell[POLICIES[1]]['evictions']:.0f}")
    await transport.stop()
    await up.stop()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(f"-> {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
