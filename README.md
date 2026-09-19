# typesafe-auto-browsing

ブラウザ操作の目的を受け取り、Playwright MCP (Chrome) を操作して達成する CLI。
どのツールを使うか・次に何をするか・終わったかの判断は [TypeSafe](https://docs.typesafe.ai) が行います。

## 使い方

```sh
export TYPESAFE_API_KEY=...   # https://console.typesafe.ai/
uv run typesafe-auto-browsing "yahooの路線検索で横浜から青森までを検索して"
```

目的文には、開きたい URL や入力するテキストをそのまま書いてください（例: `"https://transit.yahoo.co.jp/ で横浜から青森までを検索して"`）。
値は目的文の一部（または画面上の文字列）から選ぶだけで、生成はしません。

- 目的を省略すると対話入力になります。
- `-f/--file` : 目的をファイルから読む（`prompts/` にサンプル。`#` で始まる行はコメント）
- `--json` : 最後に結果を 1 つの JSON で標準出力に出す（進行のログは標準エラー）。`--dry-run` と併用するとツール確率の JSON
- `--dry-run` : 目的にどのツールが要りそうかの確率を表示するだけで、ブラウザは操作しません（`--json` で JSON 出力）。実行時は全ツールが候補です
- `-t/--threshold` : `--dry-run` で印を付けるしきい値（既定 0.5）
- `--confirm` : 変更を伴うツール呼び出しの前に、ツール名と引数（対象要素、画面上の文字列を使う値）を表示して y/n を聞く（端末が必要。既定ではオフ）
- `--max-steps` : 最大ステップ数（既定 20）
- `--done-threshold` : 「目的達成」とみなす確率（既定 0.8）
- `--headless` : Chrome をウィンドウなしで実行
- `--log-dir` : 実行の全記録の保存先（既定 `logs`）

実行の最後に、TypeSafe のリクエスト数・トークン数・コストを表示します（`--dry-run --json` では `usage` キー）。
コストは、[ドキュメント](https://docs.typesafe.ai/models)の単価（Jev 1.13: 入力 $42 / 10 億トークン、出力は無料）から計算した推定値です。

## 答え

目的が「〜を教えて」のように何かを見つけることを求めるとき、目的を達成したあとの最終ページから答えを出します（`answer.py`）。答えの直前にページを読み直し、確信度が低いとき（更新中のページなど）は 2 秒待って最大 3 回読み直します。
TypeSafe は文章を作れないので、答えは **ページ上の文字列を TypeSafe が選び、コードがそのまま写したもの** です。

1. 目的が何かを見つけて報告することを求めているか（Noul）。操作だけの目的（「検索して」）なら答えは空
2. 数量で比べる目的か（最安・最多・最短など）と、最小か最大か。比べる場合は、目的文のどの語が比べる量を指すか（「所要時間」「やすい」など）を、目的文の部分から選ぶ
3. ページを断片に分け、断片ごとに「その量（または事実）を述べた文字列」を Choice で選ぶ。確率の高い候補（最大 3 つ）を残す
4. 全断片の候補から、最小・最大のもの（または事実を述べたもの）を Choice で選ぶ
5. 比べる場合は、その値が属するものの名前（商品名・記事名・経路名）を、値の周辺から選ぶ。リンクなら、直下の `/url:` 行を写して URL にする

```json
{
  "goal": "https://news.ycombinator.com/ で一番ポイントが多い記事のタイトルを教えて",
  "success": true,
  "reason": "goal achieved (p=0.89)",
  "page": {"url": "https://news.ycombinator.com/", "title": "Hacker News"},
  "answers": [
    {"role": "value", "text": "861 points by", "confidence": 0.98, "source": "page", "url": null},
    {"role": "subject", "text": "Android 17 is the first since 3.x to add new APIs without releasing to the AOSP", "confidence": 0.92, "source": "page", "url": "https://grapheneos.social/@GrapheneOS/117282080803799576"}
  ],
  "answers_note": "answered",
  "usage": {"requests": 20, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
  "trace": "logs/….jsonl"
}
```

- `value` は目的が求める量・事実、`subject` はそれが属するものの名前（事実を読む目的では出ません）。`confidence` は TypeSafe の確率です。
- 見つからないときは推測せず、`answers` を空にして `answers_note` に理由を入れます。
- 制約: 最終の 1 ページの中だけから選びます（詳細ページへの移動はしません）。文字列はページのテキストのまま（文の断片や、連結された文字列のこともあります）。一覧の上位 N 件のような複数の答えは未対応です。

## サンプルの目的（`prompts/`）

使いまわせるように、目的を 1 つずつファイルにしてあります。`#` で始まる行はコメントで、何を確かめる目的か・期待する答えを書いています。

```sh
uv run typesafe-auto-browsing -f prompts/amazon-cheapest-usbc.txt
uv run typesafe-auto-browsing -f prompts/hn-most-points.txt --json --headless 2>/dev/null
for f in prompts/*.txt; do uv run typesafe-auto-browsing -f "$f" --json --headless 2>/dev/null | jq -c '{goal, success, answers: [.answers[].text]}'; done
```

| ファイル | 確かめること |
|---|---|
| `amazon-cheapest-usbc.txt` | 最安（最小）の価格と商品名 |
| `hn-most-points.txt` / `hn-most-comments.txt` | 最多（最大）。比べる量が違う |
| `wikipedia-tokyo-tower-height.txt` / `wikipedia-eiffel-year.txt` | 検索してから事実を読む |
| `yahoo-transit-shortest.txt` / `yahoo-transit-cheapest-fare.txt` | 経路全体の最短時間・最安料金 |
| `yahoo-transit-search-only.txt` | 操作だけの目的（答えは空が正しい） |
| `pypi-requests-version.txt` / `pypi-numpy-license.txt` | ページを開いて事実を読む |

新しい目的を足すときは、URL から始まる 1 行に、先頭のコメントで期待を書いたファイルを置くだけです（`tests/test_prompts.py` が、全ファイルに URL とコメントがあることを確認します）。

## 実行記録

実行ごとに `logs/<日時>.jsonl` へ、あとで調査できるよう省略なしで記録します（`logs/` は git 管理外。ページ内容や入力した文字列を含むので、ファイルは所有者だけが読める権限 0600 / ディレクトリは 0700 で作ります。保持期間の管理はなく、削除は手動です）。大きなスナップショットは `logs/<日時>/` の別ファイルに全文を保存します。1 行 1 イベントで、`seq` / `time` / `elapsed_s` / `kind` を持ちます。

| kind | 内容 |
|---|---|
| `run_start` | 目的文、コマンドライン引数 |
| `mcp_tools` | Playwright MCP のツール一覧（説明・`input_schema`） |
| `tools_selected` | `--dry-run` のツール確率 |
| `page_view` | 長いページで TypeSafe に見せた部分と、各部分の確率 |
| `typesafe_request` / `typesafe_response` | TypeSafe に送った `state`（目的・履歴・ページ）と `questions`、返ってきた回答（確率つき）・トークン数 |
| `typesafe_error` | TypeSafe のエラー（`max_tokens_exceeded` の再試行も含む） |
| `mcp_call` / `mcp_result` | ツール呼び出しの引数と、レスポンス全文（大きいものは `text_file` のパス）・所要時間 |
| `log` | 画面に表示した行 |
| `answers` / `answer_quantity` | 答えと、比べる量に選ばれた語 |
| `outcome` / `error` | 結果と使用量 / 例外 |

```sh
jq -c 'select(.kind=="typesafe_response") | .response.answers' logs/20260919-184251.jsonl
jq -r 'select(.kind=="mcp_result" and .tool=="browser_snapshot") | .text_file' logs/20260919-184251.jsonl
```

## 仕組み

1. `npx @playwright/mcp@latest --browser chrome` を MCP (stdio) で起動し、`list_tools` でツール一覧を取得
2. 目的を達成するまで次を繰り返す（`agent.py`）
   1. `browser_snapshot` の出力は加工せず、全文をトレースの隣にファイルとして保存する（`logs/<日時>/`）。TypeSafe に渡すのは、そのうち必要な部分だけ（`page_view.py`）:
      5 万文字以下のページはそのまま渡す。それより長いページは約 8,000 文字の「部分」にそのまま分け、TypeSafe が 2 つの Choice
      （「成果が出ている部分」「次に操作する部品がある部分」）で選んだ部分だけを、ページの順序どおりに渡す（説明が窓に収まらないときは説明を短くして再試行）
   2. **TypeSafe**: 「目的は達成済みか」(Noul) と「次に呼ぶツール」(Choice) を同時に判断。候補は、スキーマ上使える全ツール。
      ダイアログやファイル選択が開いているときは、`Modal state` が示すツール（例: `browser_handle_dialog`）だけ
   3. **TypeSafe**: 選ばれたツールの引数を、MCP ツールの `input_schema` に従って決める（`arguments.py`）。まず選択と要素、次に値:
      - 要素の参照（`target` / `ref` / `…Target`）→ スナップショット中の `[ref=…]` から Choice（失敗した要素は、ページが変わるまで候補から外す。任意なら「なし」を選べる）
      - `enum` → Choice（任意なら「指定しない」を選べる）、真偽値 → Noul、`modifiers` のような enum の配列 → 項目ごとの Noul
      - 自由な文字列・数値 → 目的文の部分（同じ種類の文字の並び。空白を含んでもよい）を候補に Choice。決まらないときだけ、画面上の要素の名前を候補にする。
        `index` は直前の出力に並ぶ番号も候補。`browser_press_key` の `key` は Playwright のキー名一覧（`keys.py`）も候補。
        コードが選ばれた候補をそのままコピーする
      - 文字列の配列（`browser_select_option` の `values` など）→ 選んだ要素の下に並ぶ `option` のラベル、なければ文字列の候補から Choice
      - `element` / `filename` / `depth` は決めない（`filename` はレスポンスを返さなくするため）
   4. 使えないツール（必須の引数に候補がない、指定するものがない）は、その場で候補から外して選び直す（最大 3 回）。3 ステップ続けて使えるツールがなければ失敗として終了
   5. `--confirm` のときは、変更を伴うツール（MCP の `read_only_hint` が偽）の呼び出し前に人へ確認する。拒否は履歴に残り、TypeSafe は別の手を選ぶ
   6. MCP のツールを呼び出し、結果を履歴に追加。失敗したときは、エラーの先頭 4 行（原因）を履歴に入れる。読み取り専用ツールの出力（例: `browser_snapshot` の `target` 指定）は次のステップの `focus` として TypeSafe に見せる

TypeSafe は文字列を生成できません。値は、目的文にあるか画面に表示されているものです。
長いページでは、選ばれなかった部分は TypeSafe から見えません。全文は `logs/<日時>/` に残ります。

### 提示しないツール

スキーマだけで決められないものは、理由を表示して提示しません。
- `browser_evaluate`: `function` が JavaScript のコード。TypeSafe はコードを書けない

`browser_run_code_unsafe`（コードは任意引数）は、指定するものがないので実質使われません。
`browser_find` は、検索テキストの候補（目的文・画面上の名前）が合わないと「指定するものがない」として外れます。

### 配列の引数（`browser_fill_form` の `fields` など）

オブジェクトの配列は 1 件ずつ決めます。1 件目は必須で、2 件目以降は「もう 1 件要る」を Noul で聞きます（最大 10 件）。
同じ要素は 2 回入力しません。項目の `name`（人が読む名前）は、選んだ要素の名前を画面から写します。

### タブ

クリックで新しいタブが開いても、現在のタブは元のままで、MCP の結果に `### Open tabs` の一覧が出ます。
`browser_tabs`（`select`）の `index` は、その一覧の番号から選びます。

## 安全について

全ツールを候補にするので、目的次第では購入確定や削除などを押すこともあり得ます。`--confirm` を付けると、変更を伴うツールの前に確認が出ます（既定ではオフ）。
`browser_navigate` の URL は目的文にあるものしか選べません（画面上のリンク先 URL は候補にしません）が、入力するテキストは画面上の文字列を使うことがあります（確認の表示に出ます）。
ページの全文が TypeSafe に送られます。Playwright MCP は既定で永続プロファイルを使うので、ログイン済みのセッションがそのまま使われます。

## テスト

```sh
uv run pytest
```

25 ツールのスキーマ（`tests/fixtures/tools.json`）と、TypeSafe・Playwright MCP を台本に差し替えたスタブで、引数の決め方・断片の選択・トレースの権限に加え、ループ（ダイアログの絞り込み、失敗した要素の除外、使えるツールがない場合、確認の拒否・読み取り専用の確認なし、`browser_close`）を確認します。
