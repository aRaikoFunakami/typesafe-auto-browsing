"""docs/cost-comparison-by-complexity.md のグラフ 4 枚を、bench_all.sh の記録から描く。
使い方: uv run --no-project --with matplotlib python scripts/plot_complexity.py OUT_DIR [OUT_DIR ...]
OUT_DIR は、複数の記録をまとめて渡せる（プロンプトごとに、成功した回の中央値を使う）。日本語のフォントは macOS の Hiragino Sans。"""
import json, statistics as st, sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties

# 複雑度（docs/cost-comparison-by-complexity.md と同じ分類）
LEVELS = {
    1: ("開いて読む", ["pypi-numpy-license", "pypi-requests-version"]),
    2: ("開いて比べる", ["hn-most-comments", "hn-most-points"]),
    3: ("検索して読む", ["wikipedia-eiffel-year", "wikipedia-tokyo-tower-designer", "wikipedia-tokyo-tower-height"]),
    4: ("選んで辿る", ["tver-meisaku-drama-detail"]),
    5: ("入力して比べる", ["amazon-cheapest-usbc", "yahoo-transit-cheapest-fare", "yahoo-transit-search-only", "yahoo-transit-shortest"]),
}
BG, INK, MUTED, GRID = "#fbfaf7", "#111111", "#666666", "#dcdcd6"
BLUE, LIGHT_BLUE, ORANGE, DOT = "#2a78d6", "#8ab8f0", "#eb6835", "#8a8a80"
REG, BOLD = FontProperties(family="Hiragino Sans W3"), FontProperties(family="Hiragino Sans W6")

# --- データ: (プロンプト, 方式) ごとの、成功した回の中央値 ---
rows = defaultdict(list)
for out in map(Path, sys.argv[1:]):
    for f in sorted(out.glob("*/*/meta.json")):
        m = json.loads(f.read_text())
        if m["ok"]:
            rows[(m["prompt"], m["method"])].append(m)
def med(p, m, k):
    r = list({x["n"]: x for x in reversed(rows[(p, m)])}.values())  # 1 つの枠に成功が複数あれば、最初の 1 件だけ
    assert len({x["n"] for x in r}) == 3, (p, m, "3 回そろっていない")
    return st.median(x[k] for x in r)
def mean(level, m, k):
    return st.mean(med(p, m, k) for p in LEVELS[level][1])

def base(title, sub, size):
    fig = plt.figure(figsize=size, dpi=100, facecolor=BG)
    fig.text(40 / (size[0] * 100), 1 - 45 / (size[1] * 100), title, fontproperties=BOLD, fontsize=21, va="center")
    fig.text(40 / (size[0] * 100), 1 - 88 / (size[1] * 100), sub, fontproperties=REG, fontsize=15, color=MUTED, va="center")
    return fig

def legend(fig, items, size, y=130):
    x = 40
    for color, label in items:
        fig.patches.append(matplotlib.patches.FancyBboxPatch(
            (x / (size[0] * 100), 1 - (y + 10) / (size[1] * 100)), 26 / (size[0] * 100), 20 / (size[1] * 100),
            boxstyle="round,pad=0,rounding_size=0.004", transform=fig.transFigure, color=color))
        t = fig.text((x + 40) / (size[0] * 100), 1 - y / (size[1] * 100), label, fontproperties=REG, fontsize=15, va="center")
        fig.canvas.draw()
        x += 40 + t.get_window_extent().width + 50

def bars(fname, title, sub, key, unit, fmt, ymax, step, stack=None):
    size = (12.8, 8.4)
    fig = base(title, sub, size)
    ax = fig.add_axes([112 / 1280, 125 / 840, 1128 / 1280, 530 / 840], facecolor=BG)
    w = 0.2
    for lv in LEVELS:
        a, b = mean(lv, "A", key), mean(lv, "B", key)
        ax.bar(lv - w * 0.55, a, w, color=BLUE, zorder=3)
        if stack:  # A のうち TypeSafe の分を、上に薄い色で
            ax.bar(lv - w * 0.55, mean(lv, "A", stack), w, bottom=a - mean(lv, "A", stack), color=LIGHT_BLUE, zorder=4)
        ax.bar(lv + w * 0.55, b, w, color=ORANGE, zorder=3)
        for x, v in ((lv - w * 0.55, a), (lv + w * 0.55, b)):
            ax.text(x, v + ymax * 0.012, fmt(v), ha="center", va="bottom", fontproperties=REG, fontsize=13.5, zorder=5)
    ax.set_xticks(list(LEVELS))
    ax.set_xticklabels([f"複雑度 {lv}\n{n}\n（{len(ps)} 件）" for lv, (n, ps) in LEVELS.items()], fontproperties=REG, fontsize=15, color="#333333", linespacing=1.4)
    ax.tick_params(axis="x", length=0, pad=12)
    ax.set_ylim(0, ymax); ax.set_yticks([i * step for i in range(int(ymax / step) + 1)])
    ax.set_yticklabels([fmt(i * step) if key != "real_s" else f"{i * step:g}" for i in range(int(ymax / step) + 1)], fontproperties=REG, fontsize=14, color=MUTED)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="y", color=GRID, zorder=0); ax.set_axisbelow(True)
    for s in ("top", "right", "left"): ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color("#bdbdb5")
    fig.text(70 / 1280, 1 - 168 / 840, unit, fontproperties=REG, fontsize=14, color=MUTED, ha="center")
    items = [(BLUE, "A: typesafe-auto-browsing"), (ORANGE, "B: Playwright MCP")]
    if stack: items.append((LIGHT_BLUE, "A のうち TypeSafe の分"))
    legend(fig, items, size)
    fig.savefig(fname, facecolor=BG); plt.close(fig)

img = Path(__file__).resolve().parent.parent / "docs" / "images"
sub = "各プロンプトの中央値（3 回）を、複雑度ごとに平均した値"
bars(img / "complexity-1-time.png", "実時間（短いほうが良い）", sub, "real_s", "秒", lambda v: f"{v:.0f}", 100, 25)
bars(img / "complexity-2-cost.png", "総コスト（安いほうが良い）", sub + "。A は Claude Code + TypeSafe", "total_cost_usd", "$", lambda v: f"{v:.3f}" if v else "0.00", 0.25, 0.05, stack="typesafe_cost_usd")
bars(img / "complexity-3-turns.png", "ターン数（Claude Code の往復の回数）", sub, "turns", "回", lambda v: f"{v:.1f}" if v % 10 else f"{v:.0f}", 30, 10)

# --- B÷A（プロンプトごと） ---
W, H = 1520, 1052
n_rows = sum(1 + len(ps) for _, ps in LEVELS.values())
fig = base("複雑度別・プロンプトごとの B ÷ A", "1 より右は A のほうが有利（B が高い）、左は B のほうが有利", (W / 100, H / 100))
ax = fig.add_axes([0, 0, 1, 1], facecolor="none"); ax.set_xlim(0, W); ax.set_ylim(H, 0); ax.axis("off")  # 画素座標（左上が原点）
def text(x, y, t, fp=REG, size=16, color=INK, ha="left"):
    ax.text(x, y, t, fontproperties=fp, fontsize=size, color=color, ha=ha, va="center")
ax.add_patch(matplotlib.patches.Circle((52, 129), 8, color=DOT)); text(82, 129, "プロンプト 1 件", size=15, color=MUTED)
ax.plot([320, 348], [129, 129], color=INK, lw=3); text(360, 129, "複雑度ごとの平均同士の比", size=15, color=MUTED)
top, bottom = 262, 960
pitch = (bottom - top) / n_rows
for x0, key, head in [(500, "total_cost_usd", "コストの比（B ÷ A）"), (1000, "real_s", "時間の比（B ÷ A）")]:
    text(x0 + 120, 203, head, BOLD, 16, ha="center")
    for g in range(4):  # 目盛り: 0×〜3×
        ax.plot([x0 + g * 120] * 2, [top - 20, bottom], color=GRID if g != 1 else "#bdbdb5", lw=1.5, zorder=0)
        text(x0 + g * 120, 985, f"{g}×", size=15, color=MUTED, ha="center")
    y = top
    for lv, (name, ps) in LEVELS.items():
        ratio = mean(lv, "B", key) / mean(lv, "A", key)
        if x0 == 500:
            text(40, y + pitch / 2, f"複雑度 {lv}　{name}", BOLD, 17)
        text(x0 + ratio * 120 + 8, y + pitch / 2, f"{ratio:.2f}×", BOLD, 16)
        y += pitch
        y0 = y
        for p in ps:
            ax.add_patch(matplotlib.patches.Circle((x0 + med(p, "B", key) / med(p, "A", key) * 120, y + pitch / 2), 8, color=DOT, zorder=3))
            if x0 == 500:
                text(68, y + pitch / 2, p, size=16, color="#444444")
            y += pitch
        ax.plot([x0 + ratio * 120] * 2, [y0 + pitch * 0.1, y - pitch * 0.1], color=INK, lw=3.5, zorder=4, solid_capstyle="butt")
fig.savefig(img / "complexity-4-ratio.png", facecolor=BG); plt.close(fig)
print("ok:", *sorted(img.glob("complexity-*.png")), sep="\n  ")
