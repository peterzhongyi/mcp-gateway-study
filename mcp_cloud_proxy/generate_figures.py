"""
Generates the paper figures (vector PDF + 300-DPI PNG) from results/benchmark_metrics.json.
Every plotted number is read from the results file.
"""

import json
import os

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter, NullFormatter
from matplotlib.patches import FancyBboxPatch

plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "DejaVu Serif"],
    "font.size": 9,
    "axes.labelsize": 9,
    "axes.titlesize": 9.5,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 7.5,
    "savefig.dpi": 300,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "grid.linestyle": "--",
})

HERE = os.path.dirname(os.path.abspath(__file__))
FIG_DIR = os.path.join(HERE, "..", "paper", "figures")
os.makedirs(FIG_DIR, exist_ok=True)
with open(os.path.join(HERE, "results", "benchmark_metrics.json"), encoding="utf-8") as f:
    D = json.load(f)

COL = {"T1": "#64748B", "T2": "#D97706", "T3": "#2563EB", "T4": "#7C3AED", "T4+cache": "#059669"}
NAME = {
    "T1": "T1 stdio bridge",
    "T2": "T2 legacy HTTP+SSE",
    "T3": "T3 Streamable HTTP, unpooled",
    "T4": "T4 Streamable HTTP, pooled",
    "T4+cache": "T4 + proposed cache",
}


def save(fig, name):
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(FIG_DIR, f"{name}.{ext}"), bbox_inches="tight", pad_inches=0.03)
    plt.close(fig)
    print("saved", name)


def fig_architecture():
    """Single-column layout (3.5 in wide) with text at 6.8 pt or larger."""
    fig, ax = plt.subplots(figsize=(3.5, 3.75))
    ax.set_xlim(0, 100)
    ax.set_ylim(8, 115)
    ax.axis("off")

    def box(x, y, w, h, title, sub="", fc="#F4F6F9", ec="#2B4C7E", tfs=7.6, sfs=6.8, lw=0.9, weight="bold"):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.5", fc=fc, ec=ec, lw=lw))
        if sub:
            ax.text(x + w / 2, y + h * 0.72, title, ha="center", va="center", fontsize=tfs, fontweight="bold")
            ax.text(x + w / 2, y + h * 0.30, sub, ha="center", va="center", fontsize=sfs, color="#334155",
                    linespacing=1.1)
        else:
            ax.text(x + w / 2, y + h / 2, title, ha="center", va="center", fontsize=tfs, fontweight=weight)

    def arrow(x0, y0, x1, y1, color="#1E293B", ls="-"):
        ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                    arrowprops=dict(arrowstyle="-|>,head_width=0.25,head_length=0.5", color=color, lw=0.9, ls=ls,
                                    shrinkA=0, shrinkB=0))

    # callers
    callers = [("Interactive", "user"), ("Delegated", "user + actor"), ("Headless", "non-user")]
    for i, (t, s) in enumerate(callers):
        box(2 + i * 33, 103.5, 29, 10.5, t, s, fc="#E0F2FE", ec="#0284C7")
        arrow(16.5 + i * 33, 102.9, 16.5 + i * 33, 96.6)

    # gateway frame
    ax.add_patch(FancyBboxPatch((1, 30), 98, 66, boxstyle="round,pad=0.4,rounding_size=2", fc="#F8FAFC", ec="#1E3A8A", lw=1.2))
    ax.text(50, 92.5, "MCP gateway (single process)", ha="center", va="center", fontsize=8, fontweight="bold", color="#1E3A8A")

    # stages as a 2x2 grid, M1 -> M2 -> M3 -> M4
    stages = [(4, 72, "M1 Identity", "token verify, persona\ncheck, token exchange", "#DBEAFE", "#1D4ED8"),
              (52, 72, "M2 Authorization", "per-tool scope,\nJSON Schema", "#DBEAFE", "#1D4ED8"),
              (4, 52, "M3 Inspection", "inbound and outbound\nrules, redaction", "#FEE2E2", "#B91C1C"),
              (52, 52, "M4 Result cache", "tenant-scoped key,\nper-tool TTL", "#DCFCE7", "#15803D")]
    for x, y, t, s, fc, ec in stages:
        box(x, y, 44, 15, t, s, fc=fc, ec=ec)
    arrow(48.6, 79.5, 51.4, 79.5)
    ax.plot([74, 74, 26, 26], [71.4, 69.5, 69.5, 67.6], color="#1E293B", lw=0.9)
    arrow(26, 67.8, 26, 67.4)
    arrow(48.6, 59.5, 51.4, 59.5)

    # cache hit returns to the caller
    hit = "#15803D"
    ax.plot([96.8, 98.2, 98.2], [59.5, 59.5, 100.5], color=hit, lw=0.9, ls="--")
    arrow(98.2, 100.5, 98.2, 102.6, color=hit, ls="--")
    ax.text(97.2, 99, "hit", ha="right", va="center", fontsize=6.8, color=hit)

    # miss path into the transports
    arrow(74, 51.4, 74, 47.6, color="#B45309")
    ax.text(72, 49.5, "miss", ha="right", va="center", fontsize=6.8, color="#92400E")
    ax.add_patch(FancyBboxPatch((4, 33), 92, 14, boxstyle="round,pad=0.4,rounding_size=1.5", fc="#FEF3C7", ec="#B45309", lw=0.9))
    ax.text(50, 44.2, "Upstream transports", ha="center", va="center", fontsize=7.6, fontweight="bold")
    chips = [("T1", "stdio"), ("T2", "HTTP+SSE"), ("T3", "unpooled"), ("T4", "pooled")]
    for i, (t, s) in enumerate(chips):
        box(6 + i * 22.5, 34.5, 20, 7, f"{t} {s}", fc="#FFFBEB", ec="#D97706", tfs=6.4, weight="normal")

    # upstream MCP servers
    servers = [("Retrieval", "kb, docs, code"), ("Analytics", "SQL, features"), ("Ops", "k8s, tickets, IAM")]
    for i, (t, s) in enumerate(servers):
        arrow(16.5 + i * 33, 29.4, 16.5 + i * 33, 22.6)
        box(2 + i * 33, 10, 29, 12, t, s, fc="#F1F5F9", ec="#475569")
    save(fig, "fig1_architecture")


def fig_transports():
    e = D["exp1"]
    rtts = e["rtts_ms"]
    labels = ["T1", "T2", "T3", "T4", "T4+cache"]
    fig, (a, b) = plt.subplots(1, 2, figsize=(7.2, 2.7), gridspec_kw={"width_ratios": [1.25, 1]})
    w = 0.16
    x = np.arange(len(rtts))
    for i, lab in enumerate(labels):
        rows = e["configs"][lab]
        xs, ys, es = [], [], []
        for j, r in enumerate(rtts):
            if str(r) in rows:
                xs.append(x[j] + (i - 2) * w)
                ys.append(rows[str(r)]["mean_ms"])
                es.append(rows[str(r)]["mean_ci95"])
        a.bar(xs, ys, w, yerr=es, capsize=2, color=COL[lab], label=NAME[lab], edgecolor="#1E293B", lw=0.4)
    a.set_xticks(x)
    a.set_xticklabels([f"RTT = {r} ms\n" + ("(loopback)" if r == 0 else "(emulated)") for r in rtts])
    a.set_ylabel("Mean end-to-end latency (ms)")
    a.set_title("(a) Mean latency per call, sequential (95% CI, 5 trials)")
    a.legend(loc="upper left", fontsize=6.5)

    for lab in ["T2", "T3", "T4", "T4+cache"]:
        s = np.sort(e["configs"][lab]["2"]["samples"])
        b.plot(s, np.arange(1, len(s) + 1) / len(s), color=COL[lab], label=NAME[lab], lw=1.4)
    s = np.sort(e["configs"]["T1"]["0"]["samples"])
    b.plot(s, np.arange(1, len(s) + 1) / len(s), color=COL["T1"], label=NAME["T1"] + " (local)", lw=1.2, ls=":")
    b.set_xscale("log")
    b.set_xlabel("End-to-end latency (ms, log scale)")
    b.set_ylabel("Empirical CDF")
    b.set_title("(b) Latency CDF at 2 ms RTT")
    b.legend(loc="lower right", fontsize=6.3)
    fig.tight_layout()
    save(fig, "fig2_transports")


def fig_middleware_ablation():
    e2, e3 = D["exp2"], D["exp3"]
    sizes = e2["payload_kb"]
    stages = [("auth", "M1 identity", "#1D4ED8"), ("rbac", "M2 authorization", "#0284C7"),
              ("cache", "M4 cache key/lookup", "#059669"), ("dpi_in", "M3 inbound", "#D97706"),
              ("dpi_out", "M3 outbound", "#DC2626")]
    fig, (a, b) = plt.subplots(1, 2, figsize=(7.2, 2.8))
    x = np.arange(len(sizes))
    bottom = np.zeros(len(sizes))
    for k, lab, c in stages:
        v = np.array([e2["rows"][str(s)][k]["mean_ms"] for s in sizes])
        a.bar(x, v, 0.55, bottom=bottom, label=lab, color=c)
        bottom += v
    p99 = [e2["rows"][str(s)]["total_p99_ms"] for s in sizes]
    a.plot(x, p99, "k--o", ms=3, lw=1, label="total, P99")
    for i, t in enumerate(bottom):
        a.annotate(f"mean {t:.2f}", (x[i], max(t, p99[i])), textcoords="offset points", xytext=(-4, 5), ha="right", fontsize=6)
    a.set_xticks(x)
    a.set_xticklabels([f"{s} KB" for s in sizes])
    a.set_xlabel("Request and response payload size")
    a.set_ylabel("Gateway processing time (ms)")
    a.set_title("(a) Per-stage cost (mean of 5 trials)")
    a.legend(fontsize=6.3, loc="upper left")

    keys = list(e3["configs"].keys())
    oh = [e3["configs"][k]["overhead_ms"] for k in keys]
    oh_sd = [e3["configs"][k]["overhead_sd"] for k in keys]
    b.bar(np.arange(len(keys)), oh, 0.5, yerr=oh_sd, capsize=2, color="#94A3B8", edgecolor="#1E293B", lw=0.4, label="gateway overhead (ms)")
    b.set_xticks(np.arange(len(keys)))
    b.set_xticklabels(keys)
    b.set_ylabel("Mean gateway overhead (ms)")
    b.set_title("(b) Ablation: overhead vs. measured detection")
    br = b.twinx()
    br.grid(False)

    def sub_rate(sec, subset):
        rows = [v for k, v in sec["per_subset"].items() if k.endswith("/" + subset) and not k.startswith("B_")]
        return 100.0 * sum(r["flagged"] for r in rows) / sum(r["total"] for r in rows)

    sig = [sub_rate(e3["configs"][k]["security"], "signature") for k in keys]
    eva = [sub_rate(e3["configs"][k]["security"], "evasive") for k in keys]
    fpr = [e3["configs"][k]["security"]["fpr_pct"] for k in keys]
    br.plot(range(len(keys)), sig, "-D", color="#7C2D12", ms=3.5, lw=1.3, label="recall, signature attacks")
    br.plot(range(len(keys)), eva, "-s", color="#B91C1C", ms=3.5, lw=1.3, ls="--", label="recall, evasive attacks")
    br.plot(range(len(keys)), fpr, "-^", color="#0F766E", ms=3.5, lw=1.1, ls=":", label="false-positive rate")
    br.set_ylim(0, 110)
    b.set_ylim(bottom=0)
    for ln in br.get_lines():
        ln.set_clip_on(False)
    br.set_ylabel("Rate (%)")
    h1, l1 = b.get_legend_handles_labels()
    h2, l2 = br.get_legend_handles_labels()
    b.legend(h1 + h2, l1 + l2, fontsize=6.0, loc="upper left")
    fig.tight_layout()
    save(fig, "fig3_middleware_ablation")


def fig_concurrency():
    e = D["exp5"]
    short = {"T2": "T2 HTTP+SSE", "T3": "T3 unpooled", "T4": "T4 pooled", "T4+cache": "T4 + cache"}
    mk = {"T2": "o", "T3": "s", "T4": "D", "T4+cache": "^"}
    fig, grid = plt.subplots(2, 2, figsize=(3.5, 3.15))
    axes = [grid[0, 0], grid[0, 1], grid[1, 0]]
    grid[1, 1].axis("off")
    for lab, s in e["series"].items():
        n = [p["n"] for p in s["points"]]
        c = COL[lab]
        model = s["models"]["dcpu_at_nmax"]["points"]
        axes[0].errorbar(n, [p["rps"] for p in s["points"]], yerr=[p["rps_sd"] for p in s["points"]], fmt=mk[lab], ms=3, color=c, capsize=1.5)
        axes[0].plot(n, [m["rps"] for m in model], color=c, lw=0.9, ls="--", alpha=0.8)
        axes[1].errorbar(n, [p["mean"] for p in s["points"]], yerr=[p["mean_sd"] for p in s["points"]], fmt=mk[lab], ms=3, color=c, capsize=1.5)
        axes[1].plot(n, [m["mean"] for m in model], color=c, lw=0.9, ls="--", alpha=0.8)
        axes[2].plot(n, [p["p99"] for p in s["points"]], "-", marker=mk[lab], ms=3, lw=1.1, color=c)
    levels = [p["n"] for p in next(iter(e["series"].values()))["points"]]
    for ax in axes:
        ax.set_xscale("log", base=2)
        ax.set_xticks(levels)
        ax.set_xticklabels([str(x) for x in levels], fontsize=6.5)
        ax.tick_params(axis="y", labelsize=7)
        ax.minorticks_off()
        ax.set_xlabel("Sessions N", fontsize=8)
    axes[0].set_ylabel("Throughput (calls/s)", fontsize=8)
    axes[0].set_title("(a) Throughput vs. bound", fontsize=8.5)
    for ax, ticks in [(axes[1], [10, 20, 50, 100, 200]), (axes[2], [20, 50, 100, 200])]:
        ax.set_yscale("log")
        ax.set_yticks(ticks)
        ax.set_yticklabels([str(t) for t in ticks])
        ax.yaxis.set_minor_formatter(NullFormatter())
    axes[1].set_ylabel("Mean latency (ms)", fontsize=8)
    axes[1].set_title("(b) Mean latency vs. model", fontsize=8.5)
    axes[2].set_ylabel("P99 latency (ms)", fontsize=8)
    axes[2].set_title("(c) P99 latency", fontsize=8.5)
    fig.tight_layout(h_pad=0.6, w_pad=0.6)
    handles = [Line2D([], [], color=COL[k], marker=mk[k], ls="-", ms=3.5, lw=1.1) for k in e["series"]]
    handles += [Line2D([], [], color="0.35", marker="o", ls="none", ms=3.5), Line2D([], [], color="0.35", ls="--", lw=0.9)]
    labels = [short[k] for k in e["series"]] + ["measured", "bounds model"]
    grid[1, 1].legend(handles, labels, loc="center", fontsize=7, frameon=False, handlelength=2.2)
    save(fig, "fig4_concurrency")


def fig_cache():
    e = D["exp4"]
    al = e["alphas"]
    pol = [("global_fifo", "global FIFO", "#D97706", "^", "-."), ("global_lru", "global LRU", "#2563EB", "v", "--"),
           ("tenant_lru", "tenant LRU", "#7C3AED", "D", "-."), ("tenant_ttl_lru", "tenant LRU + per-tool TTL", "#DB2777", "s", ":"),
           ("proposed_tiered", "proposed (+ cost-aware)", "#059669", "o", "-")]
    fig, axes = plt.subplots(1, 2, figsize=(3.5, 2.4))
    for i, (k, lab, c, mk, ls) in enumerate(pol):
        rows = e["policies"][k]
        xs = [a + 0.015 * (i - 2) for a in al]
        axes[0].errorbar(xs, [r["hit_mean"] for r in rows], yerr=[r["hit_sd"] for r in rows], color=c, marker=mk, ls=ls, ms=3, lw=1.1, capsize=1.5, label=lab)
        axes[1].errorbar(xs, [r["saved_mean"] for r in rows], yerr=[r["saved_sd"] for r in rows], color=c, marker=mk, ls=ls, ms=3, lw=1.1, capsize=1.5)
    axes[0].set_ylabel("Hit rate (% of calls)")
    axes[0].set_title("(a) Hit rate")
    axes[1].set_ylabel("Service time saved (%)")
    axes[1].set_title("(b) Upstream work avoided")
    for ax in axes:
        ax.set_xlabel(r"Zipf skew $\alpha$")
        ax.set_xticks(al)
        ax.tick_params(axis="x", labelsize=7)
    fig.tight_layout(rect=(0, 0, 1, 0.83), w_pad=0.8)
    handles = [Line2D([], [], color=c, marker=mk, ls=ls, ms=3.5, lw=1.1) for _, _, c, mk, ls in pol]
    labels = [lab for _, lab, *_ in pol]
    fig.legend(handles, labels, loc="upper center", ncol=2, fontsize=7, frameon=False,
               bbox_to_anchor=(0.5, 1.0), handlelength=2.2, columnspacing=1.2)
    save(fig, "fig5_cache")


def fig_cost():
    e = D["exp6"]
    v = e["volumes_m"]
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(3.5, 3.85), gridspec_kw={"height_ratios": [1.3, 1]})
    series = [("D1_Lambda", "D1 Lambda", "#D97706", "o", "--"), ("D1c_Lambda_cached", "D1 Lambda + cache", "#F59E0B", "o", ":"),
              ("D2_Fargate", "D2 Fargate + ALB", "#2563EB", "s", "-."), ("D3_EC2", "D3 EC2 + ALB (= D4)", "#7C3AED", "^", ":"),
              ("D4_EC2_cached", "D4 EC2 + ALB + cache", "#059669", "D", "-")]
    for k, lab, c, mk, ls in series:
        ax.plot(v, e["curves"][k], color=c, marker=mk, ls=ls, ms=3, lw=1.2, label=lab)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_ylim(0.15, 3000)
    for key, curve, c, lab, ty in [("breakeven_m_lambda_vs_fargate", "D2_Fargate", "#2563EB", "Fargate", 22),
                                   ("breakeven_m_lambda_vs_ec2_cached", "D4_EC2_cached", "#059669", "EC2", 1300)]:
        be = e.get(key)
        if be:
            y = e["curves"][curve][0]
            ax.plot([be], [y], "o", ms=6, mfc="none", mec="#B91C1C", mew=1.1, zorder=5)
            ax.annotate(f"Lambda costs more than {lab}\nabove {be:.0f} M calls/month", xy=(be, y), xytext=(1.2, ty),
                        fontsize=6, color="#991B1B", va="center",
                        arrowprops=dict(arrowstyle="->", color="#B91C1C", lw=0.7, shrinkB=4))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:g}"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"\\${x:g}"))
    ax.set_xlabel("Monthly tool calls (millions)")
    ax.set_ylabel("Hosting cost (USD/month)")
    ax.set_title(f"(a) Cost vs. volume (billed {e['inputs']['lambda_billed_ms_uncached']} ms per call)", fontsize=8.5)
    ax.legend(fontsize=6.3, loc="lower right")

    sens = e["tool_latency_sensitivity"]
    for key, c, mk, lab, annotate in [("breakeven_fargate_m", "#2563EB", "s", "Lambda vs. Fargate", True),
                                      ("breakeven_m", "#059669", "D", "Lambda vs. EC2 + cache", False)]:
        s = [r for r in sens if r.get(key)]
        bx.plot([r["tool_ms"] for r in s], [r[key] for r in s], "-", marker=mk, color=c, ms=3, lw=1.2, label=lab)
        if annotate:
            for r in s:
                bx.annotate(f"{r[key]:.0f}", (r["tool_ms"], r[key]), textcoords="offset points", xytext=(-3, -9),
                            fontsize=5.8, ha="right", color=c)
    bx.set_xscale("log")
    bx.set_yscale("log")
    bx.set_xlim(3, 1600)
    bx.margins(y=0.15)
    bx.set_ylim(bottom=5)
    bx.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:g}"))
    bx.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:g}"))
    bx.set_xlabel("Upstream tool latency (ms)")
    bx.set_ylabel("Break-even (M calls/mo)")
    bx.set_title("(b) Break-even volume vs. tool latency", fontsize=8.5)
    bx.legend(fontsize=6.3, loc="upper right")
    fig.tight_layout()
    save(fig, "fig6_cost")


def fig_security():
    sec = D["exp3"]["configs"]["A4"]["security"]["per_subset"]
    cats = [("C1_Auth", "C1 auth"), ("C2_AuthZ", "C2 authz"), ("C3_Injection", "C3 injection"),
            ("C4_SSRF_Cmd_SQL", "C4 SSRF/cmd/SQL"), ("C5_Outbound_Poisoning", "C5 poisoning"), ("C6_Outbound_DLP", "C6 DLP")]
    fig, ax = plt.subplots(figsize=(3.5, 2.4))
    x = np.arange(len(cats))

    def rate(key):
        r = sec.get(key)
        return 100.0 * r["flagged"] / r["total"] if r else np.nan

    sig = [rate(f"{c}/signature") for c, _ in cats]
    eva = [rate(f"{c}/evasive") for c, _ in cats]
    ax.bar(x - 0.18, sig, 0.36, color="#1D4ED8", label="signature subset")
    ax.bar(x + 0.18, eva, 0.36, color="#DC2626", label="evasive subset")
    for i in range(len(cats)):
        if np.isnan(eva[i]):
            ax.text(x[i] + 0.18, 2, "n/a", ha="center", fontsize=5.5)
    ax.set_xticks(x)
    ax.set_xticklabels([l for _, l in cats], rotation=25, ha="right", fontsize=6.5)
    ax.set_ylabel("Detection rate (%)")
    ax.set_ylim(0, 112)
    ax.legend(fontsize=6, loc="upper right")
    fig.tight_layout()
    save(fig, "fig7_security")


if __name__ == "__main__":
    fig_architecture()
    fig_transports()
    fig_middleware_ablation()
    fig_concurrency()
    fig_cache()
    fig_cost()
    fig_security()
