"""
Writes LaTeX tables and number macros for the paper from results/benchmark_metrics.json, so that
every number quoted in the manuscript is generated from the measurement data.
Output: paper/generated/macros.tex and paper/generated/tab_*.tex
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "paper", "generated")
os.makedirs(OUT, exist_ok=True)
with open(os.path.join(HERE, "results", "benchmark_metrics.json"), encoding="utf-8") as f:
    D = json.load(f)

macros = {}


def m(name, value, fmt="{:.2f}"):
    macros[name] = fmt.format(value) if not isinstance(value, str) else value


def write(name, text):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        f.write(text)


# ------------------------------------------------------------------ exp1
e1 = D["exp1"]
C = e1["configs"]
for lab, key in [("T1", "Tone"), ("T2", "Ttwo"), ("T3", "Tthree"), ("T4", "Tfour"), ("T4+cache", "Tcache")]:
    for r, rk in [("0", "Rzero"), ("2", "Rtwo"), ("10", "Rten")]:
        if r in C[lab]:
            m(f"e{key}{rk}", C[lab][r]["mean_ms"])
            m(f"e{key}{rk}Pnn", C[lab][r]["p99_ms"])
m("eTthreeOverTfourRten", C["T3"]["10"]["mean_ms"] / C["T4"]["10"]["mean_ms"])
m("eTthreeOverTfourRtwo", C["T3"]["2"]["mean_ms"] / C["T4"]["2"]["mean_ms"])
m("eTthreeMinusTfourRzero", C["T3"]["0"]["mean_ms"] - C["T4"]["0"]["mean_ms"])
m("eTthreeMinusTfourRten", C["T3"]["10"]["mean_ms"] - C["T4"]["10"]["mean_ms"])
m("eCacheHit", C["T4+cache"]["0"]["hit_pct"], "{:.1f}")
S = e1["session_setup"]
m("sTone", S["0"]["T1"]["p50_ms"], "{:.0f}")
m("sTtwoRzero", S["0"]["T2"]["p50_ms"])
m("sTfourRzero", S["0"]["T4"]["p50_ms"])
m("sTtwoRten", S["10"]["T2"]["p50_ms"], "{:.1f}")
m("sTfourRten", S["10"]["T4"]["p50_ms"], "{:.1f}")

rows = []
for lab, name in [("T1", "T1 stdio"), ("T2", "T2 HTTP+SSE"), ("T3", "T3 Str.\\ HTTP, unpooled"), ("T4", "T4 Str.\\ HTTP, pooled"), ("T4+cache", "T4 + cache")]:
    cells = []
    for r in ["0", "2", "10"]:
        if r in C[lab]:
            x = C[lab][r]
            cells.append(f"{x['mean_ms']:.2f} $\\pm$ {x['mean_ci95']:.2f} & {x['p99_ms']:.1f}")
        else:
            cells.append("-- & --")
    setup = S["0"].get(lab.replace("+cache", ""), {}).get("p50_ms")
    setup10 = S["10"].get(lab.replace("+cache", ""), {}).get("p50_ms")
    st = "--" if lab == "T3" else (f"{setup:.1f}" if setup is not None else "--")
    st10 = "--" if lab in ("T3", "T1") else (f"{setup10:.1f}" if setup10 is not None else "--")
    if lab == "T3":
        st = st10 = "per call"
    if lab == "T4+cache":
        st = st10 = "as T4"
    rows.append(f"{name} & " + " & ".join(cells) + f" & {st} & {st10} \\\\")
write("tab_transports.tex", "\n".join([
    "\\begin{table*}[t]",
    "\\centering",
    "\\caption{Sequential end-to-end latency per tool call (ms) by transport and emulated RTT: mean $\\pm$ 95\\% CI over five trials, and P99 of the per-trial distribution. Session setup is the median time to establish a new agent session before its first call.}",
    "\\label{tab:transports}",
    "\\begin{tabular}{l rr rr rr rr}",
    "\\toprule",
    " & \\multicolumn{2}{c}{RTT = 0 ms (loopback)} & \\multicolumn{2}{c}{RTT = 2 ms} & \\multicolumn{2}{c}{RTT = 10 ms} & \\multicolumn{2}{c}{Session setup (ms)} \\\\",
    "\\cmidrule(lr){2-3}\\cmidrule(lr){4-5}\\cmidrule(lr){6-7}\\cmidrule(lr){8-9}",
    "Transport & mean & P99 & mean & P99 & mean & P99 & RTT 0 & RTT 10 \\\\",
    "\\midrule",
    *rows,
    "\\bottomrule",
    "\\end{tabular}",
    "\\end{table*}",
    "",
]))

# ------------------------------------------------------------------ exp2
e2 = D["exp2"]
R2 = e2["rows"]
_aws_same = os.path.join(HERE, "results", "aws", "aws_same_az.json")
R2a = None
if os.path.exists(_aws_same):
    with open(_aws_same, encoding="utf-8") as f:
        R2a = json.load(f).get("exp2", {}).get("rows")
rows = []
for kb in e2["payload_kb"]:
    r = R2[str(kb)]
    ec2 = f" & {R2a[str(kb)]['total']['mean_ms']:.3f}" if R2a else ""
    rows.append(f"{kb} & {r['auth']['mean_ms']:.3f} & {r['rbac']['mean_ms']:.3f} & {r['dpi_in']['mean_ms']:.3f} & "
                f"{r['cache']['mean_ms']:.3f} & {r['dpi_out']['mean_ms']:.3f} & {r['total']['mean_ms']:.3f} & {r['total_p99_ms']:.3f}{ec2} \\\\")
write("tab_middleware.tex", "\n".join([
    "\\begin{table}[t]",
    "\\centering",
    "\\caption{Gateway processing time per stage (ms, mean of five trials of 75 calls) versus payload size on the single host, and total time on EC2 in one availability zone. Request and response bodies are both scaled to the given size.}",
    "\\label{tab:middleware}",
    "\\small",
    "\\setlength{\\tabcolsep}{2.2pt}",
    "\\begin{tabular}{r rrrrr rr" + (" r" if R2a else "") + "}",
    "\\toprule",
    (" & \\multicolumn{7}{c}{Single host} & EC2 \\\\" if R2a else ""),
    ("\\cmidrule(lr){2-8}\\cmidrule(lr){9-9}" if R2a else ""),
    "KB & M1 & M2 & M3-in & M4 & M3-out & Total & P99" + (" & Total" if R2a else "") + " \\\\",
    "\\midrule",
    *rows,
    "\\bottomrule",
    "\\end{tabular}",
    "\\end{table}",
    "",
]))
m("mOneKB", R2["1"]["total"]["mean_ms"], "{:.2f}")
m("mBigKB", R2[str(e2["payload_kb"][-1])]["total"]["mean_ms"], "{:.1f}")
m("mBigSize", e2["payload_kb"][-1], "{:d}")
big = R2[str(e2["payload_kb"][-1])]
m("mDpiShareBig", 100 * (big["dpi_in"]["mean_ms"] + big["dpi_out"]["mean_ms"]) / big["total"]["mean_ms"], "{:.0f}")
m("mAuthMax", max(R2[str(k)]["auth"]["mean_ms"] for k in e2["payload_kb"]), "{:.3f}")
m("mRbacMax", max(R2[str(k)]["rbac"]["mean_ms"] for k in e2["payload_kb"]), "{:.3f}")
m("mPerKB", (big["total"]["mean_ms"] - R2["1"]["total"]["mean_ms"]) / (e2["payload_kb"][-1] - 1) * 1000, "{:.0f}")
_wc = os.path.join(HERE, "results", "inspection_worst_case.json")
if os.path.exists(_wc):
    with open(_wc, encoding="utf-8") as f:
        wc_rows = json.load(f)["rows"]
    for name, r in zip(["Small", "Mid", "Big"], wc_rows):
        ms = r["crafted_ms"]
        m(f"wcKb{name}", r["kb"], "{:d}")
        m(f"wcCrafted{name}", f"{ms:.0f}\\ms" if ms < 1000 else f"{ms / 1000:.1f}\\,s", "{}")
if R2a:
    bga = R2a[str(e2["payload_kb"][-1])]
    m("awMidPerKB", (bga["total"]["mean_ms"] - R2a["1"]["total"]["mean_ms"]) / (e2["payload_kb"][-1] - 1) * 1000, "{:.0f}")
    m("awMidDpiShareBig", 100 * (bga["dpi_in"]["mean_ms"] + bga["dpi_out"]["mean_ms"]) / bga["total"]["mean_ms"], "{:.0f}")
    m("awMidAuthMax", max(R2a[str(k)]["auth"]["mean_ms"] for k in e2["payload_kb"]), "{:.3f}")
    m("awMidRbacMax", max(R2a[str(k)]["rbac"]["mean_ms"] for k in e2["payload_kb"]), "{:.3f}")

# ------------------------------------------------------------------ exp3 + security
e3 = D["exp3"]["configs"]


def subset_rate(sec, subset):
    rows_ = [v for k, v in sec["per_subset"].items() if k.endswith("/" + subset) and not k.startswith("B_")]
    return 100.0 * sum(r["flagged"] for r in rows_) / sum(r["total"] for r in rows_)


e3a = None
if os.path.exists(_aws_same):
    with open(_aws_same, encoding="utf-8") as f:
        e3a = json.load(f).get("exp3", {}).get("configs")
rows = []
for k, row in e3.items():
    sec = row["security"]
    prec = "--" if sec["tp"] + sec["fp"] == 0 else f"{sec['precision_pct']:.1f}"
    ec2 = f" & {e3a[k]['overhead_ms']:.3f}" if e3a else ""
    rows.append(f"{k} & {row['label']} & {row['overhead_ms']:.3f}{ec2} & {row['mean_ms']:.2f} $\\pm$ {row['mean_ci95']:.2f} & "
                f"{subset_rate(sec, 'signature'):.1f} & {subset_rate(sec, 'evasive'):.1f} & {sec['recall_pct']:.1f} & {sec['fpr_pct']:.1f} & {prec} \\\\")
write("tab_ablation.tex", "\n".join([
    "\\begin{table*}[t]",
    "\\centering",
    "\\caption{Ablation. Overhead is time spent in gateway stages on the single host and on EC2 in one availability zone; end-to-end latency is the single host's mean $\\pm$ 95\\% CI (five trials of 150 calls, T4 transport, small requests and 4 KB responses). Detection metrics are measured by running the full security suite (240 malicious, 170 benign cases) against each configuration and are identical on both testbeds.}",
    "\\label{tab:ablation}",
    "\\begin{tabular}{ll r" + (" r" if e3a else "") + " r rrr rr}",
    "\\toprule",
    " & & \\multicolumn{" + ("2" if e3a else "1") + "}{c}{Overhead (ms)} & End-to-end & \\multicolumn{3}{c}{Recall (\\%)} & FPR & Precision \\\\",
    "\\cmidrule(lr){3-" + ("4" if e3a else "3") + "}\\cmidrule(lr){" + ("6-8" if e3a else "5-7") + "}",
    "Config & Stages added & " + ("single host & EC2" if e3a else "") + " & latency (ms) & signature & evasive & all & (\\%) & (\\%) \\\\",
    "\\midrule",
    *rows,
    "\\bottomrule",
    "\\end{tabular}",
    "\\end{table*}",
    "",
]))
full = e3["A4"]["security"]
m("secRecall", full["recall_pct"], "{:.1f}")
m("secFPR", full["fpr_pct"], "{:.1f}")
m("secPrecision", full["precision_pct"], "{:.1f}")
m("secSig", subset_rate(full, "signature"), "{:.1f}")
m("secEva", subset_rate(full, "evasive"), "{:.1f}")
m("secMal", full["n_malicious"], "{:d}")
m("secBen", full["n_benign"], "{:d}")
m("abOverheadFull", e3["A4"]["overhead_ms"], "{:.2f}")
m("abOverheadAuth", e3["A1"]["overhead_ms"], "{:.3f}")
m("abOverheadRbac", e3["A2"]["overhead_ms"] - e3["A1"]["overhead_ms"], "{:.3f}")
m("abOverheadIn", e3["A3"]["overhead_ms"] - e3["A2"]["overhead_ms"], "{:.3f}")
m("abOverheadOut", e3["A4"]["overhead_ms"] - e3["A3"]["overhead_ms"], "{:.3f}")
m("abEtoEzero", e3["A0"]["mean_ms"])
m("abEtoEfour", e3["A4"]["mean_ms"])
m("abEtoEDiff", e3["A4"]["mean_ms"] - e3["A0"]["mean_ms"])
m("abOverheadCache", e3["A5"]["overhead_ms"], "{:.2f}")
if e3a:
    m("awAbOverheadFull", e3a["A4"]["overhead_ms"], "{:.2f}")
    m("awAbOverheadAuth", e3a["A1"]["overhead_ms"], "{:.3f}")
_ig = os.path.join(HERE, "results", "idle_gap_m1.json")
if os.path.exists(_ig):
    with open(_ig, encoding="utf-8") as f:
        ig = json.load(f)["m1_ms"]
    m("igMone", ig["none"], "{:.3f}")
    m("igMmid", ig["middleware"], "{:.3f}")
    m("igMabl", ig["ablation"], "{:.3f}")
sv = full["per_subset"].get("B_Benign/security_vocab", {"flagged": 0, "total": 1})
od = full["per_subset"].get("B_Benign/ordinary", {"flagged": 0, "total": 1})
m("secVocabFP", 100.0 * sv["flagged"] / sv["total"], "{:.0f}")
m("secOrdFP", 100.0 * od["flagged"] / od["total"], "{:.0f}")

cat_names = [("C1_Auth", "C1 Authentication \\& delegation"), ("C2_AuthZ", "C2 Authorization \\& schema"),
             ("C3_Injection", "C3 Prompt injection (args)"), ("C4_SSRF_Cmd_SQL", "C4 SSRF / command / SQL"),
             ("C5_Outbound_Poisoning", "C5 Poisoned tool output"), ("C6_Outbound_DLP", "C6 Secrets / PII in output")]
rows = []
for k, name in cat_names:
    sig = full["per_subset"].get(f"{k}/signature")
    eva = full["per_subset"].get(f"{k}/evasive")
    fs = f"{sig['flagged']}/{sig['total']}" if sig else "--"
    fe = f"{eva['flagged']}/{eva['total']}" if eva else "--"
    rows.append(f"{name} & {fs} & {fe} \\\\")
rows.append("\\midrule")
rows.append(f"Benign, ordinary tool calls & \\multicolumn{{2}}{{c}}{{{od['flagged']}/{od['total']} flagged}} \\\\")
rows.append(f"Benign, security vocabulary & \\multicolumn{{2}}{{c}}{{{sv['flagged']}/{sv['total']} flagged}} \\\\")
write("tab_security.tex", "\n".join([
    "\\begin{table}[t]",
    "\\centering",
    "\\caption{Security conformance of the full gateway (A4): cases flagged / cases per category. C1 and C2 contain only deterministic credential and policy violations, so they have no evasive subset.}",
    "\\label{tab:security}",
    "\\setlength{\\tabcolsep}{4pt}",
    "\\begin{tabular}{l cc}",
    "\\toprule",
    "Category & Signature & Evasive \\\\",
    "\\midrule",
    *rows,
    "\\bottomrule",
    "\\end{tabular}",
    "\\end{table}",
    "",
]))

# ------------------------------------------------------------------ public datasets (external_eval.py)
with open(os.path.join(HERE, "results", "external_eval.json"), encoding="utf-8") as f:
    X = json.load(f)
xia, xds, xni = X["injecagent"], X["deepset"], X["notinject"]
ia_blocked = xia["base"]["outbound_blocked"] + xia["enhanced"]["outbound_blocked"]
ds_tp = xds["inbound_detected"]
m("extIaBase", xia["base"]["n"], "{:,d}")
m("extIaEnh", xia["enhanced"]["n"], "{:,d}")
m("extIaN", xia["base"]["n"] + xia["enhanced"]["n"], "{:,d}")
m("extIaBlocked", ia_blocked, "{:d}")
m("extIaAllBlocked", xia["base"]["all_rules_blocked"] + xia["enhanced"]["all_rules_blocked"], "{:d}")
m("extDsMal", xds["n_malicious"], "{:d}")
m("extDsBen", xds["n_benign"], "{:d}")
m("extDsDetected", ds_tp, "{:d}")
m("extDsFp", xds["inbound_false_positives"], "{:d}")
m("extNiN", xni["n"], "{:d}")
m("extNiFp", xni["inbound_blocked"], "{:d}")
m("extAttacks", xia["base"]["n"] + xia["enhanced"]["n"] + xds["n_malicious"], "{:,d}")
m("extAttacksDetected", ia_blocked + ds_tp, "{:d}")
m("extBenign", xds["n_benign"] + xni["n"], "{:d}")
m("extBenignFp", xds["inbound_false_positives"] + xni["inbound_blocked"], "{:d}")
write("tab_external.tex", "\n".join([
    "\\begin{table}[t]",
    "\\centering",
    "\\caption{Signature inspection (M3) on public datasets: cases blocked / cases. Injected tool responses are checked as upstream responses, prompts as tool arguments. ``All rules'' applies every inbound and outbound rule to each text.}",
    "\\label{tab:external}",
    "\\setlength{\\tabcolsep}{3pt}",
    "\\begin{tabular}{l l rr}",
    "\\toprule",
    "Dataset & Direction & As deployed & All rules \\\\",
    "\\midrule",
    f"InjecAgent, base (attacks) & outbound & {xia['base']['outbound_blocked']}/{xia['base']['n']} & {xia['base']['all_rules_blocked']}/{xia['base']['n']} \\\\",
    f"InjecAgent, enhanced (attacks) & outbound & {xia['enhanced']['outbound_blocked']}/{xia['enhanced']['n']} & {xia['enhanced']['all_rules_blocked']}/{xia['enhanced']['n']} \\\\",
    f"deepset, injections & inbound & {ds_tp}/{xds['n_malicious']} & {round(xds['all_rules_recall'] * xds['n_malicious'] / 100)}/{xds['n_malicious']} \\\\",
    f"deepset, benign & inbound & {xds['inbound_false_positives']}/{xds['n_benign']} & {round(xds['all_rules_fpr'] * xds['n_benign'] / 100)}/{xds['n_benign']} \\\\",
    f"NotInject (benign) & inbound & {xni['inbound_blocked']}/{xni['n']} & {xni['all_rules_blocked']}/{xni['n']} \\\\",
    "\\bottomrule",
    "\\end{tabular}",
    "\\end{table}",
    "",
]))

# ------------------------------------------------------------------ exp4
e4 = D["exp4"]
idx = e4["alphas"].index(1.0)
P = e4["policies"]
names = [("none", "No cache"), ("global_fifo", "Global FIFO"), ("global_lru", "Global LRU"), ("tenant_lru", "Tenant LRU"),
         ("tenant_ttl_lru", "Tenant LRU + per-tool TTL"), ("proposed_tiered", "Proposed (+ cost-aware)")]
rows = []
for k, name in names:
    r = P[k][idx]
    rows.append(f"{name} & {r['hit_mean']:.1f} & {r['saved_mean']:.1f} & {r['lat_mean']:.2f} & {r['leak_mean']:.1f} & {r['stale_mean']:.1f} \\\\")
write("tab_cache.tex", "\n".join([
    "\\begin{table}[t]",
    "\\centering",
    f"\\caption{{Cache policies at Zipf $\\alpha = 1.0$ (four tenants, capacity {e4['capacity']} entries, five trials of 400 calls). Saved: share of upstream service time avoided. Leak and stale: share of all calls answered with another tenant's data or with data older than the tool's freshness bound.}}",
    "\\label{tab:cache}",
    "\\setlength{\\tabcolsep}{3pt}",
    "\\begin{tabular}{l rrrrr}",
    "\\toprule",
    "Policy & Hit & Saved & Latency & Leak & Stale \\\\",
    " & (\\%) & (\\%) & (ms) & (\\%) & (\\%) \\\\",
    "\\midrule",
    *rows,
    "\\bottomrule",
    "\\end{tabular}",
    "\\end{table}",
    "",
]))
for k, key in [("none", "None"), ("global_fifo", "Gfifo"), ("global_lru", "Glru"), ("tenant_lru", "Tlru"),
               ("tenant_ttl_lru", "Tttl"), ("proposed_tiered", "Prop")]:
    r = P[k][idx]
    m(f"c{key}Hit", r["hit_mean"], "{:.1f}")
    m(f"c{key}Saved", r["saved_mean"], "{:.1f}")
    m(f"c{key}Lat", r["lat_mean"], "{:.2f}")
    m(f"c{key}Leak", r["leak_mean"], "{:.1f}")
    m(f"c{key}Stale", r["stale_mean"], "{:.1f}")
hi = len(e4["alphas"]) - 1
m("cGlruLeakHi", P["global_lru"][hi]["leak_mean"], "{:.1f}")
m("cGlruHitHi", P["global_lru"][hi]["hit_mean"], "{:.1f}")
m("cPropHitHi", P["proposed_tiered"][hi]["hit_mean"], "{:.1f}")
m("cPropSavedHi", P["proposed_tiered"][hi]["saved_mean"], "{:.1f}")
m("cTttlSavedHi", P["tenant_ttl_lru"][hi]["saved_mean"], "{:.1f}")
m("cAlphaHi", e4["alphas"][hi], "{:.1f}")
m("cPropLatGain", 100 * (1 - P["proposed_tiered"][idx]["lat_mean"] / P["none"][idx]["lat_mean"]), "{:.1f}")
if os.path.exists(_aws_same):
    with open(_aws_same, encoding="utf-8") as f:
        e4a = json.load(f).get("exp4")
    if e4a:
        Pa = e4a["policies"]
        ia = e4a["alphas"].index(1.0)
        m("awCNoneLat", Pa["none"][ia]["lat_mean"], "{:.2f}")
        m("awCPropLat", Pa["proposed_tiered"][ia]["lat_mean"], "{:.2f}")
        m("awCPropLatGain", 100 * (1 - Pa["proposed_tiered"][ia]["lat_mean"] / Pa["none"][ia]["lat_mean"]), "{:.1f}")


def fmt_p(p: float) -> str:
    return "$p < 0.001$" if p < 0.001 else f"$p = {p:.3f}$" if p < 0.01 else f"$p = {p:.2f}$"


_sens = os.path.join(HERE, "results", "cache_cost_sensitivity_20seeds.json")
if os.path.exists(_sens):
    with open(_sens, encoding="utf-8") as f:
        S = json.load(f)
    cell = {(c["spread"], c["capacity"]): c for c in S["cells"]}
    caps = sorted({c["capacity"] for c in S["cells"]})
    m("senSeeds", len(S["seeds"]), "{:d}")
    m("senTrace", S["trace_len"], "{:d}")
    for name, cap in zip(["S", "M", "B"], caps):
        m(f"senCap{name}", cap, "{:d}")
    cur = cell[("current", caps[0])]["costs_ms"].values()
    m("senCurLo", min(cur), "{:.0f}")
    m("senCurHi", max(cur), "{:.0f}")
    for sp, key in [("current", "Cur"), ("moderate", "Mod"), ("wide", "Wide")]:
        c = cell[(sp, caps[0])]
        m(f"senGain{key}", c["diff_saved"]["mean"], "{:.1f}")
        m(f"senRel{key}", 100 * c["diff_saved"]["mean"] / c["tenant_ttl_lru"]["saved_pct"], "{:.0f}")
        m(f"senP{key}", fmt_p(c["diff_saved"]["p"]), "{}")
    m("senHitLoss", max(-cell[(sp, caps[0])]["diff_hit"]["mean"] for sp in ["current", "moderate", "wide"]), "{:.1f}")
    mid = [-cell[(sp, caps[1])]["diff_saved"]["mean"] for sp in ["current", "moderate", "wide"]]
    m("senLossMin", min(mid), "{:.2f}")
    m("senLossMax", max(mid), "{:.2f}")
    m("senPMidMax", fmt_p(max(cell[(sp, caps[1])]["diff_saved"]["p"] for sp in ["current", "moderate", "wide"])), "{}")
    n_evict = sum(1 for c in S["cells"] if c["tenant_ttl_lru"]["evictions"] > 0)
    m("senEvictCells", n_evict, "{:d}")
    m("senBonf", 0.05 / n_evict, "{:.4f}")

# ------------------------------------------------------------------ exp5
e5 = D["exp5"]
rows = []
for lab, name in [("T2", "T2 HTTP+SSE"), ("T3", "T3 unpooled"), ("T4", "T4 pooled"), ("T4+cache", "T4 + cache")]:
    s = e5["series"][lab]
    pts = s["points"]
    xmax = max(p["rps"] for p in pts)
    a, b = s["models"]["dcpu_at_n1"], s["models"]["dcpu_at_nmax"]
    pb = b["params"]
    rows.append(f"{name} & {pb['R1_ms']:.2f} & {pb['S_hold_ms']:.2f} & {a['params']['D_cpu_ms']:.3f} & {pb['D_cpu_ms']:.3f} & "
                f"{pb['bottleneck']} & {pb['knee_N']:.0f} & {xmax:.0f} & {a['mape_throughput_pct']:.0f} / {b['mape_throughput_pct']:.0f} & "
                f"{a['mape_latency_pct']:.0f} / {b['mape_latency_pct']:.0f} \\\\")
write("tab_model.tex", "\n".join([
    "\\begin{table*}[t]",
    "\\centering",
    f"\\caption{{Asymptotic-bound model versus measurement for $N \\in \\{{1,\\dots,128\\}}$ closed-loop sessions at {e5['rtt_ms']:.0f} ms RTT, pool size $c = {e5['pool']}$. $R_1$ and $S_{{\\mathrm{{hold}}}}$ are measured at $N=1$; $D_{{\\mathrm{{cpu}}}}$ is measured CPU time per call at $N=1$ and at $N=128$. Errors are mean absolute percentage errors (MAPE) of the model using $D_{{\\mathrm{{cpu}}}}$ at $N=1$ / at $N=128$.}}",
    "\\label{tab:model}",
    "\\begin{tabular}{l rr rr c r r rr}",
    "\\toprule",
    " & $R_1$ & $S_{\\mathrm{hold}}$ & \\multicolumn{2}{c}{$D_{\\mathrm{cpu}}$ (ms)} & Bottle- & Knee & Max $X$ & \\multicolumn{2}{c}{MAPE (\\%)} \\\\",
    "\\cmidrule(lr){4-5}\\cmidrule(lr){9-10}",
    "Transport & (ms) & (ms) & $N{=}1$ & $N{=}128$ & neck & $N^*$ & (calls/s) & throughput & latency \\\\",
    "\\midrule",
    *rows,
    "\\bottomrule",
    "\\end{tabular}",
    "\\end{table*}",
    "",
]))
for lab, key in [("T2", "Ttwo"), ("T3", "Tthree"), ("T4", "Tfour"), ("T4+cache", "Tcache")]:
    s = e5["series"][lab]
    pts = s["points"]
    m(f"q{key}Xmax", max(p["rps"] for p in pts), "{:.0f}")
    m(f"q{key}Knee", s["models"]["dcpu_at_nmax"]["params"]["knee_N"], "{:.0f}")
    m(f"q{key}MapeX", s["models"]["dcpu_at_nmax"]["mape_throughput_pct"], "{:.0f}")
    m(f"q{key}MapeR", s["models"]["dcpu_at_nmax"]["mape_latency_pct"], "{:.0f}")
    m(f"q{key}MapeXone", s["models"]["dcpu_at_n1"]["mape_throughput_pct"], "{:.0f}")
    m(f"q{key}MapeRone", s["models"]["dcpu_at_n1"]["mape_latency_pct"], "{:.0f}")
    m(f"q{key}Dcpuone", s["models"]["dcpu_at_n1"]["params"]["D_cpu_ms"], "{:.2f}")
    m(f"q{key}Dcpumax", s["models"]["dcpu_at_nmax"]["params"]["D_cpu_ms"], "{:.2f}")
    m(f"q{key}Hold", s["models"]["dcpu_at_nmax"]["params"]["S_hold_ms"], "{:.2f}")
    m(f"q{key}Pnnmax", pts[-1]["p99"], "{:.0f}")
    m(f"q{key}Heapmax", pts[-1]["heap_peak_kb"] / 1024, "{:.1f}")
m("qHeapMaxAll", max(s["points"][-1]["heap_peak_kb"] for s in e5["series"].values()) / 1024, "{:.1f}")
m("qPool", e5["pool"], "{:d}")
m("qRtt", e5["rtt_ms"], "{:.0f}")
m("qTthreeBound", e5["pool"] / e5["series"]["T3"]["models"]["dcpu_at_nmax"]["params"]["S_hold_ms"] * 1000, "{:.0f}")
m("qTfourBound", e5["pool"] / e5["series"]["T4"]["models"]["dcpu_at_nmax"]["params"]["S_hold_ms"] * 1000, "{:.0f}")
m("qTtwoCpuBound", 1000 / e5["series"]["T2"]["models"]["dcpu_at_nmax"]["params"]["D_cpu_ms"], "{:.0f}")
m("qTtwoOverTthree", max(p["rps"] for p in e5["series"]["T2"]["points"]) / max(p["rps"] for p in e5["series"]["T3"]["points"]), "{:.1f}")
m("qMapeXMin", min(s["models"]["dcpu_at_nmax"]["mape_throughput_pct"] for s in e5["series"].values()), "{:.0f}")
m("qMapeXMax", max(s["models"]["dcpu_at_nmax"]["mape_throughput_pct"] for s in e5["series"].values()), "{:.0f}")
m("qTfourOverTthree", max(p["rps"] for p in e5["series"]["T4"]["points"]) / max(p["rps"] for p in e5["series"]["T3"]["points"]), "{:.1f}")


def bound_frac(series):
    par = series["models"]["dcpu_at_nmax"]["params"]
    bound = min(1.0 / par["D_cpu_ms"], par["c"] / par["S_hold_ms"] if par["S_hold_ms"] > 0 else float("inf")) * 1000
    return 100 * max(p["rps"] for p in series["points"]) / bound


S5 = e5["series"]
for name, var, metric in [("qMapeR", "dcpu_at_nmax", "mape_latency_pct"), ("qMapeXOne", "dcpu_at_n1", "mape_throughput_pct"),
                          ("qMapeROne", "dcpu_at_n1", "mape_latency_pct")]:
    vals = [s["models"][var][metric] for s in S5.values()]
    m(f"{name}Min", min(vals), "{:.0f}")
    m(f"{name}Max", max(vals), "{:.0f}")
m("qBoundFracMin", min(bound_frac(s) for s in S5.values()), "{:.0f}")
m("qBoundFracMax", max(bound_frac(s) for s in S5.values()), "{:.0f}")
m("qTfourMeanmax", S5["T4"]["points"][-1]["mean"], "{:.0f}")
m("qTtwoMeanmax", S5["T2"]["points"][-1]["mean"], "{:.0f}")
m("qTcacheHitOne", S5["T4+cache"]["points"][0]["hit_pct"], "{:.1f}")
m("qTcacheHitMax", S5["T4+cache"]["points"][-1]["hit_pct"], "{:.1f}")
m("qTcacheGain", 100 * (xmax_ := max(p["rps"] for p in S5["T4+cache"]["points"])) / max(p["rps"] for p in S5["T4"]["points"]) - 100, "{:.0f}")
# ------------------------------------------------------------------ exp6
e6 = D["exp6"]
I = e6["inputs"]
p = e6["prices"]
m("kBilled", I["lambda_billed_ms_uncached"], "{:d}")
m("kCpuUnc", I["cpu_ms_per_req_uncached"], "{:.3f}")
m("kCpuC", I["cpu_ms_per_req_cached"], "{:.3f}")
m("kCapVcpu", I["capacity_m_per_vcpu_month_uncached"] / 1000, "{:.1f}")
m("kHit", 100 * I["cache_hit_ratio"], "{:.1f}")
m("kEcTwo", I["ec2_month"], "{:.2f}")
m("kFargate", I["fargate_task_month"], "{:.2f}")
m("kAlb", I["alb_month"], "{:.2f}")
m("kBreakeven", e6["breakeven_m_lambda_vs_ec2_cached"] or 0, "{:.0f}")
m("kGwSide", e6["gateway_side_ms"], "{:.2f}")
sens = {s["tool_ms"]: s for s in e6["tool_latency_sensitivity"]}
m("kBreakevenFg", e6.get("breakeven_m_lambda_vs_fargate") or 0, "{:.0f}")
m("kFargateMin", e6["curves"]["D2_Fargate"][0], "{:.0f}")
m("kEcTwoMin", e6["curves"]["D4_EC2_cached"][0], "{:.0f}")
m("kVcpuShareThousand", 100 * 1000 / I["capacity_m_per_vcpu_month_uncached"] * p["target_utilization"], "{:.0f}")
m("kHitConc", D["exp5"]["series"]["T4+cache"]["points"][-1]["hit_pct"], "{:.1f}")
m("kHitAtThirtyTwo", next(x for x in D["exp5"]["series"]["T4+cache"]["points"] if x["n"] == 32)["hit_pct"], "{:.0f}")
for t, key in [(10, "Ten"), (100, "Hundred"), (1000, "Thousand")]:
    m(f"kBe{key}", sens[t]["breakeven_m"] or 0, "{:.0f}")
    m(f"kBeFg{key}", sens[t].get("breakeven_fargate_m") or 0, "{:.0f}")
    m(f"kLam{key}", sens[t]["lambda_usd_per_m"], "{:.2f}")
m("kUtil", 100 * p["target_utilization"], "{:.0f}")
# ------------------------------------------------------------------ ~100 ms tools (MCP_IO_SCALE=4.3)
with open(os.path.join(HERE, "results", "benchmark_metrics_tool100ms.json"), encoding="utf-8") as f:
    H = json.load(f)
from upstream_mcp_server import TOOL_CATALOG  # noqa: E402
_base = [t["base_exec_ms"] for t in TOOL_CATALOG]
_sc = H["testbed"]["io_scale"]
m("hToolMin", min(_base) * _sc, "{:.0f}")
m("hToolMax", max(_base) * _sc, "{:.0f}")
m("hToolMean", sum(_base) / len(_base) * _sc, "{:.0f}")
HC = H["exp1"]["configs"]
m("hTthreeMinusTfourRten", HC["T3"]["10"]["mean_ms"] - HC["T4"]["10"]["mean_ms"], "{:.1f}")
m("hTthreeOverTfourRten", HC["T3"]["10"]["mean_ms"] / HC["T4"]["10"]["mean_ms"], "{:.2f}")
HP = H["exp4"]["policies"]
hi = H["exp4"]["alphas"].index(1.0)
m("hCPropLatGain", 100 * (1 - HP["proposed_tiered"][hi]["lat_mean"] / HP["none"][hi]["lat_mean"]), "{:.1f}")
m("hCPropSavedMs", HP["none"][hi]["lat_mean"] - HP["proposed_tiered"][hi]["lat_mean"], "{:.1f}")
HS = H["exp5"]["series"]
m("hMapeMin", min(s["models"]["dcpu_at_nmax"]["mape_throughput_pct"] for s in HS.values()), "{:.1f}")
m("hMapeMax", max(s["models"]["dcpu_at_nmax"]["mape_throughput_pct"] for s in HS.values()), "{:.1f}")
m("hMapeOneMax", max(s["models"]["dcpu_at_n1"]["mape_throughput_pct"] for s in HS.values()), "{:.1f}")
m("hBilled", H["exp6"]["inputs"]["lambda_billed_ms_uncached"], "{:d}")
m("hBreakevenFg", H["exp6"]["breakeven_m_lambda_vs_fargate"], "{:.0f}")
m("hBreakeven", H["exp6"]["breakeven_m_lambda_vs_ec2_cached"], "{:.0f}")

idx1000 = e6["volumes_m"].index(1000)
m("kDfourThousand", e6["curves"]["D4_EC2_cached"][idx1000], "{:.0f}")
m("kDoneThousand", e6["curves"]["D1_Lambda"][idx1000], "{:.0f}")

# ------------------------------------------------------------------ testbed
tb = D["testbed"]
m("tbPython", tb["python"], "{}")
m("tbIoScale", tb["io_scale"], "{:.2f}")

# ------------------------------------------------------------------ AWS multi-host validation
AWS_DIR = os.path.join(HERE, "results", "aws")
LAYOUTS = [("same_az", "Same", "Same AZ"), ("cross_az", "Xaz", "Cross AZ"), ("cross_region", "Xreg", "Cross region")]


def load_aws(name):
    p = os.path.join(AWS_DIR, f"aws_{name}.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def xmax(series):
    return max(p["rps"] for p in series["points"])


aws_lat_rows, aws_x_rows, mapes, mapes_one = [], [], [], []
aws_mape_r, aws_frac, aws_frac_cache, aws_hit_max, aws_t2_over_t4_in = [], [], [], [], []
for lay, key, title in LAYOUTS:
    for tls in (False, True):
        d = load_aws(lay + ("_tls" if tls else ""))
        if not d or "exp1" not in d or "exp5" not in d:
            continue
        sfx = key + ("Tls" if tls else "")
        rtt = d["testbed"]["network_rtt_ms"]["p50_ms"]
        c = {k: v["0"] for k, v in d["exp1"]["configs"].items()}
        s = d["exp5"]["series"]
        if not tls:
            m(f"awRtt{key}", rtt, "{:.2f}" if rtt < 5 else "{:.1f}")
        for lab, lk in [("T2", "Ttwo"), ("T3", "Tthree"), ("T4", "Tfour")]:
            m(f"aw{lk}{sfx}", c[lab]["mean_ms"])
            m(f"aw{lk}X{sfx}", xmax(s[lab]), "{:.0f}")
        m(f"awTthreeOverTfour{sfx}", c["T3"]["mean_ms"] / c["T4"]["mean_ms"])
        m(f"awTfourPnn{sfx}", s["T4"]["points"][-1]["p99"], "{:.0f}")
        m(f"awTtwoOverTfourX{sfx}", xmax(s["T2"]) / xmax(s["T4"]), "{:.1f}")
        extra_rtt = (c["T3"]["mean_ms"] - c["T4"]["mean_ms"]) / rtt
        m(f"awExtraRtt{sfx}", extra_rtt, "{:.1f}")
        run_mapes = [v["models"]["dcpu_at_nmax"]["mape_throughput_pct"] for v in s.values()]
        run_mapes_one = [v["models"]["dcpu_at_n1"]["mape_throughput_pct"] for v in s.values()]
        mapes += run_mapes
        mapes_one += run_mapes_one
        aws_mape_r += [v["models"]["dcpu_at_nmax"]["mape_latency_pct"] for v in s.values()]
        aws_frac += [bound_frac(v) for k, v in s.items() if k != "T4+cache"]
        aws_frac_cache.append(bound_frac(s["T4+cache"]))
        aws_hit_max.append(s["T4+cache"]["points"][-1]["hit_pct"])
        if lay != "cross_region":
            aws_t2_over_t4_in.append(xmax(s["T2"]) / xmax(s["T4"]))
        lead = f"{title if not tls else ''} & {'TLS' if tls else 'TCP'} & {rtt:.2f} & "
        aws_lat_rows.append(
            lead
            + " & ".join(f"{c[l]['mean_ms']:.2f}" for l in ("T2", "T3", "T4"))
            + f" & {c['T3']['mean_ms'] / c['T4']['mean_ms']:.2f} & {c['T3']['mean_ms'] - c['T4']['mean_ms']:.2f} \\\\")
        aws_x_rows.append(
            lead
            + " & ".join(f"{xmax(s[l]):.0f}" for l in ("T2", "T3", "T4"))
            + f" & {s['T4']['points'][-1]['p99']:.0f} & {min(run_mapes):.0f}--{max(run_mapes):.0f}"
            + f" & {min(run_mapes_one):.0f}--{max(run_mapes_one):.0f} \\\\")
    if lay != LAYOUTS[-1][0]:
        aws_lat_rows.append("\\addlinespace")
        aws_x_rows.append("\\addlinespace")

if aws_lat_rows:
    for rows in (aws_lat_rows, aws_x_rows):
        while rows and rows[-1] == "\\addlinespace":
            rows.pop()
    m("awMapeMin", min(mapes), "{:.1f}")
    m("awMapeMax", max(mapes), "{:.1f}")
    m("awMapeOneMin", min(mapes_one), "{:.1f}")
    m("awMapeOneMax", max(mapes_one), "{:.0f}")
    m("awMapeRMin", min(aws_mape_r), "{:.0f}")
    m("awMapeRMax", max(aws_mape_r), "{:.0f}")
    m("awBoundFracMin", min(aws_frac), "{:.0f}")
    m("awBoundFracMax", max(aws_frac), "{:.0f}")
    m("awBoundFracCacheMax", max(aws_frac_cache), "{:.0f}")
    m("awCacheHitMin", min(aws_hit_max), "{:.1f}")
    m("awCacheHitMax", max(aws_hit_max), "{:.1f}")
    m("awTtwoOverTfourInMin", min(aws_t2_over_t4_in), "{:.1f}")
    m("awTtwoOverTfourInMax", max(aws_t2_over_t4_in), "{:.1f}")
    same, same_tls = load_aws("same_az"), load_aws("same_az_tls")
    tb_aws = same["testbed"]
    m("awCpuName", tb_aws["cpu"].replace("(R)", "").split(" (")[0], "{}")
    m("awPython", tb_aws["python"], "{}")
    m("awLevels", max(p["n"] for p in same["exp5"]["series"]["T4"]["points"]), "{:d}")
    if same_tls:
        m("awTlsPerCallSame", same_tls["exp1"]["configs"]["T3"]["0"]["mean_ms"] - same["exp1"]["configs"]["T3"]["0"]["mean_ms"])
        m("awTlsCpuTthree", same_tls["exp5"]["series"]["T3"]["points"][0]["cpu_ms_per_req"], "{:.2f}")
        m("awCpuTthree", same["exp5"]["series"]["T3"]["points"][0]["cpu_ms_per_req"], "{:.2f}")
    if "exp2" in same:
        big = str(max(int(k) for k in same["exp2"]["rows"]))
        m("awMidBig", same["exp2"]["rows"][big]["total"]["mean_ms"])
        m("awMidSmall", same["exp2"]["rows"][str(min(int(k) for k in same["exp2"]["rows"]))]["total"]["mean_ms"], "{:.2f}")
    if "exp6" in same:
        e6a = same["exp6"]
        m("awCpuUnc", e6a["inputs"]["cpu_ms_per_req_uncached"], "{:.3f}")
        m("awBreakeven", e6a["breakeven_m_lambda_vs_ec2_cached"] or 0, "{:.0f}")
        m("awBreakevenFg", e6a.get("breakeven_m_lambda_vs_fargate") or 0, "{:.0f}")
        m("awBilled", e6a["inputs"]["lambda_billed_ms_uncached"], "{:d}")
    write("tab_aws_latency.tex", "\n".join([
        "\\begin{table}[t]",
        "\\centering",
        "\\caption{Sequential latency per call on Amazon EC2 (two c7i.large instances; real network, no emulated delay; mean of five trials). "
        "RTT is the median TCP connect time between the hosts.}",
        "\\label{tab:aws-latency}",
        "\\footnotesize",
        "\\setlength{\\tabcolsep}{2.7pt}",
        "\\begin{tabular}{ll r rrr r r}",
        "\\toprule",
        " & & & \\multicolumn{3}{c}{Latency (ms)} & & T3$-$T4 \\\\",
        "\\cmidrule(lr){4-6}",
        "Placement & Security & RTT (ms) & T2 & T3 & T4 & T3/T4 & (ms) \\\\",
        "\\midrule",
        *aws_lat_rows,
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table}",
        "",
    ]))
    write("tab_aws_throughput.tex", "\n".join([
        "\\begin{table}[t]",
        "\\centering",
        "\\caption{Closed-loop results on Amazon EC2. $X_{\\max}$ is the highest throughput over $N = 1$--" + str(macros.get("awLevels", "128")) + " sessions "
        "(three trials); P99 is T4's tail latency at the largest $N$. MAPE is the bounds model's throughput error across the four configurations, "
        "with $D_{\\mathrm{cpu}}$ measured at the largest $N$ (load) or at $N = 1$.}",
        "\\label{tab:aws-throughput}",
        "\\footnotesize",
        "\\setlength{\\tabcolsep}{2.1pt}",
        "\\begin{tabular}{ll r rrr r rr}",
        "\\toprule",
        " & & & \\multicolumn{3}{c}{$X_{\\max}$ (calls/s)} & T4 P99 & \\multicolumn{2}{c}{MAPE (\\%)} \\\\",
        "\\cmidrule(lr){4-6}\\cmidrule(lr){8-9}",
        "Placement & Security & RTT (ms) & T2 & T3 & T4 & (ms) & load & $N{=}1$ \\\\",
        "\\midrule",
        *aws_x_rows,
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table}",
        "",
    ]))

with open(os.path.join(OUT, "macros.tex"), "w", encoding="utf-8") as f:
    for k, v in sorted(macros.items()):
        f.write(f"\\newcommand{{\\{k}}}{{{v}}}\n")
print(f"wrote {len(macros)} macros and tables to {OUT}")
