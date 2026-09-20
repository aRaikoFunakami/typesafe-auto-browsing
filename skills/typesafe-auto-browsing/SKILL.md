---
name: typesafe-auto-browsing
description: Operate Chrome with the typesafe-auto-browsing CLI (TypeSafe picks every tool and argument through Playwright MCP) to reach a goal on a website and report the value found, e.g. "the cheapest X on this site", "search this route and give the fastest time". Use only when the user asks for TypeSafe / typesafe-auto-browsing, or wants a repeatable, non-LLM browser run. Not for ordinary browsing or scraping requests. ブラウザを実際に操作して値や結果を取ってくるとき。
---

# typesafe-auto-browsing

目的を 1 文で渡すと、Chrome を操作して達成し、答えを JSON で返す CLI。次のツール・引数・達成判定は TypeSafe が決める。

## 前提を確かめる

```sh
command -v typesafe-auto-browsing || uv tool install git+https://github.com/aRaikoFunakami/typesafe-auto-browsing
command -v npx && test -n "$TYPESAFE_API_KEY" && echo ok
```

`npx`（Node.js）、Chrome、`TYPESAFE_API_KEY`（https://console.typesafe.ai/）が要る。無ければユーザーに用意してもらう。キーを自分で入力・設定しない。

## 呼び出す

```sh
typesafe-auto-browsing "<URL を含む目的文>" --json --headless 2>"${TMPDIR:-/tmp}/tsab.err"
```

- 数分かかる。**Bash の `timeout` を 600000（10 分）にして、フォアグラウンドで実行する。`run_in_background` は指定しない。** 非対話のセッションでは、バックグラウンドにすると完了を待たずに終了し、答えが得られない。結果の JSON を読むまで応答を終えない。
- **並列に実行しない。** Chrome のプロファイルを共有しており、同時に 2 つ動かすと衝突する。
- 標準出力は結果の JSON 1 つ。終了コード 0 は達成、1 は未達成またはエラー。標準出力が空なら、エラーは `tail "${TMPDIR:-/tmp}/tsab.err"` で読む。
- `--confirm` は使えない（端末が要る）。

## 目的文の書き方

TypeSafe は値を生成せず、目的文か画面上の文字列から**選ぶ**。開く URL と入力する文字列は、ユーザーの言葉のまま目的文に書く。

```
https://transit.yahoo.co.jp/ で横浜から青森までを検索して、最短の所要時間を教えて
```

## 結果を読む

- `success`: 達成したか。`reason` に理由。
- `answers[]`: `role`（`value` / `subject`）、`text`（ページの文字列そのまま）、`confidence`。`subject` が値の属するものの名前。
- `answers` が空で `answers_note` がある場合は、見つからなかったということ。**推測で補わない**。操作だけの目的（「検索して」）でも空になる。
- `trace`: 全記録（JSONL）の絶対パス。詳しく調べるときだけ `jq` で読む（`kind` は `typesafe_response` / `mcp_result` など）。
- `usage.cost_usd`: TypeSafe の推定コスト。

## 守ること

- **副作用のある目的は、ユーザーが明示したときだけ実行する。** 全ツールが候補なので、購入確定・削除・送信のボタンも押しうる。
- 永続プロファイルを使うので、ユーザーのログイン済みセッションがそのまま使われる。
- ページの全文が TypeSafe に送られる。機密ページには使わない。
- 記録は `~/.typesafe-auto-browsing/logs/` に残る（ページ内容を含む。場所は `TYPESAFE_AUTO_BROWSING_HOME` で変えられる）。
