# 処理の流れ（データフロー図とシーケンス図）

`uv run typesafe-auto-browsing "https://www.amazon.co.jp/ で一番やすいusb-cケーブルをさがして"` のように渡した目的（プロンプト）が、どのように処理されるかをまとめます。図は [Mermaid](https://mermaid.js.org/) で書いてあり、GitHub などでそのまま描画されます。

- 図の数値（リクエスト数、文字数、確率）は、実際の実行記録（`logs/*.jsonl`）から取ったものです。ページの状態によって実行ごとに変わります。
- 関数名・定数名は現在のコードのものです（`src/typesafe_auto_browsing/`）。

## 1. 全体像

このプログラムの原則は 1 つです。**TypeSafe（Jev）は選ぶだけで、文字列を生成しない。コードが選ばれたものをそのまま写す。**

Chrome の操作は Playwright MCP に任せ、次の判断をすべて TypeSafe の Choice（選択肢から 1 つ）と Noul（はい／いいえの確率）で行います。

| 判断 | 質問の種類 | 例（Amazon の目的） |
|---|---|---|
| 目的は達成済みか | Noul `done` | 0.01 → … → 0.82 |
| 次に呼ぶツール | Choice `tool` | `browser_navigate` → `browser_type` → `browser_select_option` |
| ツールの引数 | Choice / Noul | `url = 'https://www.amazon.co.jp/'`、`text = 'usb-cケーブル'`、`values = ['価格: 安い順']` |
| 長いページのどこを読むか | 部分ごとの Noul `outcome` / `control` | 42 個の部分のうち 3 つ |
| 答えの候補（値・名前） | Choice `value` / `best` / `subject` | `￥29`、商品名。候補は確率つきで複数出す |

処理は 4 つの段階に分かれます。

1. **起動**: 目的を読み、Playwright MCP を起動してツール一覧を取得する。
2. **観察 → 判断 → 実行のループ**: 目的が達成されたと判断するまで繰り返す（最大 20 ステップ）。
3. **答え**: 目的が「教えて」のように何かを見つけることを求めていれば、最終ページから答えを選ぶ。
4. **出力**: 結果を人向けの表示、または `--json` の JSON で出す。

すべての TypeSafe への入出力と MCP の呼び出しは、`logs/<日時>.jsonl`（と、大きなスナップショットの別ファイル）に省略なしで記録されます。

## 2. データフロー図

### 2.1 コンテキスト図（システムと外部の関係）

```mermaid
flowchart LR
    user(["ユーザー"])
    sys["typesafe-auto-browsing<br/>（このプログラム）"]
    ts(["TypeSafe API<br/>Jev 1.13"])
    mcp(["Playwright MCP<br/>＋ Chrome"])
    web(["Web サイト<br/>amazon.co.jp など"])
    logs[("logs/<br/>実行記録")]

    user -->|"目的文 / -f ファイル / オプション"| sys
    sys -->|"結果（表示 or JSON）<br/>確認の質問（--confirm）"| user
    sys -->|"state（目的・履歴・ページ）<br/>＋ questions（Choice / Noul）"| ts
    ts -->|"answers（選ばれた選択肢・確率）"| sys
    sys -->|"ツール呼び出し<br/>（名前＋引数）"| mcp
    mcp -->|"結果テキスト / エラー<br/>スナップショット（YAML）"| sys
    mcp <-->|"ブラウザ操作"| web
    sys -->|"全リクエスト・全レスポンス<br/>スナップショット全文"| logs
```

### 2.2 レベル 1（プロセスとデータストア）

```mermaid
flowchart TB
    user(["ユーザー"])
    ts(["TypeSafe API"])
    mcp(["Playwright MCP<br/>+ Chrome"])

    subgraph proc["プログラム内"]
        direction TB
        p1["P1 目的の取得"]
        p2["P2 セッション起動"]
        p3["P3 観察<br/>snapshot と view_page"]
        p4["P4 判断<br/>done と tool"]
        p5["P5 引数の決定<br/>decide"]
        p6["P6 実行<br/>確認と MCP 呼び出し"]
        p7["P7 答え<br/>find_answers"]
        p8["P8 出力"]
        p1 -->|"goal"| p2
        p2 -->|"tools"| p3
        p3 -->|"view"| p4
        p4 -->|"選ばれたツール"| p5
        p5 -->|"Decision"| p6
        p6 -.->|"次のステップ"| p3
        p4 -->|"done 0.8 以上<br/>Outcome"| p7
        p7 -->|"Answers"| p8
    end

    d1[("D1 実行記録<br/>logs")]
    d2[("D2 実行状態<br/>history / failed refs<br/>focus / page_chars")]

    user -->|"目的文 / -f / オプション"| p1
    p8 -->|"結果 表示 or JSON"| user
    p6 <-->|"確認 --confirm"| user

    p2 <-->|"起動 / tools"| mcp
    p3 <-->|"snapshot"| mcp
    p6 <-->|"tool call / 結果"| mcp
    p7 <-->|"読み直し"| mcp

    p3 <-->|"部分の選択"| ts
    p4 <-->|"done と tool"| ts
    p5 <-->|"引数の質問と選択"| ts
    p7 <-->|"答えの質問と選択"| ts

    p6 -->|"追記"| d2
    d2 -->|"history / focus / modal"| p4
    proc -.->|"typesafe_request と response<br/>mcp_call と result / log<br/>snapshot 全文 / outcome / answers"| d1
```

- 実線がデータの流れです。点線は、ループの次のステップへの戻りと、記録（D1）への書き込みです（すべてのプロセスが記録します）。
- P3 の `snapshot` は MCP から受け取り、全文を D1 にも保存します。
- TypeSafe へのリクエストはすべて `MeteredClient.system_one`、MCP の呼び出しはすべて `agent._call_and_trace` を通るので、記録は 2 か所で行われます。

### 2.3 データ辞書

| データ | 形式 | 作る所 | 使う所 |
|---|---|---|---|
| `goal` | 文字列（例: `https://www.amazon.co.jp/ で一番やすい…`） | P1（引数、または `-f` のファイル。`#` 行はコメント） | 全 TypeSafe リクエストの `state.goal`、引数の候補の元 |
| `tools` | MCP のツール 25 個（name, description, input_schema, annotations） | P2（`list_tools`） | P4 の選択肢、P5 の引数の決め方の根拠 |
| `snapshot` | Playwright MCP の YAML（要素・`[ref=eN]`・リンクの `/url:`） | P3・P7（`browser_snapshot`） | P3（ページビュー）、P7（答え）、D1 |
| `view` | snapshot 全体、または選ばれた約 8,000 文字の部分の連結 | P3（`view_page`） | P4・P5 の `state.page` |
| `state` | `{goal, history, page, [focus], [modal], [next_action]}` | P3〜P7（`build_state`） | TypeSafe（32k トークンまで。超えたらページを半分に縮めて再試行） |
| `questions` | Choice（選択肢は最大 255 個＋「該当なし」）と Noul | P3〜P7 | TypeSafe |
| `answers` | Choice: 選ばれた選択肢・確信度・全選択肢の確率 / Noul: 確率 | TypeSafe | P3〜P7 |
| `Decision` | `arguments`（ツールの引数）、`sources`（goal / page / list / schema）、`unusable`（使えない理由） | P5（`decide`） | P4 の再選択、P6 |
| `history` | 実行したツール呼び出しの文字列。失敗は `-> FAILED: 原因の先頭 4 行`、拒否は `-> DECLINED by the user` | P6 | 次のステップの `state.history` |
| `focus` | 読み取り専用ツール（`browser_snapshot` の `target` 指定など）の出力（最大 8,000 文字） | P6 | 次のステップの `state.focus` |
| `Outcome` | `success`、`reason`、最終ページ、`history` | P4（done 0.8 以上で作る） | P7 |
| `Answers` | 候補の一覧（`text`、`confidence`、`in_part`、`part`、`url`、`subject`）と理由 | P7（`find_answers`） | P8 |
| 実行記録 | 1 行 1 イベントの JSON（`typesafe_request`、`typesafe_response`、`mcp_call`、`mcp_result`、`log`、`page_view`、`answers`、`outcome` など） | `Trace` | 調査（`jq` など） |

## 3. シーケンス図

### 3.1 全体（Amazon の目的）

実行記録から起こした、典型的な流れです。Amazon のページは大きく、結果が読み込まれる前にスナップショットを取ると答えが見つからないことがあるため、答えの段階で読み直しが入る例にしています。エージェントの中の細かい動き（ページビュー、引数の決定）は 3.2〜3.4 で説明します。

```mermaid
---
config:
  sequence:
    wrap: true
    width: 190
    messageMargin: 28
    noteMargin: 8
---
sequenceDiagram
    autonumber
    actor U as ユーザー
    participant CLI as CLI<br/>__init__.py
    participant AG as エージェント<br/>agent.py ほか
    participant TS as TypeSafe API
    participant MCP as Playwright MCP<br/>+ Chrome

    U->>CLI: uv run typesafe-auto-browsing "目的文"
    Note over CLI: logs/日時.jsonl を作成 (0600)。以降、全リクエストと全結果を記録
    CLI->>MCP: npx @playwright/mcp を起動 (stdio)
    MCP-->>CLI: list_tools → 25 ツール
    CLI->>AG: run_agent(goal, tools)
    Note over AG: browser_evaluate は提示しない (引数が JS コード)

    rect rgb(235, 245, 255)
    Note over AG,MCP: ステップ 1 ページを開く
    AG->>MCP: browser_snapshot
    MCP-->>AG: about:blank (58 文字)
    AG->>TS: done と tool の質問
    TS-->>AG: done = 0.01、tool = browser_navigate (1.00)
    AG->>TS: url の質問 (候補 = 目的文の部分)
    TS-->>AG: url = "https://www.amazon.co.jp/" (0.98)
    AG->>MCP: browser_navigate url
    MCP-->>AG: ok
    end

    rect rgb(235, 245, 255)
    Note over AG,MCP: ステップ 2 検索語を入力して送信
    AG->>MCP: browser_snapshot
    MCP-->>AG: 約 4.4 万文字 (ホームページ)
    Note over AG: 5 万文字以下なので全文をそのまま TypeSafe に見せる
    AG->>TS: done と tool の質問
    TS-->>AG: done = 0.01、tool = browser_type
    AG->>TS: target と submit の質問
    TS-->>AG: target = 検索ボックス、submit = true
    AG->>TS: text の質問 (next_action に target を含める)
    TS-->>AG: text = "usb-cケーブル" (目的文の部分)
    AG->>MCP: browser_type target, text, submit
    MCP-->>AG: ok (検索結果へ移動)
    end

    rect rgb(235, 245, 255)
    Note over AG,MCP: ステップ 3 並べ替えを「価格: 安い順」にする
    AG->>MCP: browser_snapshot
    MCP-->>AG: 約 34 万文字 (検索結果)
    AG->>TS: 42 個の部分それぞれに outcome と control の質問 (本文つき、並列)
    TS-->>AG: 部分 1 と 10 と 34 を選択
    Note over AG: 選ばれた 3 部分 (約 2.4 万文字) だけを見せる
    AG->>TS: done と tool の質問
    TS-->>AG: done = 0.2、tool = browser_select_option
    AG->>TS: target の質問
    TS-->>AG: target = 並べ替えのコンボボックス
    AG->>TS: values の質問 (候補 = そのコンボボックスの option)
    TS-->>AG: values = "価格: 安い順" (0.99)
    AG->>MCP: browser_select_option
    MCP-->>AG: ok (ページが更新される)
    end

    rect rgb(255, 245, 235)
    Note over AG,MCP: ステップ 4 完了の判断
    AG->>MCP: browser_snapshot
    MCP-->>AG: 更新後のページ
    AG->>TS: done と tool の質問
    TS-->>AG: done = 0.82 (しきい値 0.8 以上)
    Note over AG: Outcome (success, 最終ページ, history)
    end

    rect rgb(240, 255, 240)
    Note over AG,MCP: 答えの段階 (詳しくは 3.4)
    AG->>MCP: browser_snapshot (読み直し 1 回目)
    MCP-->>AG: 結果がまだ読み込み前のページ
    AG->>TS: wanted, compare, direction, hint, value を部分ごとに
    TS-->>AG: 該当する値なし (確信度が低い)
    AG->>MCP: browser_wait_for 2 秒
    AG->>MCP: browser_snapshot (読み直し 2 回目)
    MCP-->>AG: 検索結果 (約 37 万文字)
    AG->>TS: wanted, compare, direction, hint, value を部分ごとに、best, subject
    TS-->>AG: 候補 ￥29 (確信度 1.00) と、その商品名
    end

    AG-->>CLI: Outcome と Answers (候補の一覧)
    CLI-->>U: 結果 (JSON なら標準出力、進行ログは標準エラー)
```

- この例の実測は、リクエスト 71 回（操作のループ 13 回、答えの段階 58 回）、コスト約 $0.02 です。
- 答えの段階のリクエストの大半は、断片ごとの `value` の質問です（50 回。読み直し 2 回分の断片数の合計）。
- 実際の実行では、この図にない操作が入ることがあります。この記録では、ステップ 1 と 2 の間に、ページの読み込み前のスナップショットを見て、ホームページ上の要素を 1 回クリックしています。図はそれを省いた、目的にまっすぐ向かう流れです。
- ステップ 3 のあと、ページ更新の途中のスナップショットが取れて、ステップ 4 で別の操作が選ばれることもあります。その場合もループは同じ手順で続きます。

### 3.2 1 ステップの詳細（ループの中身）

`agent.run_agent` の 1 回分です。

```mermaid
---
config:
  sequence:
    wrap: true
    width: 170
    messageMargin: 28
    noteMargin: 8
---
sequenceDiagram
    autonumber
    participant AG as run_agent (agent.py)
    participant PV as view_page (page_view.py)
    participant AR as decide (arguments.py)
    participant TS as TypeSafe API
    participant MCP as Playwright MCP
    actor U as ユーザー

    AG->>MCP: browser_snapshot
    alt ダイアログやファイル選択が開いている (Modal state)
        MCP-->>AG: エラー + 「can be handled by browser_handle_dialog」
        Note over AG: 直前のページを使い、そのツールだけを候補にする
    else 通常
        MCP-->>AG: snapshot
    end

    AG->>PV: view_page(snapshot, goal, history, page_chars)
    opt snapshot が page_chars (5 万文字) を超える
        PV->>TS: 部分ごとに outcome と control の Noul (部分の本文つき、並列)
        TS-->>PV: 各部分の確率 (0.2 以上の部分を選ぶ)
        Note over PV: 選ばれた部分をページの順序のまま連結 (加工しない)
    end
    PV-->>AG: view

    AG->>TS: state = goal, history, page, focus, modal / questions = done (Noul), tool (Choice)
    Note over AG,TS: max_tokens_exceeded ならページを半分に縮めて再試行
    TS-->>AG: done の確率、選ばれたツール

    alt history があり done が 0.8 以上
        AG-->>AG: Outcome(success) を返す
    end

    loop 使えるツールが見つかるまで (最大 4 回)
        AG->>AR: decide(context, tool)
        AR-->>AG: Decision
        alt Decision.unusable (値の候補がない、指定するものがない)
            Note over AG: そのツールをこのステップだけ候補から外す
            AG->>TS: tool の Choice を、残りのツールだけで再質問
            TS-->>AG: 別のツール
        end
    end

    alt 使えるツールがなかった
        Note over AG: history に記録。3 ステップ連続なら失敗で終了
    else 使えるツールがあった
        Note over AG: 同じ (操作, ページ) を 3 回目に繰り返すなら stuck で失敗終了
        opt --confirm かつ読み取り専用でない
            AG->>U: ツール名・引数・対象要素を表示して y/n を聞く
            U-->>AG: y または n (n なら history に DECLINED を記録して次のステップへ)
        end
        AG->>MCP: tool(arguments)
        alt エラー
            MCP-->>AG: エラー
            Note over AG: history に FAILED と原因の先頭 4 行を追記。使った ref をそのツールの失敗リストへ
        else 成功
            MCP-->>AG: 結果
            Note over AG: history に追記。読み取り専用なら出力を focus に。変更ありなら失敗リストを空にする
        end
    end
```

### 3.3 引数の決定（`decide`）

ツールの `input_schema` だけを根拠に、TypeSafe が引数を選びます。段階に分けて聞くのは、「入力する文字列は、入力先の要素が決まってから決める」ためです。

```mermaid
---
config:
  sequence:
    wrap: true
    width: 170
    messageMargin: 28
    noteMargin: 8
---
sequenceDiagram
    autonumber
    participant AG as run_agent
    participant AR as decide / _decide_object
    participant TS as TypeSafe API

    AG->>AR: decide(context, tool)
    Note over AR: input_schema の各プロパティを分類<br/>element, filename, depth は決めない
    Note over AR: 要素の参照 target / ref / ...Target → 要素の Choice<br/>enum → Choice (任意なら「指定しない」を追加)<br/>真偽値 → Noul、enum の配列 (modifiers) → 項目ごとの Noul<br/>文字列 → 目的文の部分の Choice と、画面上の名前の Choice<br/>数値 → 目的文の数字と、直前の出力に並ぶ番号の Choice<br/>文字列の配列 (values, paths) → 段階 3<br/>オブジェクトの配列 (fields) → 段階 4

    rect rgb(235, 245, 255)
    Note over AR,TS: 段階 1 選択と要素 (1 リクエスト)
    AR->>TS: enum, 真偽値, target の Choice (next_action = ツール名)
    TS-->>AR: 選択、確率、選ばれた要素
    Note over AR: 選択肢が 255 個を超える要素は、分割して質問し、各勝者で決勝の質問を追加
    Note over AR: entry の name は、選んだ要素の名前を画面から写す
    end

    rect rgb(240, 255, 240)
    Note over AR,TS: 段階 2 値 (1 リクエスト。next_action に決まった要素を含める)
    AR->>TS: text, url, index などの Choice (目的文の候補 と 画面上の名前の候補)
    TS-->>AR: 選ばれた候補と確信度
    Note over AR: 目的文の候補を優先。確信度 0.5 未満は使わない。決まらない時だけ画面上の名前を使う
    end

    rect rgb(255, 250, 235)
    Note over AR,TS: 段階 3 文字列の配列
    AR->>TS: values の Choice (選んだ要素の下の option ラベル、なければ文字列の候補)
    TS-->>AR: 選ばれた 1 つ
    end

    rect rgb(255, 240, 245)
    Note over AR,TS: 段階 4 オブジェクトの配列 (browser_fill_form の fields)
    loop 1 件ずつ (最大 10 件)
        AR->>TS: 2 件目以降は「もう 1 件要るか」の Noul
        AR->>AR: 1 件分を、同じ手順 (段階 1〜2) で再帰的に決める。使った要素は次の件から外す
    end
    end

    Note over AR: 排他の引数 (説明の「either A or B, not both」) は、スキーマの順で先の 1 つだけ残す
    Note over AR: 必須の引数に値がなければ unusable を返す
    AR-->>AG: Decision (arguments, sources, unusable)
```

### 3.4 答えの段階（`answer_goal` と `find_answers`）

答えは 1 つに絞らず、TypeSafe が返す確率をそのまま候補の確信度として出します。

```mermaid
---
config:
  sequence:
    wrap: true
    width: 170
    messageMargin: 28
    noteMargin: 8
---
sequenceDiagram
    autonumber
    participant CLI as CLI
    participant AG as answer_goal (agent.py)
    participant FA as find_answers (answer.py)
    participant TS as TypeSafe API
    participant MCP as Playwright MCP

    CLI->>AG: answer_goal(goal, outcome)
    loop 最大 3 回
        AG->>MCP: browser_snapshot (最終ページを読み直す)
        MCP-->>AG: snapshot
        alt 前回の読み取りと同じページ
            Note over AG: 読み込みは終わっている。読み直さず、前回の結果を出す
        else 変わっている
        AG->>FA: find_answers(goal, history, snapshot)

        FA->>TS: wanted (Noul): 目的は何かを見つけて報告することを求めているか
        TS-->>FA: 確率
        alt 0.5 未満 (「検索して」のような操作だけの目的)
            FA-->>AG: Answers(wanted = false, 候補は空)
        else 0.5 以上
            FA->>TS: compare (Noul): 最安・最多・最短のように量で比べるか<br/>direction (Choice): lowest か highest か
            TS-->>FA: compare、direction
            opt 比べる場合
                FA->>TS: hint (Choice): 目的文のどの語が比べる量を指すか
                TS-->>FA: hint = 「やすい」 (0.5 以上なら質問文に入れる)
            end

            Note over FA: snapshot を約 8,000 文字の部分に分ける
            par 部分ごとに並列 (最大 8 件)
                FA->>TS: value (Choice): その部分の文字列から、量 (または事実) を述べたもの
                TS-->>FA: 各候補の確率 (その部分の中での確率 in_part)
            end
            Note over FA: 各部分で確率 0.2 以上の候補を最大 3 つ残す

            alt 候補が 1 種類
                Note over FA: 確信度 = その部分の中での確率
            else 複数
                FA->>TS: best (Choice): 最小 / 最大のもの、または事実を述べたもの (候補は 254 個ずつ。多ければ勝者どうしでもう一巡)
                TS-->>FA: 候補ごとの確率 (確信度)
            end
            Note over FA: 確率 0.05 以上の候補を、高い順に最大 5 つ (最上位は必ず)

            opt 比べる場合 (値が属するものの名前)
                par 候補ごとに並列
                    FA->>TS: subject (Choice): その値の周辺 (前 6,000 文字 と 後 4,000 文字) の文字列から、その名前
                    TS-->>FA: subject (「該当なし」なら空)
                end
                Note over FA: リンクなら、直下の /url: 行を写して URL にする
            end
            FA-->>AG: Answers (候補: text, confidence, in_part, url, subject)
        end
        end

        alt 操作だけの目的、または最上位の確信度が 0.5 以上
            Note over AG: ループを抜ける
        else 候補がない、または確信度が低い かつ ページが変わっている
            AG->>MCP: browser_wait_for 2 秒
        end
    end
    AG-->>CLI: Answers
```

## 4. 出力

- **既定**: `Done: goal achieved (p=0.82)` に続けて、答え（`value` / `subject` と確信度）と使用量（TypeSafe のリクエスト数・トークン数・推定コスト）を表示します。
- **`--json`**: 進行のログは標準エラーに出し、標準出力には次の JSON だけを出します。

```json
{
  "goal": "https://www.amazon.co.jp/ で一番やすいusb-cケーブルをさがして",
  "success": true,
  "reason": "goal achieved (p=0.82)",
  "page": {"url": "https://www.amazon.co.jp/s?k=…&s=price-asc-rank", "title": "Amazon.co.jp : usb-cケーブル"},
  "answers": [
    {
      "rank": 1, "text": "￥29", "confidence": 1.0, "in_part": 0.95, "part": 2, "url": null,
      "subject": {"text": "USB Type C ケーブル短い【25CM/2本セット】 …", "confidence": 0.99, "source": "page", "url": null}
    }
  ],
  "answers_note": "answered",
  "usage": {"requests": 71, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0206},
  "trace": "logs/20260919-201541.jsonl"
}
```

- `answers` は候補の一覧です（確率の高い順）。`confidence` は最後の選択での確率、`in_part` はその候補がある断片の中での確率です。2 つが食い違うことがあります。
- 見つからないときは `answers` が空になり、`answers_note` に理由が入ります。

## 5. コードとの対応

| モジュール | 役割 | 主な関数・クラス |
|---|---|---|
| `__init__.py` | CLI。引数と `-f` の読み取り、起動、出力 | `main`, `_run`, `_read_goal`, `goal_from_file`, `_confirm` |
| `playwright_mcp.py` | Playwright MCP の起動と呼び出し | `playwright_session`, `call_tool` |
| `agent.py` | 観察 → 判断 → 実行のループ、答えの読み直し | `run_agent`, `_call_and_trace`, `answer_goal` |
| `page_view.py` | 長いページの部分選択 | `view_page`, `split_parts`, `describe_part` |
| `arguments.py` | ツールの引数の決定（スキーマ駆動） | `decide`, `_decide_object`, `ask_picks`, `ask_fitting_page`, `goal_candidates`, `unusable_reason`, `modal_handlers` |
| `keys.py` | `browser_press_key` のキー名（唯一の静的リスト） | `KEYS` |
| `answer.py` | 答えの選択 | `find_answers`, `_final_round`, `page_texts`, `url_of` |
| `usage.py` | TypeSafe の入出力の記録とコスト計算 | `MeteredClient`, `Usage` |
| `trace.py` | 実行記録（`logs/`、所有者だけが読める権限） | `Trace` |

### 主な上限・しきい値

| 名前 | 値 | 意味 |
|---|---|---|
| `Settings.max_steps` | 20 | ループの最大ステップ数 |
| `Settings.done_threshold` | 0.8 | 「達成済み」とみなす `done` の確率 |
| `Settings.page_chars` | 50,000 | TypeSafe に見せるページの上限（超えたら部分選択） |
| `PART_CHARS` | 8,000 | 1 部分の大きさ |
| `MAX_OPTIONS` | 255 | 1 つの Choice の選択肢の数 |
| `MIN_CONFIDENCE` | 0.5 | 自由な文字列・数値の候補を使う最低の確信度 |
| `MAX_RETRIES` | 3 | 使えないツールを外して選び直す回数（1 ステップ内） |
| `MAX_DEAD_STEPS` | 3 | 使えるツールがないステップが連続したら失敗にする数 |
| `MAX_ENTRIES` | 10 | 配列の引数（`fields`）の件数 |
| `FOCUS_CHARS` | 8,000 | 読み取り専用ツールの出力を TypeSafe に見せる上限 |
| `ANSWER_ATTEMPTS` / `SETTLE_SECONDS` | 3 / 2 | 答えの読み直しの回数と待ち時間 |
| `MIN_ANSWER_CONFIDENCE` | 0.5 | これ未満なら読み直す |
| `FINALIST_PROBABILITY` / `FINALISTS_PER_PART` | 0.2 / 3 | 部分ごとに決勝に残す候補の条件 |
| `MAX_CANDIDATES` / `MIN_CANDIDATE_CONFIDENCE` | 5 / 0.05 | 出す候補の数と最低の確信度（最上位は必ず出す） |

## 6. 図の確認・更新

- 図は Mermaid です。GitHub、VS Code の Markdown プレビュー（Mermaid 対応の拡張）、[Mermaid Live Editor](https://mermaid.live/) で描画できます。
- 実行記録から数値を取り直すには、`jq` が便利です。

```sh
# 種類ごとのイベント数
jq -r '.kind' logs/<日時>.jsonl | sort | uniq -c
# TypeSafe に送った質問の種類の並び
jq -c 'select(.kind=="typesafe_request") | [.seq, (.questions | keys)]' logs/<日時>.jsonl
# 操作の並び
jq -c 'select(.kind=="mcp_call" and .tool!="browser_snapshot") | [.seq, .tool, .arguments]' logs/<日時>.jsonl
```
