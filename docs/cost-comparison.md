# コスト比較: typesafe-auto-browsing と Claude Code (haiku) + Playwright MCP

`prompts/` の 11 個の目的を、次の 2 つでそれぞれ 1 回ずつ実行し、1 件ごとのコストを比べた記録です（2026-09-20）。

1. **typesafe-auto-browsing**（このリポジトリ。判断は TypeSafe の Jev）
2. **Claude Code + Playwright MCP**（`claude -p`、モデルは haiku）

結果は、実行のたびに変わります（ページの状態、Amazon の 503、PyPI の bot 判定など）。1 回ずつの実測で、平均ではありません。

## 結果

| プロンプト | typesafe-auto-browsing | Claude Code (haiku) | 倍率 |
|---|---|---|---|
| hn-most-comments | $0.0008（3 リクエスト） | $0.0335（4 ターン） | 約 42 倍 |
| hn-most-points | $0.0008（3） | $0.0595（4 ターン） | 約 74 倍 |
| pypi-requests-version | $0.0005（3） | $0.0425（4 ターン） | 約 83 倍 |
| pypi-numpy-license | $0.0006（3） | $0.0453（4 ターン） | 約 78 倍 |
| wikipedia-eiffel-year | $0.0079（34） | $0.0332（4 ターン） | 約 4 倍 |
| wikipedia-tokyo-tower-designer | $0.0154（82） | $0.0273（4 ターン） | 約 1.8 倍 |
| wikipedia-tokyo-tower-height | $0.0153（82） | $0.0495（4 ターン） | 約 3 倍 |
| yahoo-transit-search-only | $0.0115（42） | $0.2813（16 ターン） | 約 24 倍 |
| yahoo-transit-shortest | $0.0119（42） | $0.1471（21 ターン） | 約 12 倍 |
| yahoo-transit-cheapest-fare | $0.0157（55） | $0.3398（40 ターン） | 約 22 倍 |
| amazon-cheapest-usbc | $0.0347（121） | $0.0726（13 ターン）※ | 約 2 倍 |
| **合計** | **$0.115** | **$1.13** | 約 10 倍 |

- ※ Amazon の Claude Code は、目的文の先頭に「playwright-mcp を使って」を足したときの値です（下の「Amazon で Claude Code が断る」）。この行を除いた 10 件の合計は、$0.081 対 $1.06（約 13 倍）です。
- 11 件すべて、両ツールとも目的のページに着き、答えを返しました。**答えが正しいかは、確かめていません。** typesafe-auto-browsing は答えを作らず、最終ページのスナップショットを返す設計で、答えの出し方が違います。
- かっこの中は、typesafe-auto-browsing が TypeSafe に出したリクエスト数（`usage.requests`）と、Claude Code のターン数（`num_turns`）です。

読み取れること:

- **ページを開いて読むだけの目的**（HN、PyPI）: typesafe-auto-browsing は 3 リクエストで終わり、$0.0005〜$0.0008 です。Claude Code は 1 件で $0.03〜$0.06 かかり、差は 40〜80 倍あります。
- **検索してから読む目的**（Wikipedia）: 差は 2〜4 倍に縮まります。typesafe-auto-browsing は 34〜82 リクエストを使うためです。
- **操作が多い目的**（Yahoo 乗換案内）: Claude Code は 16〜40 ターン、1〜2 分かけて $0.15〜$0.34 になります。typesafe-auto-browsing は $0.012〜$0.016 です。
- **Amazon**: typesafe-auto-browsing の 121 リクエストは、503 の再試行が多かったためだと思います。1 回目は 207 リクエスト・$0.0675、2 回目は 121 リクエスト・$0.0347 でした。

## 単価

| | 入力 | キャッシュ書き込み | キャッシュ読み出し | 出力 |
|---|---|---|---|---|
| **TypeSafe Jev 1.13** | **$0.042 / MTok**（$42 / 10 億トークン） | なし | なし | **無料** |
| **Claude Haiku 4.5** | $1 / MTok | $1.25 / MTok（5 分）、**$2 / MTok（1 時間）** | $0.10 / MTok | $5 / MTok |

- 出典: TypeSafe は <https://docs.typesafe.ai/models>（「Charged per input token. Output tokens are free.」、キャッシュの単価の記載はなし）。Haiku 4.5 は <https://platform.claude.com/docs/en/about-claude/pricing>（2026-09-20 に確認）。
- Claude Code の実行では、キャッシュ書き込みはすべて 1 時間のもの（`usage.cache_creation.ephemeral_1h_input_tokens`）で、$2 / MTok で課金されています。
- 1 トークンあたりの単価は、入力で見ると TypeSafe が Haiku の **1/24**（通常の入力）、**1/48**（キャッシュ書き込み）、**1/2.4**（キャッシュ読み出し）です。出力は、TypeSafe が無料、Haiku が $5 / MTok です。

## テストごとのトークン数とコスト

各テストで実際に使ったトークン数と、単価から出したコストです（実行記録の値。1 回ずつの実測）。

### typesafe-auto-browsing（Jev 1.13）

コスト = 入力トークン数 × $0.042 / 100 万（出力トークンは無料なので、コストに入りません）。

| テスト | リクエスト数 | 入力トークン | 出力トークン（無料） | コスト |
|---|---|---|---|---|
| hn-most-comments | 3 | 18,801 | 1,981 | $0.00079 |
| hn-most-points | 3 | 18,801 | 1,981 | $0.00079 |
| pypi-requests-version | 3 | 12,132 | 1,350 | $0.00051 |
| pypi-numpy-license | 3 | 13,801 | 1,177 | $0.00058 |
| wikipedia-eiffel-year | 34 | 188,550 | 12,289 | $0.00792 |
| wikipedia-tokyo-tower-designer | 82 | 367,610 | 14,045 | $0.01544 |
| wikipedia-tokyo-tower-height | 82 | 365,257 | 13,074 | $0.01534 |
| yahoo-transit-search-only | 42 | 274,620 | 23,712 | $0.01153 |
| yahoo-transit-shortest | 42 | 284,051 | 33,949 | $0.01193 |
| yahoo-transit-cheapest-fare | 55 | 373,537 | 41,213 | $0.01569 |
| amazon-cheapest-usbc | 121 | 827,354 | 17,036 | $0.03475 |
| **合計（11 件）** | 470 | 2,744,514 | 161,807 | **$0.1153** |
| 合計（Amazon を除く 10 件） | 349 | 1,917,160 | 144,771 | $0.0805 |

### Claude Code（haiku）+ Playwright MCP

コスト = 未キャッシュ入力 × $1 + キャッシュ書き込み（1 時間）× $2 + キャッシュ読み出し × $0.10 + 出力 × $5（いずれも 100 万トークンあたり）。

| テスト | ターン数 | 未キャッシュ入力 | キャッシュ書き込み | キャッシュ読み出し | 出力 | コスト |
|---|---|---|---|---|---|---|
| hn-most-comments | 4 | 36 | 7,170 | 118,416 | 1,451 | $0.0335 |
| hn-most-points | 4 | 36 | 20,323 | 118,171 | 1,391 | $0.0595 |
| pypi-requests-version | 4 | 36 | 13,844 | 117,970 | 596 | $0.0425 |
| pypi-numpy-license | 4 | 36 | 14,072 | 118,046 | 1,059 | $0.0453 |
| wikipedia-eiffel-year | 4 | 36 | 8,875 | 118,345 | 725 | $0.0332 |
| wikipedia-tokyo-tower-designer | 4 | 36 | 5,966 | 118,686 | 695 | $0.0273 |
| wikipedia-tokyo-tower-height | 4 | 36 | 16,391 | 118,803 | 958 | $0.0495 |
| yahoo-transit-search-only | 16 | 132 | 82,420 | 964,431 | 3,967 | $0.2813 |
| yahoo-transit-shortest | 21 | 174 | 22,386 | 793,937 | 4,556 | $0.1471 |
| yahoo-transit-cheapest-fare | 40 | 332 | 46,030 | 1,985,436 | 9,767 | $0.3398 |
| amazon-cheapest-usbc ※ | 13 | 記録なし | 記録なし | 記録なし | 記録なし | $0.0726 |
| **合計（Amazon を除く 10 件）** | 105 | 890 | 237,477 | 4,572,241 | 25,165 | **$1.0589** |
| 合計（Amazon ※ を含む 11 件） | 118 | – | – | – | – | $1.1315 |

- ※ Amazon は、目的文の先頭に「playwright-mcp を使って」を足して、手元で別に実行した結果です。コストとターン数は、その出力から書き写しました。トークン数は、実行記録が残っていないので、ありません。元の目的文の実行は断られた（1 ターン、$0.0131）ので、載せていません。

### Claude Code のコストの内訳（$）

どの種類のトークンに、いくらかかったかです。

| テスト | 未キャッシュ入力 | キャッシュ書き込み | キャッシュ読み出し | 出力 | 合計 |
|---|---|---|---|---|---|
| hn-most-comments | 0.0000 | 0.0143 | 0.0118 | 0.0073 | $0.0335 |
| hn-most-points | 0.0000 | 0.0406 | 0.0118 | 0.0070 | $0.0595 |
| pypi-requests-version | 0.0000 | 0.0277 | 0.0118 | 0.0030 | $0.0425 |
| pypi-numpy-license | 0.0000 | 0.0281 | 0.0118 | 0.0053 | $0.0453 |
| wikipedia-eiffel-year | 0.0000 | 0.0177 | 0.0118 | 0.0036 | $0.0332 |
| wikipedia-tokyo-tower-designer | 0.0000 | 0.0119 | 0.0119 | 0.0035 | $0.0273 |
| wikipedia-tokyo-tower-height | 0.0000 | 0.0328 | 0.0119 | 0.0048 | $0.0495 |
| yahoo-transit-search-only | 0.0001 | 0.1648 | 0.0964 | 0.0198 | $0.2813 |
| yahoo-transit-shortest | 0.0002 | 0.0448 | 0.0794 | 0.0228 | $0.1471 |
| yahoo-transit-cheapest-fare | 0.0003 | 0.0921 | 0.1985 | 0.0488 | $0.3398 |
| **合計（10 件）** | 0.0009 | 0.4750 | 0.4572 | 0.1258 | **$1.0589** |

- キャッシュ書き込み（$2 / MTok）と読み出し（$0.10 / MTok）が、コストの大半です。ターンごとに約 3 万トークンの system prompt とツール定義を、送り直しているためです。
- 出力（答えの文章と、ツールを呼ぶための出力）は、コストの約 12% です。TypeSafe は出力が無料です。

## 差の妥当性

「Claude Code の合計が約 10〜13 倍」という差が、単価とトークン数から説明できるかを、実行記録（`claude -p` の `modelUsage`、typesafe-auto-browsing の `usage`）のトークン数で確かめました。Amazon は、Claude Code の有効な実行記録（トークン数）が残っていないので、除いた 10 件で見ます。

### ① 記録のコストは、トークン数と単価から再現できる

- **Claude Code:** 10 件すべてで、`入力 × $1 + 出力 × $5 + キャッシュ書き込み × $2 + キャッシュ読み出し × $0.10`（MTok あたり）が、`total_cost_usd` と小数点以下 4 桁まで一致しました。
- **typesafe-auto-browsing:** 10 件すべてで、`input_tokens × $0.042 / MTok` が `usage.cost_usd` と一致しました。出力トークンは、コストに入りません。

つまり、表のコストは、上の単価表のとおりに計算された値です。

### ② 差は「単価の差」と「入力トークン数の差」に分けられる

10 件の合計です。

| | typesafe-auto-browsing | Claude Code (haiku) | 比 |
|---|---|---|---|
| 入力側のトークン数 | 191.7 万 | 481.1 万（未キャッシュ 890 + 書き込み 23.7 万 + 読み出し 457.2 万） | Claude Code が **2.5 倍** |
| 入力側の平均単価 | $0.042 / MTok | $0.194 / MTok（読み出しが多いので、$1 より大きく下がる） | Claude Code が **4.6 倍** |
| 入力側のコスト | $0.081 | $0.933 | 11.6 倍（2.5 × 4.6） |
| 出力のコスト | $0（無料） | $0.126（出力 2.5 万トークン × $5） | – |
| **合計** | **$0.081** | **$1.059** | **13.1 倍** |

- 「入力トークン数 2.5 倍 × 平均単価 4.6 倍 = 11.6 倍」に、出力のコストが上乗せされて、13.1 倍になります。
- 差の大きな要因は、TypeSafe の単価が安いこと（入力 $0.042 / MTok、出力は無料）です。Claude Code は、ターンごとに約 3 万トークンの system prompt とツール定義を送り、その分がキャッシュ読み出し（$0.10）と、1 時間キャッシュの書き込み（$2）として、毎回課金されます。

### ③ 1 件ごとの差も、同じ分解で説明できる

| プロンプト | TS の入力トークン | CC の入力側トークン | トークン数の比 | CC の平均単価（TS の何倍か） | コストの比 |
|---|---|---|---|---|---|
| hn-most-comments | 1.9 万 | 12.6 万 | 6.7 倍 | $0.209 / MTok（5.0 倍） | 42 倍 |
| hn-most-points | 1.9 万 | 13.9 万 | 7.4 倍 | $0.379 / MTok（9.0 倍） | 75 倍 |
| pypi-numpy-license | 1.4 万 | 13.2 万 | 9.6 倍 | $0.303 / MTok（7.2 倍） | 78 倍 |
| pypi-requests-version | 1.2 万 | 13.2 万 | 10.9 倍 | $0.300 / MTok（7.1 倍） | 83 倍 |
| wikipedia-eiffel-year | 18.9 万 | 12.7 万 | **0.7 倍** | $0.233 / MTok（5.5 倍） | 4 倍 |
| wikipedia-tokyo-tower-designer | 36.8 万 | 12.5 万 | **0.3 倍** | $0.191 / MTok（4.6 倍） | 2 倍 |
| wikipedia-tokyo-tower-height | 36.5 万 | 13.5 万 | **0.4 倍** | $0.331 / MTok（7.9 倍） | 3 倍 |
| yahoo-transit-search-only | 27.5 万 | 104.7 万 | 3.8 倍 | $0.250 / MTok（5.9 倍） | 24 倍 |
| yahoo-transit-shortest | 28.4 万 | 81.6 万 | 2.9 倍 | $0.152 / MTok（3.6 倍） | 12 倍 |
| yahoo-transit-cheapest-fare | 37.4 万 | 203.2 万 | 5.4 倍 | $0.143 / MTok（3.4 倍） | 22 倍 |

（「コストの比」は、出力のコストを含めた値なので、「トークン数の比 × 単価の比」とは少し違います。）

- **HN と PyPI で 40〜80 倍になるのは:** typesafe-auto-browsing は、ページを開いて 3 回だけ TypeSafe に聞くので、入力は 1〜2 万トークンです。Claude Code は、4 ターンで約 13 万トークンを処理します。この約 13 万は、system prompt の約 3 万トークンを、毎ターン読み直す分が大半です。トークン数が 7〜11 倍で、単価が 5〜9 倍なので、掛けて 40〜80 倍になります。
- **Wikipedia で 2〜4 倍に縮むのは:** typesafe-auto-browsing は、検索や記事のページで 34〜82 回聞くので、入力が 19〜37 万トークンになります。Claude Code の約 13 万トークンより**多い**（0.3〜0.7 倍）のです。単価が 5〜8 倍安くても、トークン数で逆転しているぶん、差が縮みます。
- **Yahoo で 12〜24 倍になるのは:** Claude Code が 16〜40 ターンかけて、80〜200 万トークンを処理するためです。typesafe-auto-browsing は 42〜55 回聞いて、27〜37 万トークンです。

### ④ 差の一部は、キャッシュの状態で変わる

- Claude Code は、キャッシュ書き込み（$2 / MTok）が大きな割合を占めます。10 件の合計では、コストの約 45%（$0.475）が、書き込みです。実行ごとの書き込みは 0.6 万〜8.2 万トークンで、ばらつきます。直前の実行のキャッシュが残っていれば、書き込みは減ります。
- 試しに、**キャッシュ書き込みをすべて無料**にしても、Claude Code の合計は $0.584 で、typesafe-auto-browsing の 7.3 倍です。つまり、差の大きさは、キャッシュの状態だけでは説明できません。
- 一方で、書き込みのトークン数は実行ごとにばらつくので、「約 13 倍」は、この実行でのキャッシュの状態を反映した値です。キャッシュが温まった状態で続けて実行すれば、差は小さくなる可能性があります（確かめていません）。

### 結論

**差は妥当です。** 記録のトークン数と公表の単価から、コストを再現でき、差の原因も分解できます。

- 原因の 1 つ目は、TypeSafe の入力単価が安く（Haiku の 1/24）、出力が無料であること。
- 原因の 2 つ目は、Claude Code が、毎ターン約 3 万トークンの固定の文脈を送り直すこと。ページを 1 つ読むだけの目的では、これが支配的です。
- 逆に、typesafe-auto-browsing は、検索や記事の読み込みが多い目的では、Claude Code より多くのトークンを送ります。その場合の差は 2〜4 倍まで縮みます。

### この検証の限界

- **どちらも単価表からの計算値です。** Claude Code の `modelUsage` は `costBasis: "list"`（定価ベース）で、実際の請求（サブスクリプションや割引）とは限りません。typesafe-auto-browsing も、ドキュメントの単価から計算した値です。
- **トークンの数え方が違います。** TypeSafe と Claude では、トークナイザが違うので、同じテキストでもトークン数は同じになりません。「トークン数の比」は目安です。
- **やっていることが同じではありません。** typesafe-auto-browsing は答えを作らず最終ページを返し、Claude Code は答えの文章まで作ります（出力トークンのコストに含まれます）。
- **1 回ずつの実測です。** ばらつきは見ていません。

## 倍率が違う理由（3 件の調べ）

倍率の大きいもの・小さいもの・中間の 1 件ずつを、両ツールの実行記録（typesafe-auto-browsing は `logs/*.jsonl`、Claude Code は `~/.claude/projects/` のセッション記録）で、1 ステップずつ調べました。

| | 倍率 | typesafe-auto-browsing | Claude Code (haiku) |
|---|---|---|---|
| **pypi-requests-version** | **83 倍** | 3 リクエスト、12,132 トークン、$0.00051 | 4 ターン、$0.0425 |
| **yahoo-transit-shortest** | **12 倍** | 42 リクエスト、284,051 トークン、$0.0119 | 21 ターン、$0.1471 |
| **wikipedia-tokyo-tower-designer** | **1.8 倍** | 82 リクエスト、367,610 トークン、$0.0154 | 4 ターン、$0.0273 |

### 仕組みの違い（先に）

- **typesafe-auto-browsing** は、TypeSafe への 1 回ごとのリクエストが独立していて、前のやり取りは積み上がりません（`state.history` の短い記録だけ）。コストは、**各リクエストに入れたページ（state）の大きさの合計**で決まります。長いページは、約 8,000 文字のパートに切り、パートごとに別のリクエスト（`outcome` と `control`）で判定するので、ページが長いほどリクエスト数も増えます。
- **Claude Code** は、会話が 1 つに積み上がります。ターンごとに、**それまでの全部**（約 3 万トークンの system prompt とツール定義に、これまでのツールの結果を足したもの）を送り直し、キャッシュ読み出し（$0.10 / MTok）で課金されます。新しく増えた分は、キャッシュ書き込み（$2 / MTok）と、出力（$5 / MTok）です。コストは、**ターン数 × それまでの文脈の大きさ**で決まります。

### ① 83 倍: pypi-requests-version（ページを 1 つ読むだけ）

両ツールとも、読んだ内容はほぼ同じで、ページのスナップショット（約 2.8 万文字、約 9 千トークン）を 1 回だけ読んでいます。

- **typesafe-auto-browsing（3 リクエスト）:** ①何も開いていない状態で、次のツールを選ぶ（1,159 トークン）→ ②URL を選ぶ（1,257）→ ③ページを渡して「達成したか」を聞く（9,716）。達成の確率が高いので、そこで終わります。合計 12,132 トークンで、$0.042 / MTok なので $0.00051 です。
- **Claude Code（4 ターン）:** ①`ToolSearch` で Playwright のツールを読み込む → ②`browser_navigate` → ③`browser_snapshot`（結果 27,819 文字）→ ④答えを書く。

Claude Code のコストの内訳（合計 $0.0425）:

| 項目 | トークン | 単価 | コスト |
|---|---|---|---|
| キャッシュ書き込み | 13,844（うち、ページのスナップショットが 8,763） | $2 / MTok | $0.0277 |
| キャッシュ読み出し | 117,970（約 3 万トークンの固定の文脈 × 4 ターン） | $0.10 / MTok | $0.0118 |
| 出力 | 596 | $5 / MTok | $0.0030 |

- **同じ約 9 千トークンのページでも**、typesafe-auto-browsing は約 $0.0004（入力 $0.042 / MTok）、Claude Code は約 $0.0175（書き込み $2 / MTok）です。トークン数は同じで、単価だけで約 43 倍の差があります。
- さらに Claude Code は、**固定の文脈の読み直しだけで $0.0118** かかります。これだけで、typesafe-auto-browsing の合計の 23 倍です。
- 仕事が小さい（1 ページ、1 回の操作）ので、Claude Code の固定費（毎ターン約 3 万トークン）が、そのまま倍率になっています。**倍率が最大になるのは、この型の目的です**（HN の 2 件、PyPI の 2 件が同じ）。

### ② 12 倍: yahoo-transit-shortest（フォームを操作して、結果を読む）

- **typesafe-auto-browsing（42 リクエスト、284,051 トークン）:** 実行した操作は 3 つだけ（`browser_navigate` → `browser_fill_form` → `browser_click`）です。トークンの内訳は次のとおりです。

  | 種類 | リクエスト数 | 入力トークン |
  |---|---|---|
  | ページのパートの判定（`outcome`、`control`） | 27 | 75,583 |
  | 次のツールの選択と、達成の判定（`done`、`tool`） | 4 | 46,101 |
  | 引数の質問（`value`、`target`、`type` など） | 11 | 162,367 |

  引数の質問は、ほとんどが約 4.8 万文字のページを含むので、1 回あたり 1.3〜2 万トークンです（URL の質問だけは、ページが空なので 4 千トークン）。それでも、合計は 28 万トークンで、$0.042 / MTok なので $0.0119 です。
- **Claude Code（21 ターン、$0.1471）:** 操作が試行錯誤になりました。ツールの呼び出しは、`browser_click` 5 回、`browser_take_screenshot` 4 回、`browser_evaluate`（ページの中で動かす JavaScript を、自分で書く）4 回、`browser_fill_form` 2 回、`browser_wait_for` 2 回、`browser_navigate` と `ToolSearch` です。入力欄の候補（オートコンプリート）の選択に手間取り、最初のフォームの入力が 2 回に分かれています。
  - 固定の文脈（約 3 万トークン）に、ターンごとの結果が積み上がり、**文脈が 3.0 万 → 4.7 万トークンに増えました**。これを 21 回読み直した分が、キャッシュ読み出し 793,937 トークン（$0.0794）です。
  - 新しく増えた分の書き込みが 22,386 トークン（$0.0448）、出力が 4,556 トークン（$0.0228）です。
- 両ツールとも、作業が増えるにつれてコストが増えます。ただし、Claude Code は**ターンが増えるほど、毎ターンの読み直す量も増える**ので、増え方が大きくなります。倍率は、①の 83 倍から 12 倍まで縮みます。

### ③ 1.8 倍: wikipedia-tokyo-tower-designer（長い記事から 1 つの事実を探す）

このテストは、typesafe-auto-browsing のほうが、Claude Code より**多くのトークンを使う**例です（コストは、単価が安いので、typesafe-auto-browsing のほうが低いままです）。

- **typesafe-auto-browsing（82 リクエスト、367,610 トークン）:** トップページで「東京タワー」を検索し、記事のページを開きます。この記事のスナップショットは **644,769 文字**あります。約 8,000 文字のパートに切り、**75 回**、パートごとに `outcome` と `control` を聞きます（271,581 トークン、入力全体の 74%）。そのあと、確率の高いパートをつなげた約 4.5 万文字を、`done` と `tool` の質問に入れます。記事全体を、TypeSafe が読んでいることになります。
- **Claude Code（4 ターン、$0.0273）:** 検索をせず、記事の URL（`/wiki/東京タワー`）へ直接移動しました。そのあと `browser_find` で「設計者」を探しました。**結果は 905 文字だけ**で、記事のスナップショットは一度も読んでいません。書き込みは 5,966 トークン、読み出しは 118,686 トークン（固定の文脈の読み直し）です。
- 入力トークン数は、Claude Code が約 12.5 万、typesafe-auto-browsing が約 36.8 万で、**Claude Code のほうが少ない**（0.3 倍）です。TypeSafe の単価が Claude Code の平均より 4.6 倍安い分が効いて、コストの差は 1.8 倍に縮みます。
- 差を縮めたのは、Claude Code が「ページ全体を読まず、`browser_find` で必要な所だけを取る」という手を選んだことです。`browser_snapshot` で記事を読んでいたら、約 64 万文字なので、Haiku の文脈（20 万トークン）に収まらなかった可能性が高いです（確かめていません）。

### まとめ

| 目的の型 | typesafe-auto-browsing のコストの決まり方 | Claude Code のコストの決まり方 | 倍率 |
|---|---|---|---|
| 短いページを読む（HN、PyPI） | ページ 1 つ分（1〜2 万トークン）× $0.042 | 固定の文脈 × 4 ターン + 書き込み。仕事に対して固定費が大きい | **40〜80 倍** |
| フォームを操作する（Yahoo） | 操作ごとに、ページ（約 4.8 万文字）を入れた質問が数回 | ターンが増え、文脈も膨らむ | **12〜24 倍** |
| 長い記事から探す（Wikipedia） | 記事全体を、パートに分けて読む（記事が長いほど増える） | `browser_find` などで、必要な所だけを取れば、少なく済む | **2〜4 倍** |

- 倍率を決めるのは、**両ツールが読むページの量**と、**Claude Code のターン数**です。単価の差（入力 $0.042 対 $1 / $2 / $0.10）は、常にありますが、読む量が逆転すると、倍率は縮みます。
- **1 回ずつの調べです。** Claude Code のターン数や、選ぶ手（`browser_find` を使うか、`browser_evaluate` を使うか）は、実行のたびに変わります。Wikipedia の Claude Code が今回、検索を飛ばして URL を直接開いたのも、その 1 回の選択です。

## テスト方法

### コストの取り方

- **typesafe-auto-browsing**: `--json` の出力の `usage.cost_usd` です。README の式（Jev 1.13、入力 $42 / 10 億トークン、出力は無料）で計算した**推定値**です。Playwright MCP 自体の費用はありません。
- **Claude Code**: `claude -p --output-format json` の `total_cost_usd` です。`/cost` と同じ集計です。

### 実行の条件

両ツールとも、次を揃えました。

- 目的文は `prompts/<名前>.txt` の、`#` で始まらない行です。
- Playwright MCP は `@playwright/mcp@latest`、`--browser chrome`、`--config src/typesafe_auto_browsing/playwright-mcp.json`（同じ設定ファイル）です。
- プロンプトごとに、typesafe-auto-browsing → Claude Code の順に、1 つずつ実行しました（同時には動かしません）。
- **headless**: `--headless` で実行しました。ただし PyPI の 2 件だけは、headless なしです（下の「PyPI の bot 判定」）。

### 実行コマンド

typesafe-auto-browsing:

```sh
uv run typesafe-auto-browsing -f prompts/<名前>.txt --json --headless 2>/dev/null
```

Claude Code（`mcp.json` は下に示します）:

```sh
mkdir -p /tmp/cc-cwd && cd /tmp/cc-cwd
claude -p "$(grep -v '^#' /path/to/prompts/<名前>.txt | grep -v '^$')" \
  --model haiku --output-format json \
  --strict-mcp-config --mcp-config mcp.json \
  --allowedTools "mcp__playwright" --disallowedTools WebFetch WebSearch \
  --setting-sources "" --permission-mode dontAsk
```

`mcp.json`:

```json
{"mcpServers":{"playwright":{"command":"npx","args":["-y","@playwright/mcp@latest","--browser","chrome","--headless","--config","/path/to/src/typesafe_auto_browsing/playwright-mcp.json"]}}}
```

各オプションの理由:

| オプション | 理由 |
|---|---|
| 空のディレクトリで実行 | このリポジトリの CLAUDE.md や memory を読ませない |
| `--setting-sources ""` | ユーザー設定の hooks などを読ませない（読ませると、追加の文脈が入りコストが変わる） |
| `--strict-mcp-config` | 指定した Playwright MCP だけを使う（ほかの MCP は使わない） |
| `--allowedTools "mcp__playwright"` | Playwright MCP のツールを許可する |
| `--disallowedTools WebFetch WebSearch` | 付けないと、Claude Code が Playwright を使わず WebFetch で読もうとして、権限拒否で終わる |
| `--permission-mode dontAsk` | 許可されていないツールは、聞かずに拒否する（`-p` で止まらないため） |

Claude Code は、ビルトインのツールを含む約 3 万トークンの system prompt を毎回送ります。「1+1は？」だけでも、最初の 1 回は約 $0.06 でした。表の Claude Code の値には、この分（キャッシュが効いた分を含む）が入っています。

## 途中でやり直したこと

最終の表の値は、次の 3 つの問題を直した後のものです。

### WebFetch が先に使われた

最初の実行では、`--disallowedTools` を付けていませんでした。Claude Code は Playwright ではなく WebFetch を使おうとし、権限拒否で、11 件中 7 件が答えを返せず終わりました。`--disallowedTools WebFetch WebSearch` を付けて、Claude Code 側の 11 件をやり直しました。

### Amazon で Claude Code が断る

`prompts/amazon-cheapest-usbc.txt` の目的文（「https://www.amazon.co.jp/ で一番やすいusb-cケーブルをさがして」）のままだと、Claude Code (haiku) はブラウザを操作せず、1 ターンで「ソフトウェアエンジニアリング専用なので、Amazon の商品検索はできない」と断ります（2 回とも。$0.013）。Playwright の設定の問題ではなく、プロンプトの解釈の問題です。

目的文の先頭に「playwright-mcp を使って」を足すと、13 ターンで実行し、¥29 の商品を返しました（$0.0726）。表には、この値を入れています。**この 1 件だけ、目的文が元のものと違います。** 他の 10 件は、元の目的文のままです。

### PyPI の bot 判定

`pypi-numpy-license` を `--headless` で実行すると、typesafe-auto-browsing は失敗（`stuck: repeated browser_navigate`）し、Claude Code は 1 回目に CAPTCHA で止まりました（2 回目は成功）。

失敗した実行の記録（`logs/20260920-162337.jsonl`）では、ページのタイトルが `Client Challenge` で、スナップショットには `Fastly Logo` と `Enter the characters seen…` の入力欄がありました。numpy のページではなく、CDN の Fastly が出す bot 判定のページです。typesafe-auto-browsing は、入力欄に入れる値がないため `browser_type` を使えず、戻って同じ URL を開き直すことを繰り返して `stuck` で終わります。

`--headless` なしでは、同じ目的が両ツールとも成功しました（typesafe-auto-browsing は 3 リクエスト、$0.0006）。headless の Chrome は、headed の Chrome と、判定に使われる値が少し違うためだと思いますが、どの値が決め手かは確かめていません。CAPTCHA の突破はしない方針なので、この 2 件（`pypi-numpy-license`、`pypi-requests-version`）は、headless なしで両ツールをやり直した値を表に入れています。

## 限界

- 各目的 1 回ずつの実測で、ばらつきは見ていません。
- typesafe-auto-browsing のコストは、単価から計算した推定値です。Claude Code は、実際の請求に基づく集計です。
- 答えの正しさは比べていません。「両ツールが目的のページに着き、答えを返した」までです。
- Claude Code は haiku だけです。ほかのモデル、`--headless` の有無（PyPI の 2 件以外）、`--max-steps` などの違いは見ていません。
- Amazon の Claude Code の値は、目的文が違います（上を参照）。
