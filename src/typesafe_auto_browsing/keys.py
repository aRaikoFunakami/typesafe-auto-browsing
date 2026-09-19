"""browser_press_key のキー名。

ツールのスキーマは「ArrowLeft や a など」としか言わない。名前は Playwright のキーボードの
ドキュメント（https://playwright.dev/docs/api/class-keyboard）から取った。このプロジェクトで
唯一の固定の一覧。1 文字のキー（`a`、`/`）は、他の文字列と同じく目的文から選ぶ。
"""

KEYS = (
    "Enter",
    "Tab",
    "Escape",
    "Backspace",
    "Delete",
    "Space",
    "ArrowUp",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
    "Home",
    "End",
    "PageUp",
    "PageDown",
    "Insert",
    *(f"F{n}" for n in range(1, 13)),
)
