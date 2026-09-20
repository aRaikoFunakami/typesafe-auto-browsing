"""bench_all.sh の記録から、プロンプト × 方式の中央値（成功した回だけ）を出す。使い方: python3 scripts/bench_summary.py OUT_DIR"""
import json, statistics as st, sys
from collections import defaultdict
from pathlib import Path

out = Path(sys.argv[1])
ok, bad = defaultdict(list), defaultdict(list)
for f in sorted(out.glob("*/*/meta.json")):
    m = json.loads(f.read_text())
    (ok if m["ok"] else bad)[(m["prompt"], m["method"])].append(m)
for k, rows in ok.items():  # 1 つの枠（n）に成功が複数あるときは、最初の 1 件だけ
    ok[k] = list({r["n"]: r for r in reversed(rows)}.values())[::-1]

def med(rows, k):
    return st.median(r[k] for r in rows)

print("| prompt | 方式 | n | 時間 中央値(s) | 総費用 中央値($) | Claude | TypeSafe | ターン | 失敗(費用$) |")
print("|---|---|---|---|---|---|---|---|---|")
tot = defaultdict(lambda: [0.0, 0.0, 0])
for (p, m) in sorted(set(ok) | set(bad)):
    r, b = ok.get((p, m), []), bad.get((p, m), [])
    fail = f"{len(b)}（{sum(x['total_cost_usd'] for x in b):.3f}）"
    if r:
        n = len({x["n"] for x in r})  # 成功した枠の数
        print(f"| {p} | {m} | {n} | {med(r,'real_s'):.1f} | {med(r,'total_cost_usd'):.4f} | {med(r,'claude_cost_usd'):.4f} | {med(r,'typesafe_cost_usd'):.4f} | {med(r,'turns'):g} | {fail} |")
        if n == 3:
            tot[m][0] += med(r, "real_s"); tot[m][1] += med(r, "total_cost_usd"); tot[m][2] += 1
    else:
        print(f"| {p} | {m} | 0 | – | – | – | – | – | {fail} |")
print("\n3 回そろったプロンプトだけの合計（中央値の和）:")
for m, (t, c, k) in sorted(tot.items()):
    print(f"  {m}: {k} 件, 時間 {t:.0f}s, 費用 ${c:.4f}")
nj = out / "needs_judgment.tsv"
if nj.exists():
    print("\n要判断:\n" + nj.read_text())
