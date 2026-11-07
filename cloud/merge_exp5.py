"""
Replace the concurrency experiment (exp5) in each EC2 result file with a rerun.

The rerun files (aws_<layout>[_tls]<suffix>.json) contain only exp5. Other experiments do not depend
on the change being rerun, so they are kept; exp6 (cost model) reads exp5 and is recomputed where the
file has its other inputs.

  python merge_exp5.py [--suffix _exp5fifo]
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.join(HERE, "..", "mcp_cloud_proxy")
RESULTS = os.path.join(CODE, "results", "aws")
LABELS = [f"{lay}{tls}" for lay in ("same_az", "cross_az", "cross_region") for tls in ("", "_tls")]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suffix", default="_exp5fifo")
    a = ap.parse_args()
    sys.path.insert(0, CODE)
    from run_benchmarks import exp6_cost

    for label in LABELS:
        base_p = os.path.join(RESULTS, f"aws_{label}.json")
        new_p = os.path.join(RESULTS, f"aws_{label}{a.suffix}.json")
        if not os.path.exists(new_p):
            print(f"{label}: no rerun file, skipped")
            continue
        with open(base_p, encoding="utf-8") as f:
            base = json.load(f)
        with open(new_p, encoding="utf-8") as f:
            new = json.load(f)
        base["exp5"] = new["exp5"]
        base.setdefault("provenance", {})["exp5"] = {"source": os.path.basename(new_p), "testbed": new["testbed"]}
        if all(k in base for k in ("exp1", "exp4", "exp5")):
            base["exp6"] = exp6_cost(base["exp1"], base["exp4"], base["exp5"])
        with open(base_p, "w", encoding="utf-8") as f:
            json.dump(base, f, indent=1)
        print(f"{label}: exp5 replaced" + (", exp6 recomputed" if "exp6" in base else ""))


if __name__ == "__main__":
    main()
