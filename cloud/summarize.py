"""Print the headline numbers of each AWS result file in mcp_cloud_proxy/results/aws."""

import glob
import json
import os

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcp_cloud_proxy", "results", "aws")

for path in sorted(glob.glob(os.path.join(RESULTS, "aws_*.json"))):
    d = json.load(open(path, encoding="utf-8"))
    tb = d.get("testbed", {})
    print(f"{os.path.basename(path)}  [{tb.get('label')}, tls={tb.get('tls')}, rtt p50={(tb.get('network_rtt_ms') or {}).get('p50_ms')} ms]")
    if "exp1" in d:
        means = {k: v["0"]["mean_ms"] for k, v in d["exp1"]["configs"].items()}
        setup = {k: v["p50_ms"] for k, v in d["exp1"]["session_setup"]["0"].items()}
        print("  exp1 mean ms:", means)
        print("  session setup p50 ms:", setup)
    for k, s in d.get("exp5", {}).get("series", {}).items():
        pts, m = s["points"], s["models"]["dcpu_at_nmax"]
        print(f"  exp5 {k:9s} Xmax={max(p['rps'] for p in pts):6.0f}/s  p99@N={pts[-1]['n']}: {pts[-1]['p99']:6.0f} ms  "
              f"cpu/call={pts[-1]['cpu_ms_per_req']:.3f} ms  hold={pts[0]['hold_ms']:.2f} ms  "
              f"bottleneck={m['params']['bottleneck']}  MAPE_X={m['mape_throughput_pct']}%")
    if "exp6" in d:
        print("  exp6 breakeven M/month:", d["exp6"]["breakeven_m_lambda_vs_ec2_cached"])
