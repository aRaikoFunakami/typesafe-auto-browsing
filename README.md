# typesafe-auto-browsing

目的を 1 文で渡すと、Playwright MCP 経由で Chrome を操作して達成する CLI。
次に使うツール、その引数、目的を達成したかどうかは、すべて [TypeSafe](https://docs.typesafe.ai) が判断する。LLM のように文章を生成する処理はない。

```sh
export TYPESAFE_API_KEY=...   # https://console.typesafe.ai/
uv run typesafe-auto-browsing "https://transit.yahoo.co.jp/ で横浜から青森までを検索して"
```

必要なもの: `uv`、`npx`（Node.js）、Chrome、TypeSafe の API キー。

## インストール

```sh
uv tool install git+https://github.com/aRaikoFunakami/typesafe-auto-browsing
export TYPESAFE_API_KEY=...
typesafe-auto-browsing "https://news.ycombinator.com/ で一番ポイントが多い記事のタイトルを教えて"
```

インストールせずに試すなら `uvx --from git+https://github.com/aRaikoFunakami/typesafe-auto-browsing typesafe-auto-browsing "<目的>"`（毎回依存を解決するので遅い）。

## 使う前に

- 全ツールが候補なので、目的によっては購入確定や削除のボタンも押す。`--confirm` を付けると、変更を伴うツールの実行前に y/n を聞く（既定ではオフ）。
- ページの全文が TypeSafe に送られる。
- Playwright MCP は既定で永続プロファイルを使う。ログイン済みのセッションがそのまま使われる。
- `browser_navigate` の URL は目的文に書かれたものしか選べない。画面上のリンク先 URL は候補にしない。入力するテキストには、画面上の文字列が使われることがある（`--confirm` の表示に出る）。

## 目的文の書き方

値（URL、入力する文字列）は、目的文か画面上の文字列から**選ぶ**だけで、TypeSafe は生成しない。開きたい URL や入力したいテキストは、目的文にそのまま書く。

```
https://transit.yahoo.co.jp/ で横浜から青森までを検索して
```

目的を省略すると対話入力になる。ファイルから読むときは `-f`（`prompts/` にサンプルがある。`#` で始まる行はコメント）。

## オプション

| オプション | 内容 |
|---|---|
| `-f`, `--file` | 目的をファイルから読む |
| `--json` | 結果を 1 つの JSON で標準出力に出す。進行のログは標準エラー |
| `--dry-run` | 目的にどのツールが要りそうかの確率を表示するだけで、ブラウザは操作しない。`--json` と併用すると JSON で出す |
| `-t`, `--threshold` | `--dry-run` で印を付けるしきい値（既定 0.5）。実行時は全ツールが候補 |
| `--confirm` | 変更を伴うツールの実行前に、ツール名と引数を表示して y/n を聞く（端末が必要） |
| `--max-steps` | 最大ステップ数（既定 20） |
| `--done-threshold` | 「目的達成」とみなす確率（既定 0.8） |
| `--headless` | Chrome をウィンドウなしで実行 |
| `--log-dir` | 実行記録の保存先（既定 `~/.typesafe-auto-browsing/logs`） |

終了時に、TypeSafe のリクエスト数・トークン数・コストを表示する（`--dry-run --json` では `usage` キー）。
コストは[ドキュメント](https://docs.typesafe.ai/models)の単価（Jev 1.13: 入力 $42 / 10 億トークン、出力は無料）から計算した推定値。

## 答えを返す

目的が「〜を教えて」のように何かを見つけることを求めるとき、達成後の最終ページから答えを出す（`answer.py`）。操作だけの目的（「検索して」）なら答えは空になる。

答えは、**ページ上の文字列を TypeSafe が選び、コードがそのまま写したもの**。要約や言い換えはしない。

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
  "trace": "/Users/you/.typesafe-auto-browsing/logs/….jsonl"
}
```

- `value`: 目的が求める量や事実。
- `subject`: その値が属するものの名前（商品名、記事名、経路名）。事実を読む目的では出ない。
- `confidence`: TypeSafe の確率。
- 見つからないときは推測せず、`answers` を空にして `answers_note` に理由を入れる。

手順:

1. 目的が「見つけて報告する」ことを求めているか判定する（Noul）。
2. 最小・最大で比べる目的（最安・最多・最短など）か判定する。比べる場合は、目的文のどの語が比べる量（「所要時間」「やすい」など）を指すかも選ぶ。
3. 答えの直前にページを読み直す。確信度が低いとき（更新中のページなど）は 2 秒待ち、最大 3 回まで読み直す。
4. ページを断片に分け、断片ごとに「その量（または事実）を述べた文字列」を Choice で選ぶ。確率の高い候補を最大 3 つ残す。
5. 全断片の候補から、最小・最大のもの（または事実を述べたもの）を Choice で選ぶ。
6. 比べる場合は、その値が属するものの名前を値の周辺から選ぶ。リンクなら、直下の `/url:` 行を写して `url` にする。

制約:
- 最終の 1 ページの中だけから選ぶ。詳細ページへは移動しない。
- 文字列はページのテキストのまま。文の断片や、連結された文字列になることがある。
- 「上位 N 件」のような複数の答えは未対応。

## サンプルの目的（`prompts/`）

目的を 1 つずつファイルにしてある。先頭の `#` 行に、何を確かめる目的か、期待する答えを書いている。

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

新しい目的を足すには、URL から始まる 1 行と、期待を書いた先頭コメントを持つファイルを置く。`tests/test_prompts.py` が、全ファイルにその 2 つがあることを確認する。

## 仕組み

`npx @playwright/mcp@latest --browser chrome` を MCP（stdio）で起動し、`list_tools` でツール一覧を取得する。以降は、目的を達成するか `--max-steps` に達するまで、次の 1〜5 を繰り返す（`agent.py`）。

1. **ページを読む。** `browser_snapshot` の出力は加工せず、全文を `<log-dir>/<日時>/` にファイルとして保存する。TypeSafe に渡すのは必要な部分だけ（`page_view.py`）。
   - 5 万文字以下のページは全文を渡す。
   - それより長いページは、約 8,000 文字ずつの「部分」に分ける。TypeSafe が「成果が出ている部分」と「次に操作する部品がある部分」を Choice で選び、選ばれた部分だけをページの順序どおりに渡す。説明が窓に収まらないときは、説明を短くして再試行する。
   - 選ばれなかった部分は TypeSafe から見えない。全文は `<log-dir>/<日時>/` に残る。
2. **ツールを選ぶ。** 「目的は達成済みか」（Noul）と「次に呼ぶツール」（Choice）を同時に判断する。候補は、スキーマ上使えるすべてのツール。ダイアログやファイル選択が開いているときは、`Modal state` が示すツール（例: `browser_handle_dialog`）だけが候補になる。
3. **引数を決める。** MCP ツールの `input_schema` に従い、まず選択と要素、次に値を決める（`arguments.py`）。下の表を参照。
4. **実行する。** `--confirm` のときは、変更を伴うツール（MCP の `read_only_hint` が偽）の呼び出し前に人へ確認する。拒否は履歴に残り、TypeSafe は別の手を選ぶ。呼び出しの結果は履歴に追加する。失敗したときは、エラーの先頭 4 行（原因）を履歴に入れる。読み取り専用ツールの出力（例: `browser_snapshot` の `target` 指定）は、次のステップで `focus` として TypeSafe に見せる。
5. **使えないツールを外す。** 必須の引数に候補がない、指定するものがない、といったツールはその場で候補から外し、選び直す（最大 3 回）。3 ステップ続けて使えるツールがなければ、失敗として終了する。

### 引数の決め方

| スキーマ上の型 | 決め方 |
|---|---|
| 要素の参照（`target` / `ref` / `…Target`） | スナップショット中の `[ref=…]` から Choice。失敗した要素は、ページが変わるまで候補から外す。任意の引数なら「なし」も選べる |
| `enum` | Choice。任意なら「指定しない」も選べる |
| 真偽値 | Noul |
| `modifiers` のような enum の配列 | 項目ごとの Noul |
| 自由な文字列・数値 | 目的文の部分（同じ種類の文字の並び。空白を含んでもよい）を候補に Choice。決まらないときだけ、画面上の要素の名前を候補にする。選ばれた候補は、コードがそのままコピーする |
| `index` | 上に加えて、直前の出力に並ぶ番号も候補 |
| `browser_press_key` の `key` | 上に加えて、Playwright のキー名一覧（`keys.py`）も候補 |
| 文字列の配列（`browser_select_option` の `values` など） | 選んだ要素の下に並ぶ `option` のラベルから Choice。なければ文字列の候補から |
| オブジェクトの配列（`browser_fill_form` の `fields` など） | 下記 |

`element` / `filename` / `depth` は決めない。`filename` を決めないのは、レスポンスをファイルに逃がさず返させるため。

**オブジェクトの配列**は 1 件ずつ決める。1 件目は必須で、2 件目以降は「もう 1 件要る」を Noul で聞く（最大 10 件）。同じ要素は 2 回入力しない。項目の `name`（人が読む名前）は、選んだ要素の名前を画面から写す。

### 提示しないツール

- `browser_evaluate`: `function` が JavaScript のコードで、TypeSafe はコードを書けない。理由を表示して候補から外す。
- `browser_run_code_unsafe`: コードは任意引数だが、指定するものがないので実質使われない。
- `browser_find`: 検索テキストの候補（目的文、画面上の名前）が合わないと「指定するものがない」として外れる。

### タブ

クリックで新しいタブが開いても、現在のタブは元のまま。MCP の結果に `### Open tabs` の一覧が出る。`browser_tabs`（`select`）の `index` は、その一覧の番号から選ぶ。

## AI エージェントから使う

Claude Code や GitHub Copilot から呼び出させるための Agent Skill（[`skills/typesafe-auto-browsing/SKILL.md`](skills/typesafe-auto-browsing/SKILL.md)）がある。エージェントは Bash で `typesafe-auto-browsing "<目的>" --json` を実行し（`--headless` は、人が headless での実行を明示したときだけ付ける）、JSON の `answers` を読む。

```sh
uv tool install git+https://github.com/aRaikoFunakami/typesafe-auto-browsing   # CLI 本体（Skill には含まれない）
npx skills add aRaikoFunakami/typesafe-auto-browsing                            # Skill（Claude Code / Copilot など）
gh skill install aRaikoFunakami/typesafe-auto-browsing                          # 同上、GitHub CLI 版
```

- 並列に実行しない。Chrome のプロファイルを共有しているため、同時に 2 つ動かすと衝突する。
- `--confirm` は端末が要るので使えない。購入・削除などの目的は、人が明示したときだけ実行させる。
- ログインが要るサイトは、あらかじめ人が一度ログインしておく（永続プロファイル）。

## 実行記録

実行ごとに `~/.typesafe-auto-browsing/logs/<日時>.jsonl` へ、省略なしで記録する（環境変数 `TYPESAFE_AUTO_BROWSING_HOME` で `~/.typesafe-auto-browsing` を変えられる。Playwright MCP の出力はその下の `playwright/`）。1 行が 1 イベントで、`seq` / `time` / `elapsed_s` / `kind` を持つ。大きなスナップショットは `<日時>/` の別ファイルに全文を保存する。

- ホーム直下なので git 管理に混ざらない。ページ内容や入力した文字列を含むため、ファイルは 0600、ディレクトリは 0700 で作る。
- 保持期間の管理はない。削除は手動。

| kind | 内容 |
|---|---|
| `run_start` | 目的文、コマンドライン引数 |
| `mcp_tools` | Playwright MCP のツール一覧（説明、`input_schema`） |
| `tools_selected` | `--dry-run` のツール確率 |
| `page_view` | 長いページで TypeSafe に見せた部分と、各部分の確率 |
| `typesafe_request` / `typesafe_response` | TypeSafe に送った `state`（目的・履歴・ページ）と `questions`、返ってきた回答（確率つき）・トークン数 |
| `typesafe_error` | TypeSafe のエラー（`max_tokens_exceeded` の再試行も含む） |
| `mcp_call` / `mcp_result` | ツール呼び出しの引数と、レスポンス全文（大きいものは `text_file` のパス）・所要時間 |
| `log` | 画面に表示した行 |
| `answers` / `answer_quantity` | 答えと、比べる量に選ばれた語 |
| `outcome` / `error` | 結果と使用量 / 例外 |

```sh
jq -c 'select(.kind=="typesafe_response") | .response.answers' ~/.typesafe-auto-browsing/logs/20260919-184251.jsonl
jq -r 'select(.kind=="mcp_result" and .tool=="browser_snapshot") | .text_file' ~/.typesafe-auto-browsing/logs/20260919-184251.jsonl
```

## テスト

```sh
uv run pytest
```

TypeSafe と Playwright MCP を台本に差し替えたスタブで、次を確認する。ツールのスキーマは 25 個分（`tests/fixtures/tools.json`）。

- 引数の決め方、断片の選択、トレースのファイル権限
- ループの挙動: ダイアログでの候補の絞り込み、失敗した要素の除外、使えるツールがない場合、`--confirm` の拒否と読み取り専用ツールの確認なし、`browser_close`
