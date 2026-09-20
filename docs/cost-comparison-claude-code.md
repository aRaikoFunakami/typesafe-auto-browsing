# Claude Code から使ったときの速度とコスト: typesafe-auto-browsing と Playwright MCP

`prompts/` の 11 個の目的を、**Claude Code（`claude -p`、haiku）から**、次の 2 つの方法で実行し、実時間とコストを比べた記録です（2026-09-20、各 3 回の中央値）。

- **A: Claude Code + typesafe-auto-browsing**（スキルを使い、CLI を Bash で呼ぶ。ブラウザの操作は TypeSafe が決める）
- **B: Claude Code + Playwright MCP**（Claude Code が Playwright のツールを直接呼ぶ）

[cost-comparison.md](cost-comparison.md) は、Claude Code 単体と typesafe-auto-browsing の CLI 単体の比較です。この文書は、**どちらも Claude Code の中から使う**ときの比較で、条件が違います。

## 結果

11 件 × 2 方式 × 3 回 = 66 回、すべて成功しました（失敗の扱いは下の「実行の条件」）。値は、3 回の中央値です。

| プロンプト | 時間 A / B（秒） | 総コスト A / B（$） | A のほうが |
|---|---|---|---|
| amazon-cheapest-usbc | 63.9 / 109.6 | 0.135 / 0.219 | 速い（1.7 倍）、安い（1.6 倍） |
| yahoo-transit-cheapest-fare | 45.7 / 92.9 | 0.097 / 0.298 | 速い（2.0 倍）、安い（3.1 倍） |
| yahoo-transit-search-only | 41.2 / 88.5 | 0.095 / 0.187 | 速い（2.1 倍）、安い（2.0 倍） |
| yahoo-transit-shortest | 43.7 / 86.5 | 0.103 / 0.226 | 速い（2.0 倍）、安い（2.2 倍） |
| wikipedia-tokyo-tower-height | 40.8 / 40.9 | 0.072 / 0.100 | 同じ、安い（1.4 倍） |
| wikipedia-tokyo-tower-designer | 41.5 / 36.6 | 0.074 / 0.096 | 遅い（1.1 倍）、安い（1.3 倍） |
| wikipedia-eiffel-year | 34.3 / 37.7 | 0.093 / 0.077 | 速い（1.1 倍）、高い（1.2 倍） |
| hn-most-comments | 34.2 / 26.0 | 0.080 / 0.068 | 遅い（1.3 倍）、高い（1.2 倍） |
| pypi-requests-version | 25.3 / 15.4 | 0.062 / 0.040 | 遅い（1.6 倍）、高い（1.5 倍） |
| pypi-numpy-license | 27.6 / 16.7 | 0.059 / 0.046 | 遅い（1.7 倍）、高い（1.3 倍） |
| hn-most-points | 37.0 / 17.0 | 0.081 / 0.031 | 遅い（2.2 倍）、高い（2.6 倍） |
| **合計（各プロンプトの中央値の和）** | **435 / 568** | **0.950 / 1.389** | 速い（1.3 倍）、安い（1.5 倍） |

- 「A のほうが」の倍率は、B ÷ A（A が有利なとき）か A ÷ B（A が不利なとき）です。
- 中央値の和は、11 件を 1 回ずつ実行したときの、典型的な合計です。

### A のコストの内訳

`/cost`（`total_cost_usd`）に出るのは、Claude Code の分だけです。A では、TypeSafe の分（CLI が返す `usage.cost_usd`）が別にあり、足して総コストにしています。

| | Claude Code | TypeSafe | 合計 |
|---|---|---|---|
| A（11 件の中央値の和） | $0.813（86%） | $0.135（14%） | $0.950 |
| B（同） | $1.389 | – | $1.389 |

## 読み取れること

- **操作の多い目的（Yahoo 乗換案内、Amazon）は、A が速く、安い。** B は 23〜31 ターンかけて画面を操作しますが、A のターン数は 7〜10 で、ほぼ変わりません。B のコストはターン数に比例して増え、A は増えにくい構造です。
- **ページを開いて読むだけの目的（HN、PyPI）は、B が速く、安い。** B は 4〜5 ターンで終わります。A は、目的にかかわらず、スキルを読む → 前提を確認する Bash が 2 回 → CLI を実行する → スナップショットを Read する、という 7 ターン前後の固定の手順を踏みます。この固定分が、1 件あたり約 $0.06〜0.08、25〜37 秒です。
- **Wikipedia は、どちらも同程度。** 検索して記事を開くまでの手数が、A の固定の手順と釣り合います。
- **CLI 単体の比較（[cost-comparison.md](cost-comparison.md)、約 10 倍）より、差がずっと小さい。** Claude Code の中から使うと、A でも Claude Code のコスト（システムプロンプトのキャッシュ書き込みと読み出し）がコストの 86% を占め、TypeSafe の単価の安さが効く割合が小さくなるためです。

## 答えの確認（成功 ≠ 正しい）

「成功」は、目的のページに着いて、答えが返ったことです（下の「実行の条件」）。答えが正しいかは、自動では確かめていません。全 66 回の回答文を見ると、次の違いがありました。

- **amazon-cheapest-usbc:** A は 3 回とも ¥29（2 本セット、配送料 ¥700）の商品を返しました。B は ¥551、¥551、¥749 の商品を返しています。B は ¥29 の商品を見つけられていません。「一番安い」を満たしているのは、この 3 回では A のほうです（配送料を含めた比較はしていません）。
- **yahoo-transit-shortest:** B-3 だけが「最短 20 時間 41 分」と答えました（ほかの 5 回は 3 時間 25 分）。原因は調べていません。
- **yahoo-transit-cheapest-fare:** A も B も、回によって 4,553 円 / 8,980 円 / 9,020 円と答えが分かれます。検索時刻で出る経路が変わるためと思われますが、どれが「一番安い」かは確かめていません。
- **hn-most-comments、hn-most-points:** 同じ記事を返しますが、コメント数（872 / 875）とポイント数（1674 / 1676）は、実行時刻で変わります。
- **wikipedia、pypi:** どの回も、同じ答えです（東京タワー 333 m、エッフェル塔 1889 年、requests 2.34.2、numpy のライセンス）。

この差が、コストや時間に影響している可能性があります（たとえば、B の Amazon は、別の商品を選んだ分、手数が違う）。

## 実行の条件

### 共通

- モデルは haiku、Chrome は **headed**（`--headless` なし）です。
- プロンプトは `prompts/<名前>.txt` の、`#` で始まらない行です。先頭に、A は「typesafe-auto-browsing をつかって、」、B は「playwright-mcp をつかって、」を付けました。
- 1 つずつ順番に、プロンプトごとに A1 → B1 → A2 → B2 → A3 → B3 の順に実行しました（並列にしていません）。
- 実時間は `/usr/bin/time`（`claude -p` の起動から終了まで。npx での MCP の起動を含む）です。
- コストは、`--output-format stream-json` の最後の `result` の `total_cost_usd`（`/cost` と同じ集計）です。A は、これに、TypeSafe の `usage.cost_usd` を足しました。
- **`-p` の中では `/cost` は使えません**（`total_cost_usd` が同じ集計）。

### 方式ごとの違い

| | A | B |
|---|---|---|
| 作業ディレクトリ | `cc-a`（`.claude/skills/typesafe-auto-browsing/SKILL.md` を置いた） | `cc-b`（空） |
| 設定 | `--setting-sources project` | `--setting-sources ""` |
| MCP | `--strict-mcp-config`（なし） | `--strict-mcp-config --mcp-config`（`@playwright/mcp@latest`、`--browser chrome`、`playwright-mcp.json`） |
| 許可 | `Skill` `Bash` `Read` `Grep` | `mcp__playwright` |
| 共通で禁止 | `WebFetch` `WebSearch` | 同左 |

共通で `--permission-mode dontAsk` です。A の TypeSafe の記録（`~/.typesafe-auto-browsing` 相当）は、実行ごとに分けた場所に置きました。

### 失敗の扱い

失敗は、次のどれかです。

- Claude Code のエラー終了、`is_error`、タイムアウト（15 分）、空の回答。
- A で、`Skill(typesafe-auto-browsing)` が呼ばれない、CLI が呼ばれない、Playwright を直接呼んだ、CLI の `success` が偽。
- B で、Playwright が呼ばれない、Skill や CLI が呼ばれた。

失敗した回は、中央値に含めずに、同じ枠でリトライします。同じプロンプトと方式で 3 回連続して失敗したら、打ち切って「要判断」にします。

**結果:** 実行 67 回のうち、失敗は 0 回、要判断は 0 件でした。実行が 1 回多いのは、判定スクリプトの誤検知（`wikipedia-eiffel-year` の B-1。B が読んだファイルのパスにリポジトリ名が入っていて、「CLI が呼ばれた」と誤判定した）で、自動リトライが 1 回走ったためです。誤検知を直して、その回を成功に訂正しました。集計は、同じ枠の成功のうち最初の 1 件だけを使っています。

### 記録の場所

`bench-results/run-20260920-230727/`（`.gitignore` 済み。ページの内容を含みます）。

- `index.tsv`: 全実行の一覧（プロンプト、方式、回、試行、`session_id`、時間、コスト、ターン数、成否）。
- `<プロンプト>/<方式>-<回>-t<試行>/`: `prompt.txt`、`cmd.sh`（実行したコマンド）、`stream.jsonl`（全出力）、`transcript.jsonl`（Claude Code の会話記録。`claude --resume <session_id>` でも開ける）、`meta.json`（集計と回答文）、A は `tsab-home/`（TypeSafe の記録と最終スナップショット）。
- `progress.log`: 進行の記録。

再現と集計:

```sh
scripts/bench_all.sh bench-results/run-<日時>      # 全件（再開できる）
python3 scripts/bench_summary.py bench-results/run-<日時>   # 中央値の表
```

## 限界

- **各 3 回の中央値です。** 回ごとのばらつきは大きく、たとえば Amazon は、A が 9〜14 ターン・$0.13〜0.22、B が 19〜25 ターン・$0.19〜0.22 と、回によって違いました。
- **答えの正しさは、自動では確かめていません**（上の「答えの確認」）。
- **A では、Claude Code がスキルに渡す目的文を、自分の言葉で書き換えます。** プロンプトの文面そのままではありません（書き換えた目的文は `stream.jsonl` に残っています）。B では、Claude Code が全ページのスクリーンショットを撮って画像から読む回がありました。どちらも、実際の使われ方として、そのまま計測しています。
- **キャッシュの状態は揃えていません。** Claude Code のコストは、システムプロンプトのキャッシュ（1 時間）が効いているかで変わります。A と B を交互に実行したので、両方に同じように影響していると思いますが、確かめていません。
- **時間は、サイトの混み具合やネットワークで変わります。** 実行は 23:07〜23:57 の約 50 分に行いました。
- **モデルは haiku だけです。** より大きなモデルでは、B のターン数や、A のスキルの使い方が変わる可能性があります。
- **TypeSafe のコストは、単価から計算した推定値です**（[cost-comparison.md](cost-comparison.md)）。Claude Code は、実際の請求に基づく集計です。
- **Chrome のプロファイルは、方式ごとに、実行をまたいで共有しています**（A は `profile-A`、B は Playwright MCP の既定）。
