"""
Worst-case cost of the outbound tool-poisoning rule (M3-out).

Times one search of the gateway's outbound poisoning patterns over benign filler text and over a
crafted response made of repeated opening `<important>` tags with no closing tag, for which the lazy
`.*?` scan restarts from every tag. Reports the median of three repetitions per size.
Results go to results/inspection_worst_case.json.
"""

import json
import os
import statistics
import time

from gateway_middleware import DeepPayloadInspectionGuardrail

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "results", "inspection_worst_case.json")
SIZES_KB = [16, 64, 256]
REPS = 3


def time_search(text: str) -> float:
    runs = []
    for _ in range(REPS):
        t0 = time.perf_counter()
        for rx in DeepPayloadInspectionGuardrail.OUTBOUND_POISON_PATTERNS:
            rx.search(text)
        runs.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(runs)


def main() -> None:
    out = {"reps": REPS, "rows": []}
    for kb in SIZES_KB:
        n = kb * 1024
        benign = ("lorem ipsum " * (n // 12 + 1))[:n]
        crafted = ("<important> " * (n // 12 + 1))[:n]
        row = {"kb": kb, "benign_ms": round(time_search(benign), 3), "crafted_ms": round(time_search(crafted), 1)}
        out["rows"].append(row)
        print(row)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
