"""Summarise the newest `hyperframes check` report by finding type."""

import json
from collections import defaultdict
from pathlib import Path

reports = sorted((Path(__file__).parent / ".hyperframes" / "artifacts" / "checks").glob("*.json"), key=lambda p: p.stat().st_mtime)
d = json.loads(reports[-1].read_text(encoding="utf-8"))
items = []


def walk(o):
    if isinstance(o, dict):
        if "code" in o and ("severity" in o or "message" in o):
            items.append(o)
        for v in o.values():
            walk(v)
    elif isinstance(o, list):
        for v in o:
            walk(v)


walk(d)
by = defaultdict(list)
for i in items:
    by[i["code"]].append(i)
if not by:
    print("no findings")
for code, lst in by.items():
    print(f"== {code} x{len(lst)} [{lst[0].get('severity')}] {(lst[0].get('message') or '')[:230]}")
    for j in lst[:6]:
        text = j.get("text") if isinstance(j.get("text"), str) else ""
        print("   -", Path(j.get("sourceFile") or "").name, j.get("selector") or "", text[:50], j.get("firstSeen") or "")
