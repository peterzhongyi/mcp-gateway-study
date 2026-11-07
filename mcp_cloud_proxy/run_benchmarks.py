"""
Benchmark suite for the MCP gateway prototype.

Testbed: one host; the load generator and gateway share one Python process (asyncio Proactor loop),
the upstream MCP server runs in a separate process (HTTP/1.1 over loopback TCP, or stdio pipes).
All processes request 1 ms Windows timer resolution. The only synthetic delays are (i) emulated
upstream tool service time and (ii) optional emulated network RTT (see proxy_transports.NetDelay).

Experiments:
  exp1  Transport topologies (T1-T4, T4+cache), sequential calls, RTT in {0, 2, 10} ms; session setup cost.
  exp2  Per-stage middleware CPU latency vs. payload size.
  exp3  Ablation: latency and measured security recall/FPR for six progressive configurations.
  exp4  Cache policies (none, global FIFO, global LRU, tenant LRU, proposed) vs. Zipf skew.
  exp5  Closed-loop concurrency N in {1..128}; asymptotic-bound queueing model vs. measurement; heap.
  exp6  Analytical cloud cost model parameterized by exp1/exp4/exp5 measurements.

Usage: python run_benchmarks.py [exp1 exp2 ...]   (default: all; results merged into the JSON file)
"""

import asyncio
import gc
import json
import math
import os
import platform
import random
import sys
import time
import tracemalloc
from typing import Any, Dict, List, Tuple

import numpy as np

import security_suite
from gateway_middleware import MCPGatewayPipeline
from hires_timer import enable_high_res_timer
from proxy_transports import (
    NetDelay,
    T1StdioBridge,
    T2LegacySSE,
    T3StreamableUnpooled,
    T4StreamablePooled,
    UpstreamProcess,
)
from upstream_mcp_server import TOOL_CATALOG, TOOL_MAP

HERE = os.path.dirname(os.path.abspath(__file__))

# Multi-host runs (see cloud/aws_bench.py) override these through the environment.
def _env_list(name: str, default: str, cast=float) -> list:
    return [cast(x) for x in os.environ.get(name, default).split(",") if x]


OUT_PATH = os.environ.get("MCP_OUT") or os.path.join(HERE, "results", "benchmark_metrics.json")
TRIAL_SEEDS = [101, 202, 303, 404, 505][: int(os.environ.get("MCP_TRIALS", "5"))]
IO_SCALE = float(os.environ.get("MCP_IO_SCALE", "0.25"))  # upstream service time = base_exec_ms * IO_SCALE (2.1 - 10.5 ms at 0.25)
POOL = 16                # connection pool size / in-flight limit for every network transport
HOST = os.environ.get("MCP_UPSTREAM_HOST", "127.0.0.1")
EXP1_RTTS = _env_list("MCP_RTTS", "0,2,10")
EXP5_RTT = float(os.environ.get("MCP_CONC_RTT", "2"))
EXP5_LEVELS = _env_list("MCP_LEVELS", "1,2,4,8,16,32,64,128", int)
EXP5_CALLS_PER_SESSION = int(os.environ.get("MCP_CALLS_PER_SESSION", "32"))
# Throughput over the window in which every session is still active (until the first session finishes),
# so that stragglers draining at the end of a run do not lower it. MCP_STEADY_X=0 gives calls / total time.
EXP5_STEADY_X = os.environ.get("MCP_STEADY_X", "1") == "1"


def pct(data: List[float], p: float) -> float:
    return float(np.percentile(np.asarray(data, dtype=np.float64), p)) if data else 0.0


def mean_sd(vals: List[float]) -> Tuple[float, float]:
    a = np.asarray(vals, dtype=np.float64)
    return round(float(a.mean()), 4), round(float(a.std(ddof=1)) if len(a) > 1 else 0.0, 4)


def ci95(vals: List[float]) -> float:
    """Half-width of a 95% t-interval across trials."""
    from scipy import stats

    a = np.asarray(vals, dtype=np.float64)
    if len(a) < 2:
        return 0.0
    return round(float(stats.t.ppf(0.975, len(a) - 1) * a.std(ddof=1) / math.sqrt(len(a))), 4)


def summarize(trials: List[List[float]]) -> Dict[str, Any]:
    """Per-trial statistics aggregated across trials (mean +/- SD of the per-trial statistic)."""
    out: Dict[str, Any] = {}
    for name, fn in [("mean", np.mean), ("p50", lambda x: pct(x, 50)), ("p95", lambda x: pct(x, 95)), ("p99", lambda x: pct(x, 99))]:
        per = [float(fn(t)) for t in trials]
        m, s = mean_sd(per)
        out[f"{name}_ms"] = m
        out[f"{name}_sd"] = s
        out[f"{name}_ci95"] = ci95(per)
    return out


def generate_trace(
    num_requests: int,
    zipf_alpha: float = 1.0,
    read_ratio: float = 0.75,
    num_distinct_queries: int = 40,
    num_tenants: int = 4,
    seed: int = 101,
) -> List[Dict[str, Any]]:
    """
    Multi-tenant MCP call trace. Read-only calls draw a query id from a Zipf(alpha) distribution
    over `num_distinct_queries`; mutating calls carry unique arguments. Inter-arrival gaps on a
    simulated clock are U(1.2, 3.8) s so that per-tool TTLs (15-600 s) expire within a trace.
    """
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)
    read_tools = [t["name"] for t in TOOL_CATALOG if t["cacheable"]]
    write_tools = [t["name"] for t in TOOL_CATALOG if not t["cacheable"]]
    ranks = np.arange(1, num_distinct_queries + 1, dtype=np.float64)
    probs = ranks ** (-zipf_alpha)
    probs /= probs.sum()
    tenants = [f"tenant-{chr(97 + k)}" for k in range(num_tenants)]
    sim_time = 1_700_000_000.0

    trace: List[Dict[str, Any]] = []
    for i in range(num_requests):
        sim_time += rng.uniform(1.2, 3.8)
        tenant_id = rng.choice(tenants)
        if rng.random() < 0.05:
            trace.append({"jsonrpc": "2.0", "id": i + 1, "method": "tools/list", "params": {}, "_tenant": tenant_id, "_sim_time": sim_time})
            continue
        if rng.random() < read_ratio:
            tname = rng.choice(read_tools)
            qid = int(np_rng.choice(num_distinct_queries, p=probs))
            args = {
                "kb_vector_search": {"query": f"mcp gateway architecture topic {qid}", "top_k": 5},
                "doc_metadata_lookup": {"doc_id": f"DOC-2026-{qid:04d}"},
                "code_symbol_index": {"symbol": f"resolve_token_{qid}", "repo": "core-gateway"},
                "sql_analytics_query": {"metric": "tool_latency_p99", "dimension": f"region_{qid}", "window_hours": 24},
                "feature_store_fetch": {"entity_id": f"agent_session_{qid:03d}", "feature_group": "rate_limit_stats"},
                "billing_cost_rollup": {"tenant_id": f"acct_{qid}", "period": "2026-10"},
                "k8s_pod_telemetry": {"cluster": "prod-us-east-1", "namespace": f"agent-pool-{qid}"},
                "slo_burn_rate_check": {"service_name": f"mcp-router-{qid}"},
            }[tname]
        else:
            tname = rng.choice(write_tools)
            uid = rng.randint(1, 10**6)
            args = {
                "log_trace_correlate": {"trace_id": f"tr-{uid:08x}", "service": "auth-proxy"},
                "jira_incident_create": {"title": f"Latency alert {uid}", "severity": "SEV-3"},
                "iam_temp_grant": {"principal": f"user_{uid % 50}", "role": "sre-reader", "duration_min": 15},
                "ci_pipeline_trigger": {"repo": "mcp-gateway-service", "branch": f"release-{uid % 20}"},
            }[tname]
        trace.append(
            {"jsonrpc": "2.0", "id": i + 1, "method": "tools/call", "params": {"name": tname, "arguments": args},
             "_tenant": tenant_id, "_sim_time": sim_time}
        )
    return trace


def make_gateway(cache_policy: str = "none", **kw) -> Tuple[MCPGatewayPipeline, Dict[str, str]]:
    gw = MCPGatewayPipeline(enable_cache=cache_policy != "none", cache_policy=cache_policy, **kw)
    tokens = {
        f"tenant-{chr(97 + k)}": gw.auth_engine.issue_client_token(
            f"agent@tenant-{chr(97 + k)}", "user", ["mcp:tools:*"], tenant_id=f"tenant-{chr(97 + k)}"
        )
        for k in range(8)
    }
    return gw, tokens


async def make_transport(kind: str, port: int, rtt_ms: float, payload_kb: float = 2.0):
    net = NetDelay(rtt_ms)
    t = {
        "T1": lambda: T1StdioBridge(IO_SCALE, payload_kb),
        "T2": lambda: T2LegacySSE(HOST, port, POOL, net),
        "T3": lambda: T3StreamableUnpooled(HOST, port, POOL, net),
        "T4": lambda: T4StreamablePooled(HOST, port, POOL, net),
    }[kind]()
    await t.start()
    return t


# --------------------------------------------------------------------------------------------- exp1
async def exp1_topologies() -> Dict[str, Any]:
    print("exp1: transport topologies")
    up = UpstreamProcess(IO_SCALE, 2.0)
    port = await up.start()
    configs = [("T1", "none"), ("T2", "none"), ("T3", "none"), ("T4", "none"), ("T4", "proposed_tiered")]
    results: Dict[str, Any] = {"rtts_ms": [int(r) if r == int(r) else r for r in EXP1_RTTS], "configs": {}, "session_setup": {}}

    for rtt in results["rtts_ms"]:
        for kind, pol in configs:
            if kind == "T1" and rtt > 0:
                continue
            label = f"{kind}{'+cache' if pol != 'none' else ''}"
            transport = await make_transport(kind, port, rtt)
            trials, upstream_ms, hits = [], [], []
            for seed in TRIAL_SEEDS:
                trace = generate_trace(160, zipf_alpha=1.0, num_tenants=4, seed=seed)
                gw, tokens = make_gateway(pol)
                for w in trace[:10]:
                    await gw.process_request(tokens[w["_tenant"]], w, transport.send, simulated_clock=w["_sim_time"])
                gw.cache_engine.hits = gw.cache_engine.misses = 0
                lat, ups = [], []
                for msg in trace[10:]:
                    _, m = await gw.process_request(tokens[msg["_tenant"]], msg, transport.send, simulated_clock=msg["_sim_time"])
                    lat.append(m.total_e2e_ms)
                    if not m.cache_hit:
                        ups.append(m.upstream_transport_ms)
                trials.append(lat)
                upstream_ms.append(float(np.mean(ups)))
                hits.append(100.0 * gw.cache_engine.hits / len(lat))
            await transport.stop()
            row = summarize(trials)
            row["upstream_leg_ms"], row["upstream_leg_sd"] = mean_sd(upstream_ms)
            row["hit_pct"], row["hit_sd"] = mean_sd(hits)
            row["samples"] = [round(x, 4) for t in trials for x in t]
            results["configs"].setdefault(label, {})[str(rtt)] = row
            print(f"  rtt={rtt:>2}ms {label:8s} mean={row['mean_ms']:.3f}+/-{row['mean_sd']:.3f} "
                  f"p50={row['p50_ms']:.3f} p99={row['p99_ms']:.3f} hit={row['hit_pct']:.1f}%")

    # Session establishment cost per topology (what a new agent session pays before its first call)
    for rtt in results["rtts_ms"]:
        setup: Dict[str, Any] = {}
        reps = 15
        samples: Dict[str, List[float]] = {"T1": [], "T2": [], "T3": [], "T4": []}
        for _ in range(reps):
            if rtt == 0:
                t0 = time.perf_counter()
                t1 = T1StdioBridge(IO_SCALE, 2.0)
                await t1.start()
                samples["T1"].append((time.perf_counter() - t0) * 1000)
                await t1.stop()
            t0 = time.perf_counter()
            t2 = T2LegacySSE(HOST, port, 1, NetDelay(rtt))
            await t2.start()
            samples["T2"].append((time.perf_counter() - t0) * 1000)
            await t2.stop()
            samples["T3"].append(0.0)
            t0 = time.perf_counter()
            t4 = T4StreamablePooled(HOST, port, 1, NetDelay(rtt))
            await t4.start()
            samples["T4"].append((time.perf_counter() - t0) * 1000)
            await t4.stop()
        for k, v in samples.items():
            if v:
                setup[k] = {"mean_ms": round(float(np.mean(v)), 3), "p50_ms": round(pct(v, 50), 3), "sd_ms": round(float(np.std(v, ddof=1)), 3)}
        results["session_setup"][str(rtt)] = setup
        print(f"  session setup rtt={rtt}: " + ", ".join(f"{k}={v['p50_ms']:.2f}ms" for k, v in setup.items()))

    await up.stop()
    return results


# --------------------------------------------------------------------------------------------- exp2
async def exp2_middleware() -> Dict[str, Any]:
    print("exp2: middleware stage breakdown vs payload size")
    results: Dict[str, Any] = {"payload_kb": [1, 4, 16, 64, 256], "rows": {}}
    for kb in results["payload_kb"]:
        up = UpstreamProcess(IO_SCALE * 0.2, float(kb))
        port = await up.start()
        transport = await make_transport("T4", port, 0, float(kb))
        pad = "lorem ipsum " * max(1, int(kb * 1024 / 12 / 2))
        stage_trials: Dict[str, List[float]] = {k: [] for k in ["auth", "rbac", "dpi_in", "cache", "dpi_out", "total"]}
        p99_trials: List[float] = []
        for seed in TRIAL_SEEDS:
            gw, tokens = make_gateway("proposed_tiered")
            tok = tokens["tenant-a"]
            per: Dict[str, List[float]] = {k: [] for k in stage_trials}
            for i in range(80):
                msg = {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                       "params": {"name": "kb_vector_search", "arguments": {"query": f"s={seed} i={i} {pad}", "top_k": 5}}}
                _, m = await gw.process_request(tok, msg, transport.send)
                if i < 5:
                    continue
                per["auth"].append(m.auth_exchange_ms)
                per["rbac"].append(m.rbac_schema_ms)
                per["dpi_in"].append(m.dpi_inbound_ms)
                per["cache"].append(m.cache_lookup_ms)
                per["dpi_out"].append(m.dpi_outbound_ms)
                per["total"].append(m.total_gateway_overhead_ms)
            for k in stage_trials:
                stage_trials[k].append(float(np.mean(per[k])))
            p99_trials.append(pct(per["total"], 99))
        row = {k: dict(zip(["mean_ms", "sd_ms"], mean_sd(v))) for k, v in stage_trials.items()}
        row["total_p99_ms"], row["total_p99_sd"] = mean_sd(p99_trials)
        results["rows"][f"{kb}"] = row
        print(f"  {kb:>3} KB: total={row['total']['mean_ms']:.3f}ms (p99 {row['total_p99_ms']:.3f}) "
              f"auth={row['auth']['mean_ms']:.3f} rbac={row['rbac']['mean_ms']:.3f} in={row['dpi_in']['mean_ms']:.3f} "
              f"cache={row['cache']['mean_ms']:.3f} out={row['dpi_out']['mean_ms']:.3f}")
        await transport.stop()
        await up.stop()
    return results


# --------------------------------------------------------------------------------------------- exp3
ABLATION = [
    ("A0", "Bare proxy", dict(enable_auth=False, enable_rbac=False, enable_dpi=False, enable_dpi_outbound=False), "none"),
    ("A1", "+M1 auth", dict(enable_auth=True, enable_rbac=False, enable_dpi=False, enable_dpi_outbound=False), "none"),
    ("A2", "+M2 RBAC/schema", dict(enable_auth=True, enable_rbac=True, enable_dpi=False, enable_dpi_outbound=False), "none"),
    ("A3", "+M3 inbound DPI", dict(enable_auth=True, enable_rbac=True, enable_dpi=True, enable_dpi_outbound=False), "none"),
    ("A4", "+M3 outbound DPI/DLP", dict(enable_auth=True, enable_rbac=True, enable_dpi=True, enable_dpi_outbound=True), "none"),
    ("A5", "+M4 proposed cache", dict(enable_auth=True, enable_rbac=True, enable_dpi=True, enable_dpi_outbound=True), "proposed_tiered"),
]


async def exp3_ablation() -> Dict[str, Any]:
    print("exp3: ablation (latency + measured security)")
    up = UpstreamProcess(IO_SCALE, 4.0)
    port = await up.start()
    transport = await make_transport("T4", port, 0, 4.0)
    results: Dict[str, Any] = {"configs": {}}
    for key, label, flags, pol in ABLATION:
        trials, overheads = [], []
        for seed in TRIAL_SEEDS:
            trace = generate_trace(150, zipf_alpha=1.0, seed=seed)
            gw, tokens = make_gateway(pol, **flags)
            lat, oh = [], []
            for msg in trace:
                _, m = await gw.process_request(tokens[msg["_tenant"]], msg, transport.send, simulated_clock=msg["_sim_time"])
                lat.append(m.total_e2e_ms)
                oh.append(m.total_gateway_overhead_ms)
            trials.append(lat)
            overheads.append(float(np.mean(oh)))
        row = summarize(trials)
        row["overhead_ms"], row["overhead_sd"] = mean_sd(overheads)
        sec = await security_suite.evaluate(lambda: make_gateway(pol, **flags)[0], transport.send)
        row["label"] = label
        row["security"] = sec
        results["configs"][key] = row
        print(f"  {key} {label:22s} mean={row['mean_ms']:.3f}ms overhead={row['overhead_ms']:.4f}ms "
              f"recall={sec['recall_pct']:.1f}% FPR={sec['fpr_pct']:.1f}% precision={sec['precision_pct']:.1f}%")
    await transport.stop()
    await up.stop()
    return results


# --------------------------------------------------------------------------------------------- exp4
async def exp4_cache() -> Dict[str, Any]:
    print("exp4: cache policy comparison")
    up = UpstreamProcess(IO_SCALE, 2.0)
    port = await up.start()
    transport = await make_transport("T4", port, 0)
    alphas = [0.6, 0.8, 1.0, 1.2, 1.4]
    policies = ["none", "global_fifo", "global_lru", "tenant_lru", "tenant_ttl_lru", "proposed_tiered"]
    results: Dict[str, Any] = {"alphas": alphas, "policies": {p: [] for p in policies}, "capacity": 64}
    for alpha in alphas:
        for pol in policies:
            acc: Dict[str, List[float]] = {k: [] for k in ["hit", "lat", "leak", "stale", "saved"]}
            for seed in TRIAL_SEEDS:
                trace = generate_trace(400, zipf_alpha=alpha, read_ratio=0.8, num_distinct_queries=40, num_tenants=4,
                                       seed=seed + int(alpha * 100))
                gw, tokens = make_gateway(pol, cache_max_entries=results["capacity"])
                lat, leaks, saved_ms, total_ms = [], 0, 0.0, 0.0
                for msg in trace:
                    resp, m = await gw.process_request(tokens[msg["_tenant"]], msg, transport.send, simulated_clock=msg["_sim_time"])
                    lat.append(m.total_e2e_ms)
                    served_tenant = ((resp.get("result") or {}).get("_meta") or {}).get("tenant")
                    if served_tenant is not None and served_tenant != msg["_tenant"]:
                        leaks += 1
                    if msg["method"] == "tools/call":
                        cost = TOOL_MAP[msg["params"]["name"]]["base_exec_ms"]
                        total_ms += cost
                        saved_ms += cost if m.cache_hit else 0.0
                hits = gw.cache_engine.hits
                acc["hit"].append(100.0 * hits / len(trace))
                acc["lat"].append(float(np.mean(lat)))
                acc["leak"].append(100.0 * leaks / len(trace))
                acc["stale"].append(100.0 * gw.cache_engine.stale_reads / len(trace))
                acc["saved"].append(100.0 * saved_ms / total_ms)
            row = {"alpha": alpha}
            for k in acc:
                row[f"{k}_mean"], row[f"{k}_sd"] = mean_sd(acc[k])
            results["policies"][pol].append(row)
            print(f"  a={alpha} {pol:16s} hit={row['hit_mean']:.1f}% saved={row['saved_mean']:.1f}% lat={row['lat_mean']:.3f}ms "
                  f"leak={row['leak_mean']:.2f}% stale={row['stale_mean']:.2f}% (of all requests)")
    await transport.stop()
    await up.stop()
    return results


# --------------------------------------------------------------------------------------------- exp5
def bounds_model(n: int, r1_ms: float, dcpu_ms: float, hold_ms: float, c: int) -> Tuple[float, float]:
    """
    Asymptotic bound analysis for a closed system of N agent sessions with zero think time.
    Throughput X(N) <= min(N / R1, 1 / D_cpu, c / S_hold): the population bound, the single-threaded
    gateway CPU bound, and the connection-pool bound (c connections each held S_hold per call).
    Response time follows from Little's law, R(N) = N / X(N).
    """
    caps = [n / r1_ms, 1.0 / dcpu_ms]
    if hold_ms > 0:
        caps.append(c / hold_ms)
    x_per_ms = min(caps)
    return x_per_ms * 1000.0, n / x_per_ms


async def exp5_concurrency() -> Dict[str, Any]:
    print("exp5: closed-loop concurrency + bounds model")
    rtt = EXP5_RTT
    levels = EXP5_LEVELS
    up = UpstreamProcess(IO_SCALE, 2.0)
    port = await up.start()
    configs = [("T2", "none"), ("T3", "none"), ("T4", "none"), ("T4", "proposed_tiered")]
    results: Dict[str, Any] = {"levels": levels, "rtt_ms": rtt, "pool": POOL, "series": {}}

    async def run_level(transport, pol: str, n: int, seed: int, trace_mem: bool = False) -> Dict[str, float]:
        total = max(320, EXP5_CALLS_PER_SESSION * n)
        trace = generate_trace(total + 40, zipf_alpha=1.0, num_tenants=4, seed=seed + n)
        gw, tokens = make_gateway(pol, cache_max_entries=256)
        for w in trace[:40]:
            await gw.process_request(tokens[w["_tenant"]], w, transport.send, simulated_clock=w["_sim_time"])
        work = trace[40:]
        lat: List[float] = []
        done_at: List[float] = []
        session_end: List[float] = []
        if trace_mem:
            gc.collect()
            tracemalloc.start()
            base = tracemalloc.get_traced_memory()[0]

        async def session(k: int) -> None:
            for msg in work[k::n]:
                _, m = await gw.process_request(tokens[msg["_tenant"]], msg, transport.send, simulated_clock=msg["_sim_time"])
                lat.append(m.total_e2e_ms)
                done_at.append(time.perf_counter())
            session_end.append(time.perf_counter())

        transport.reset_hold()
        c0, t0 = time.process_time(), time.perf_counter()
        await asyncio.gather(*(session(k) for k in range(n)))
        wall, cpu = time.perf_counter() - t0, time.process_time() - c0
        first_end = min(session_end)
        rps_total = len(work) / wall
        rps_steady = sum(1 for t in done_at if t <= first_end) / (first_end - t0)
        out = {"rps": rps_steady if EXP5_STEADY_X else rps_total, "rps_total": rps_total, "rps_steady": rps_steady,
               "mean": float(np.mean(lat)), "p50": pct(lat, 50), "p99": pct(lat, 99),
               "cpu_ms_per_req": 1000.0 * cpu / len(work), "hold_ms": transport.mean_hold_ms(),
               "hit_pct": 100.0 * gw.cache_engine.hits / len(work)}
        if trace_mem:
            out["heap_peak_kb"] = (tracemalloc.get_traced_memory()[1] - base) / 1024.0
            tracemalloc.stop()
        return out

    for kind, pol in configs:
        label = f"{kind}{'+cache' if pol != 'none' else ''}"
        transport = await make_transport(kind, port, rtt)
        pts = []
        for n in levels:
            runs = [await run_level(transport, pol, n, seed) for seed in TRIAL_SEEDS[:3]]
            mem = await run_level(transport, pol, n, 999, trace_mem=True)
            pt: Dict[str, Any] = {"n": n}
            for k in ["rps", "rps_total", "rps_steady", "mean", "p50", "p99", "cpu_ms_per_req", "hold_ms", "hit_pct"]:
                pt[k], pt[f"{k}_sd"] = mean_sd([r[k] for r in runs])
            pt["heap_peak_kb"] = round(mem["heap_peak_kb"], 1)
            pts.append(pt)
            print(f"  {label:8s} N={n:>3} X={pt['rps']:.0f}/s mean={pt['mean']:.2f}ms p99={pt['p99']:.2f}ms "
                  f"cpu={pt['cpu_ms_per_req']:.3f}ms/req hold={pt['hold_ms']:.2f}ms heap={pt['heap_peak_kb']:.0f}KB")
        await transport.stop()

        # Model parameters come from measurements other than the predicted quantities: R1 is the
        # N=1 response time, S_hold the N=1 connection hold time (transport accounting), and D_cpu
        # the CPU time per call from process CPU accounting. Per-call CPU falls with load because
        # event-loop wake-ups are amortized over more ready events, so we report the model with
        # D_cpu taken at N=1 and at the largest N.
        r1, hold = pts[0]["mean"], pts[0]["hold_ms"]
        variants = {"dcpu_at_n1": pts[0]["cpu_ms_per_req"], "dcpu_at_nmax": pts[-1]["cpu_ms_per_req"]}
        meas_x = np.array([p["rps"] for p in pts])
        meas_r = np.array([p["mean"] for p in pts])
        series: Dict[str, Any] = {"points": pts, "models": {}}
        for vname, dcpu in variants.items():
            model = []
            for pt in pts:
                x, r = bounds_model(pt["n"], r1, dcpu, hold, POOL)
                model.append({"n": pt["n"], "rps": round(x, 2), "mean": round(r, 3)})
            mod_x = np.array([m["rps"] for m in model])
            mod_r = np.array([m["mean"] for m in model])
            bottleneck = "cpu" if dcpu >= hold / POOL else "pool"
            series["models"][vname] = {
                "points": model,
                "params": {"R1_ms": r1, "D_cpu_ms": dcpu, "S_hold_ms": hold, "c": POOL, "bottleneck": bottleneck,
                           "knee_N": round(r1 / max(dcpu, hold / POOL), 1)},
                "mape_throughput_pct": round(float(np.mean(np.abs(mod_x - meas_x) / meas_x)) * 100, 2),
                "mape_latency_pct": round(float(np.mean(np.abs(mod_r - meas_r) / meas_r)) * 100, 2),
            }
            print(f"  {label} [{vname}]: R1={r1:.2f} Dcpu={dcpu:.3f} Shold={hold:.2f} bottleneck={bottleneck} "
                  f"knee N*={series['models'][vname]['params']['knee_N']} -> "
                  f"MAPE X={series['models'][vname]['mape_throughput_pct']}% R={series['models'][vname]['mape_latency_pct']}%")
        results["series"][label] = series
    await up.stop()
    return results


# --------------------------------------------------------------------------------------------- exp6
PRICES = {
    "source": "AWS public on-demand list prices, us-east-1 (Linux, x86)",
    "lambda_per_m_requests": 0.20,
    "lambda_per_gb_s": 0.0000166667,
    "lambda_memory_gb": 0.5,
    "fargate_vcpu_hour": 0.04048,
    "fargate_gb_hour": 0.004445,
    "fargate_task_vcpu": 1,
    "fargate_task_gb": 2,
    "ec2_m6i_large_hour": 0.096,
    "ec2_m6i_large_vcpu": 2,
    "alb_hour": 0.0225,
    "hours_per_month": 730,
    "target_utilization": 0.6,
    "min_replicas": 2,
}


def exp6_cost(exp1: Dict[str, Any], exp4: Dict[str, Any], exp5: Dict[str, Any]) -> Dict[str, Any]:
    print("exp6: analytical cost model")
    p = PRICES
    sec_per_month = p["hours_per_month"] * 3600

    # Measured inputs
    # 2 ms emulated RTT in local runs; the single (real-network) setting in multi-host runs.
    rk = "2" if "2" in exp1["configs"]["T4"] else next(iter(exp1["configs"]["T4"]))
    lambda_ms = exp1["configs"]["T4"][rk]["mean_ms"]
    lambda_cached_ms = exp1["configs"]["T4+cache"][rk]["mean_ms"]
    pt_unc = next(x for x in exp5["series"]["T4"]["points"] if x["n"] == 32)
    pt_c = next(x for x in exp5["series"]["T4+cache"]["points"] if x["n"] == 32)
    cpu_unc, cpu_c = pt_unc["cpu_ms_per_req"], pt_c["cpu_ms_per_req"]
    hit = next(r for r in exp4["policies"]["proposed_tiered"] if r["alpha"] == 1.0)["hit_mean"] / 100.0

    def per_vcpu_month(cpu_ms: float) -> float:
        return p["target_utilization"] * sec_per_month * 1000.0 / cpu_ms / 1e6  # millions of requests

    cap_vcpu_unc, cap_vcpu_c = per_vcpu_month(cpu_unc), per_vcpu_month(cpu_c)
    fg_task_month = (p["fargate_vcpu_hour"] * p["fargate_task_vcpu"] + p["fargate_gb_hour"] * p["fargate_task_gb"]) * p["hours_per_month"]
    ec2_month = p["ec2_m6i_large_hour"] * p["hours_per_month"]
    alb_month = p["alb_hour"] * p["hours_per_month"]

    def lam(v: float, dur_ms: float) -> float:
        return v * p["lambda_per_m_requests"] + v * 1e6 * math.ceil(dur_ms) / 1000.0 * p["lambda_memory_gb"] * p["lambda_per_gb_s"]

    def fargate(v: float) -> float:
        return max(p["min_replicas"], math.ceil(v / (cap_vcpu_unc * p["fargate_task_vcpu"]))) * fg_task_month + alb_month

    def ec2(v: float, cap_vcpu: float) -> float:
        return max(p["min_replicas"], math.ceil(v / (cap_vcpu * p["ec2_m6i_large_vcpu"]))) * ec2_month + alb_month

    volumes = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000]
    curves = {
        "D1_Lambda": [round(lam(v, lambda_ms), 2) for v in volumes],
        "D1c_Lambda_cached": [round(lam(v, lambda_cached_ms), 2) for v in volumes],
        "D2_Fargate": [round(fargate(v), 2) for v in volumes],
        "D3_EC2": [round(ec2(v, cap_vcpu_unc), 2) for v in volumes],
        "D4_EC2_cached": [round(ec2(v, cap_vcpu_c), 2) for v in volumes],
    }
    # Break-even between Lambda (uncached) and each always-on option, found on a fine grid
    grid = np.linspace(0.1, 1000, 100000)
    be = next((float(v) for v in grid if lam(v, lambda_ms) > ec2(v, cap_vcpu_c)), None)
    be_fg = next((float(v) for v in grid if lam(v, lambda_ms) > fargate(v)), None)

    # Lambda bills wall-clock time, including time spent waiting for the upstream tool. Billed
    # duration for an upstream tool latency L: measured gateway-side time (e2e minus upstream leg)
    # plus L plus one network RTT.
    t4 = exp1["configs"]["T4"][rk]
    gw_side_ms = t4["mean_ms"] - t4["upstream_leg_ms"]
    sens = []
    for tool_ms in [5, 10, 20, 50, 100, 200, 500, 1000]:
        dur = gw_side_ms + tool_ms + 2.0
        fine = np.linspace(0.01, 1000, 100000)
        be_l = next((float(v) for v in fine if lam(v, dur) > ec2(v, cap_vcpu_c)), None)
        be_lf = next((float(v) for v in fine if lam(v, dur) > fargate(v)), None)
        sens.append({"tool_ms": tool_ms, "billed_ms": math.ceil(dur), "lambda_usd_per_m": round(lam(1, dur), 4),
                     "breakeven_m": round(be_l, 2) if be_l else None,
                     "breakeven_fargate_m": round(be_lf, 2) if be_lf else None})
    out = {
        "prices": p,
        "inputs": {
            "lambda_billed_ms_uncached": math.ceil(lambda_ms),
            "lambda_billed_ms_cached": math.ceil(lambda_cached_ms),
            "cpu_ms_per_req_uncached": cpu_unc,
            "cpu_ms_per_req_cached": cpu_c,
            "capacity_m_per_vcpu_month_uncached": round(cap_vcpu_unc, 1),
            "capacity_m_per_vcpu_month_cached": round(cap_vcpu_c, 1),
            "cache_hit_ratio": round(hit, 4),
            "fargate_task_month": round(fg_task_month, 2),
            "ec2_month": round(ec2_month, 2),
            "alb_month": round(alb_month, 2),
        },
        "volumes_m": volumes,
        "curves": curves,
        "upstream_calls_avoided_m": [round(v * hit, 2) for v in volumes],
        "breakeven_m_lambda_vs_ec2_cached": round(be, 2) if be else None,
        "breakeven_m_lambda_vs_fargate": round(be_fg, 2) if be_fg else None,
        "gateway_side_ms": round(gw_side_ms, 3),
        "tool_latency_sensitivity": sens,
    }
    print(f"  inputs: {out['inputs']}")
    print(f"  sensitivity: {[(s['tool_ms'], s['breakeven_m']) for s in sens]}")
    print(f"  break-even Lambda vs Fargate: {out['breakeven_m_lambda_vs_fargate']}, "
          f"vs EC2+cache: {out['breakeven_m_lambda_vs_ec2_cached']} M req/month")
    return out


# --------------------------------------------------------------------------------------------- main
async def measure_tcp_rtt(samples: int = 200) -> Dict[str, float]:
    """TCP connect time to the upstream agent (one SYN/SYN-ACK round trip) as an RTT estimate."""
    host, port = os.environ["MCP_UPSTREAM_AGENT"].rsplit(":", 1)
    vals = []
    for _ in range(samples):
        t0 = time.perf_counter()
        _, w = await asyncio.open_connection(host, int(port))
        vals.append((time.perf_counter() - t0) * 1000)
        w.close()
        await w.wait_closed()
    return {"p50_ms": round(pct(vals, 50), 4), "mean_ms": round(float(np.mean(vals)), 4), "p99_ms": round(pct(vals, 99), 4)}


async def main(selected: List[str]) -> None:
    timer_ok = enable_high_res_timer()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    data: Dict[str, Any] = {}
    if os.path.exists(OUT_PATH) and selected != ["all"]:
        with open(OUT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if "exp1" not in data:
            data = {}

    t0 = time.perf_counter()
    runners = {"exp1": exp1_topologies, "exp2": exp2_middleware, "exp3": exp3_ablation, "exp4": exp4_cache, "exp5": exp5_concurrency}
    for name, fn in runners.items():
        if selected == ["all"] or name in selected:
            data[name] = await fn()
    if all(k in data for k in ("exp1", "exp4", "exp5")) and (selected == ["all"] or "exp6" in selected or any(k in selected for k in ("exp1", "exp4", "exp5"))):
        data["exp6"] = exp6_cost(data["exp1"], data["exp4"], data["exp5"])

    data["testbed"] = {
        "cpu": os.environ.get("MCP_TESTBED_CPU", "Intel Core i7-14700HX (20 cores / 28 threads)"),
        "ram_gb": float(os.environ.get("MCP_TESTBED_RAM_GB", "32")),
        "label": os.environ.get("MCP_TESTBED_LABEL", "local"),
        "upstream_host": HOST,
        "network_rtt_ms": await measure_tcp_rtt() if HOST != "127.0.0.1" else None,
        "tls": os.environ.get("MCP_TLS") == "1",
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "timer_1ms": timer_ok,
        "trial_seeds": TRIAL_SEEDS,
        "io_scale": IO_SCALE,
        "pool": POOL,
        "exp5_calls_per_session": EXP5_CALLS_PER_SESSION,
        "exp5_steady_x": EXP5_STEADY_X,
    }
    data.setdefault("wall_time_s", {})
    data["wall_time_s"]["last_run"] = round(time.perf_counter() - t0, 1)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    print(f"done in {data['wall_time_s']['last_run']} s -> {OUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["all"]))
