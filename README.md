# Security-Enforcing Gateways for the Model Context Protocol: Artifact

Code, data, and scripts for the paper *Transport, Policy, and Caching Trade-offs in Security-Enforcing
Gateways for the Model Context Protocol: An Empirical Study* (Yi Zhong, submitted to IEEE Access).

The artifact contains the gateway prototype, the benchmark harness, the raw results behind every table
and figure, and the scripts that turn those results into the paper's tables, figures, and in-text
numbers. Measured numbers are not typed into the manuscript by hand: `make_tables.py` writes every
table and the measured numbers quoted in the text as LaTeX macros from the result files.

## Layout

```
mcp_cloud_proxy/          gateway prototype, benchmark harness, analysis scripts
  gateway_middleware.py     stages M1-M4 (identity, authorization, inspection, cache policies)
  proxy_transports.py       upstream transports T1-T4 and the emulated network delay
  upstream_mcp_server.py    upstream MCP server and its 12-tool catalog (stdio)
  upstream_http_server.py   the same server over HTTP/1.1 (Streamable HTTP and legacy HTTP+SSE)
  upstream_agent.py         starts upstream servers on the remote host in the EC2 runs
  http11.py                 HTTP/1.1 framing shared by gateway and upstream
  hires_timer.py            1 ms timer resolution on Windows
  security_suite.py         the 410-case security conformance suite
  run_benchmarks.py         experiments exp1-exp6 (see below)
  cache_cost_sensitivity.py when cost-aware eviction pays off (capacity x tool-cost grid)
  idle_gap_m1.py            M1 cost versus idle time between calls
  inspection_worst_case.py  worst-case cost of the outbound poisoning rule
  external_eval.py          inspection on public prompt-injection datasets
  make_tables.py            results -> paper/generated/macros.tex and tab_*.tex
  generate_figures.py       results -> paper/figures/*.pdf
  external_data/            public datasets used by external_eval.py (see "Third-party data")
  results/                  raw results (JSON) and run logs
    aws/                    EC2 results, one file per placement and security setting
cloud/                    EC2 orchestration
  aws_bench.py              create instances, run experiments, collect results, tear down
  merge_exp5.py             merge a rerun of the concurrency experiment into EC2 result files
  summarize.py              print headline numbers of the EC2 result files
paper/                    LaTeX source of the manuscript (IEEE Access template)
  main.tex, references.bib
  generated/                tables and number macros written by make_tables.py
  figures/                  figures written by generate_figures.py
```

## Requirements

- Python 3.12 or later with `numpy`, `scipy`, `matplotlib`, and `pyarrow` (`pip install -r requirements.txt`).
  Tested with Python 3.14.0, numpy 2.5.0, scipy 1.18.0, matplotlib 3.11.1, and pyarrow 26.0.1 on the
  single host, and with Ubuntu 24.04's Python 3.12.3 and its `python3-numpy` and `python3-scipy`
  packages on EC2. `matplotlib` is needed only for figures and `pyarrow` only for `external_eval.py`.
- A LaTeX distribution with `latexmk` and `pdflatex` to build the paper.
- For the EC2 runs only: an AWS account, the AWS CLI v2 with a profile named `mcp-bench`, and an
  OpenSSH client. `aws_bench.py` looks for the CLI at its default Windows location; elsewhere, set
  `AWS_CLI=aws`.

## Rebuild the paper from the included results

```
cd mcp_cloud_proxy
python make_tables.py
python generate_figures.py
cd ../paper
latexmk -pdf main.tex
```

This takes under a minute and needs no measurements.

## Rerun the measurements

The commands below are for PowerShell, where the experiments were run. In a POSIX shell, write
environment variables as `MCP_IO_SCALE=4.3 python ...`. Each command writes or updates the result file
named after it; run `make_tables.py` and `generate_figures.py` afterwards. Absolute numbers depend on the
machine; the published single-host results come from an Intel Core i7-14700HX with 32 GB of RAM under
Windows 11.

### Single host

`run_benchmarks.py` runs the gateway and load generator in one process and the upstream MCP server in a
separate process, reached over pipes (T1) or loopback TCP (T2-T4).

| Experiment | What it measures | Paper |
|---|---|---|
| `exp1` | sequential latency per transport at 0, 2, and 10 ms emulated RTT; session setup | transport table |
| `exp2` | time per middleware stage versus payload size (1-256 KB) | middleware table |
| `exp3` | ablation of stages, with the security suite run against each configuration | ablation and security tables |
| `exp4` | six cache policies versus Zipf skew | cache table and figure |
| `exp5` | closed-loop concurrency, N = 1-128 sessions, against the bounds model | model table, concurrency figure |
| `exp6` | cloud cost model from the exp1, exp4, and exp5 measurements (computed, not measured) | cost figure |

```
cd mcp_cloud_proxy
python run_benchmarks.py                     # all experiments -> results/benchmark_metrics.json (about 15 minutes)
python run_benchmarks.py exp5 exp6           # rerun selected experiments; others in the file are kept
```

Tools of about 100 ms (the "about 100 ms" sentences in the results; about two hours):

```
$env:MCP_IO_SCALE = "4.3"
$env:MCP_OUT = "results\benchmark_metrics_tool100ms.json"
python run_benchmarks.py exp1 exp4 exp5 exp6
```

Supplementary experiments:

```
$env:MCP_SENS_SEEDS = "20"; python cache_cost_sensitivity.py   # -> results/cache_cost_sensitivity_20seeds.json
python idle_gap_m1.py                                          # -> results/idle_gap_m1.json
python inspection_worst_case.py                                # -> results/inspection_worst_case.json
python external_eval.py                                        # -> results/external_eval.json
```

Settings read from the environment by `run_benchmarks.py`:

| Variable | Default | Meaning |
|---|---|---|
| `MCP_OUT` | `results/benchmark_metrics.json` | result file |
| `MCP_TRIALS` | `5` | number of trials (seeds 101, 202, 303, 404, 505) |
| `MCP_IO_SCALE` | `0.25` | upstream service time as a multiple of each tool's base time (0.25 gives 2.1-10.5 ms) |
| `MCP_RTTS` | `0,2,10` | emulated RTTs for exp1, in ms |
| `MCP_CONC_RTT` | `2` | emulated RTT for exp5, in ms |
| `MCP_LEVELS` | `1,2,4,8,16,32,64,128` | session counts for exp5 |
| `MCP_CALLS_PER_SESSION` | `32` | exp5 runs max(320, this x N) calls per level |
| `MCP_STEADY_X` | `1` | exp5 throughput over the window before the first session finishes; `0` divides all calls by the total time, which reads low wherever the gateway is not saturated |
| `MCP_TLS` | unset | `1` uses TLS 1.3 to the upstream (EC2 runs) |

### Amazon EC2

`cloud/aws_bench.py` creates a gateway instance and an upstream instance (c7i.large, Ubuntu 24.04) for
each placement, uploads the code, runs the experiments with real network delay and no emulated RTT, and
copies the results to `mcp_cloud_proxy/results/aws/`. Placements: `same-az` (us-east-1a to us-east-1a),
`cross-az` (us-east-1a to us-east-1b), and `cross-region` (us-east-1a to us-east-2a).

```
cd cloud
python aws_bench.py up --stack same-az --layout same-az
python aws_bench.py run --stack same-az --label aws_same_az --exps all
python aws_bench.py run --stack same-az --label aws_same_az_tls --exps "exp1 exp5" --env "MCP_TLS=1"
python aws_bench.py up --stack cross-az --layout cross-az
python aws_bench.py run --stack cross-az --label aws_cross_az --exps "exp1 exp5"
python aws_bench.py run --stack cross-az --label aws_cross_az_tls --exps "exp1 exp5" --env "MCP_TLS=1"
python aws_bench.py up --stack cross-region --layout cross-region
python aws_bench.py run --stack cross-region --label aws_cross_region --exps "exp1 exp5"
python aws_bench.py run --stack cross-region --label aws_cross_region_tls --exps "exp1 exp5" --env "MCP_TLS=1"
python aws_bench.py down --all
```

`make_tables.py` reads the six files `results/aws/aws_<placement>[_tls].json`. The instances are billed
while they exist: **always finish with `python aws_bench.py down --all`**, which terminates every instance
and deletes the key pairs and security groups the script created (all tagged `Project=mcp-bench`).

### How the published result files were assembled

The concurrency experiment (exp5) was rerun after the throughput measurement was changed to the
steady-state window that is now the default. On the single host, exp5 and exp6 were rerun into
`results/benchmark_metrics.json`. On EC2, exp5 was rerun on its own
(`--label aws_<placement>[_tls]_exp5steady --exps exp5`) and merged into the six result files with
`python merge_exp5.py --suffix _exp5steady`; the rerun files are included. A fresh run with the current
defaults produces the final files directly and needs no merge.

## Third-party data

`external_eval.py` reads three public datasets from `mcp_cloud_proxy/external_data/`. The included files are
byte-identical to the upstream files at the revisions below.

| Dataset | Source and revision | Files used | License |
|---|---|---|---|
| InjecAgent (Zhan et al., 2024) | [github.com/uiuc-kang-lab/InjecAgent](https://github.com/uiuc-kang-lab/InjecAgent), commit `f19c9f2`, `data/` | `test_cases_{dh,ds}_{base,enhanced}.json`, `attacker_cases_{dh,ds}.jsonl` (saved with an `injecagent_` prefix) | MIT |
| NotInject (Li and Liu, 2024) | [huggingface.co/datasets/leolee99/NotInject](https://huggingface.co/datasets/leolee99/NotInject), revision `847ae76`, `data/` | `NotInject_{one,two,three}-00000-of-00001.parquet` (saved as `notinject_{one,two,three}.parquet`) | MIT |
| deepset prompt-injections | [huggingface.co/datasets/deepset/prompt-injections](https://huggingface.co/datasets/deepset/prompt-injections), revision `4f61ecb`, `data/` | `train-` and `test-*.parquet` (saved as `deepset_{train,test}.parquet`) | Apache-2.0 |

These datasets remain under their own licenses and belong to their authors; cite the original works if
you use them.

## License

The code and results are released under the MIT License (see `LICENSE`). The datasets in
`mcp_cloud_proxy/external_data/` keep their own licenses (above). The IEEE Access LaTeX template files in
`paper/` (`ieeeaccess.cls`, `IEEEtran.cls`, `IEEEtran.bst`, `spotcolor.sty`, the `t1*` font files, and the
logo images) are IEEE's and are included only so that the manuscript builds.

## Citation

If you use this artifact, please cite the paper (citation details will be added on publication).
