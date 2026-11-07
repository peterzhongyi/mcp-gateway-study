"""
Evaluates the gateway's signature inspection (M3) on public datasets that we did not write.

  InjecAgent (Zhan et al., 2024): tool responses carrying an injected attacker instruction, base and
    "enhanced" variants; checked as upstream responses (outbound). The same responses with the
    injection removed give a benign control.
  NotInject (Li and Liu, 2024): benign sentences containing prompt-injection trigger words; checked as
    tool arguments (inbound) for false positives.
  deepset/prompt-injections: labeled injection and benign prompts; checked as tool arguments (inbound).

Each text is also checked against every rule in both directions ("all rules"), a best case for the rules.
Writes results/external_eval.json. Datasets are read from external_data/ (see README for sources).
"""
from __future__ import annotations

import json
import os
from collections import Counter
from typing import Any, Dict, List

import pyarrow.parquet as pq

from gateway_middleware import DeepPayloadInspectionGuardrail

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "external_data")
G = DeepPayloadInspectionGuardrail()


def inbound(text: str) -> str | None:
    ok, reason = G.inspect_inbound({"name": "search_docs", "arguments": {"query": text}})
    return None if ok else reason.split(":", 1)[1]


def outbound(text: str) -> Dict[str, Any]:
    resp = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}]}}
    out, redactions, blocked = G.sanitize_outbound(resp)
    return {"blocked": blocked, "redactions": redactions}


def any_rule(text: str) -> bool:
    return inbound(text) is not None or outbound(text)["blocked"]


def rate(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def injecagent() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for variant in ("base", "enhanced"):
        cases: List[Dict[str, Any]] = []
        for kind in ("dh", "ds"):
            with open(os.path.join(DATA, f"injecagent_test_cases_{kind}_{variant}.json"), encoding="utf-8") as f:
                cases += [dict(c, kind=kind) for c in json.load(f)]
        blocked = sum(outbound(c["Tool Response"])["blocked"] for c in cases)
        allr = sum(any_rule(c["Tool Response"]) for c in cases)
        rules = Counter(r for c in cases if (r := inbound(c["Tool Response"])))
        out[variant] = {"n": len(cases), "outbound_blocked": blocked, "outbound_recall": rate(blocked, len(cases)),
                        "all_rules_blocked": allr, "all_rules_recall": rate(allr, len(cases)),
                        "inbound_rule_hits": dict(rules)}
    with open(os.path.join(DATA, "injecagent_test_cases_dh_base.json"), encoding="utf-8") as f:
        templates = {c["Tool Response Template"] for c in json.load(f)}
    with open(os.path.join(DATA, "injecagent_test_cases_ds_base.json"), encoding="utf-8") as f:
        templates |= {c["Tool Response Template"] for c in json.load(f)}
    benign = [t.replace("<Attacker Instruction>", "") for t in sorted(templates)]
    fp = sum(any_rule(t) for t in benign)
    out["benign_control"] = {"n": len(benign), "all_rules_blocked": fp, "fpr": rate(fp, len(benign))}
    return out


def notinject() -> Dict[str, Any]:
    rows = []
    for s in ("one", "two", "three"):
        rows += pq.read_table(os.path.join(DATA, f"notinject_{s}.parquet")).to_pylist()
    hits = [r for r in rows if inbound(r["prompt"])]
    allr = sum(any_rule(r["prompt"]) for r in rows)
    return {"n": len(rows), "inbound_blocked": len(hits), "inbound_fpr": rate(len(hits), len(rows)),
            "all_rules_blocked": allr, "all_rules_fpr": rate(allr, len(rows)),
            "inbound_rule_hits": dict(Counter(inbound(r["prompt"]) for r in hits)),
            "examples_blocked": [r["prompt"] for r in hits[:5]]}


def deepset() -> Dict[str, Any]:
    rows = []
    for s in ("train", "test"):
        rows += pq.read_table(os.path.join(DATA, f"deepset_{s}.parquet")).to_pylist()
    mal = [r["text"] for r in rows if r["label"] == 1]
    ben = [r["text"] for r in rows if r["label"] == 0]
    tp = sum(inbound(t) is not None for t in mal)
    fp = sum(inbound(t) is not None for t in ben)
    tp_all = sum(any_rule(t) for t in mal)
    fp_all = sum(any_rule(t) for t in ben)
    return {"n_malicious": len(mal), "n_benign": len(ben),
            "inbound_recall": rate(tp, len(mal)), "inbound_fpr": rate(fp, len(ben)),
            "all_rules_recall": rate(tp_all, len(mal)), "all_rules_fpr": rate(fp_all, len(ben)),
            "inbound_detected": tp, "inbound_false_positives": fp,
            "examples_missed": [t for t in mal if inbound(t) is None][:5]}


def main() -> None:
    res = {"injecagent": injecagent(), "notinject": notinject(), "deepset": deepset()}
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    with open(os.path.join(HERE, "results", "external_eval.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2, ensure_ascii=False)
    print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
