"""Key names for browser_press_key.

The tool's schema only says "such as ArrowLeft or a"; the names come from Playwright's keyboard
documentation (https://playwright.dev/docs/api/class-keyboard), the only static list in this
project. Single characters (`a`, `/`) are chosen from the goal like any other string.
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
