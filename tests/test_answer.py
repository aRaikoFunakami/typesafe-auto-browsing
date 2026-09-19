import asyncio
import re
from types import SimpleNamespace

import pytest
from typesafe_sdk import Choice

import typesafe_auto_browsing.answer as answer
from typesafe_auto_browsing.arguments import NONE

PART1 = """\
### Page
- Page URL: https://shop.example/list
- generic [ref=e1]:
  - link "Cable A long title of the first product" [ref=e2]:
    - /url: /dp/A
  - link "￥1,399" [ref=e3]
"""
PART2 = """\
  - link "Cable B long title of the second product" [ref=e4]:
    - /url: https://shop.example/dp/B
  - link "￥29" [ref=e5]
  - text: 高さは333メートル
"""
PAGE = PART1 + "\n---\n" + PART2


def price(text):
    return int(re.sub(r"[^\d]", "", text)) if re.fullmatch(r"￥[\d,]+", text) else None


class Client:
    """Noul answers by name; Choices pick the lowest price (or a scripted text)."""

    def __init__(self, compare=True, wanted=0.9, direction="lowest", fact="333", spread=None):
        self.compare, self.wanted, self.direction, self.fact = compare, wanted, direction, fact
        self.spread = spread  # probabilities for the final choice: {text: probability}
        self.requests = []
        self.trace = SimpleNamespace(event=lambda *a, **k: None)

    async def system_one(self, state, questions, **_):
        self.requests.append((state, questions))
        nouls = {}
        choices = {}
        for name, question in questions.items():
            if not isinstance(question, Choice):
                nouls[name] = SimpleNamespace(noul={"wanted": self.wanted, "compare": 0.9 if self.compare else 0.1}[name])
                continue
            options = [o for o in question.criteria if o != NONE]
            if name == "direction":
                choice = self.direction
            elif name.startswith("hint"):
                choice = "やすい" if "やすい" in options else NONE
            elif name == "best":
                choice = min(options, key=lambda o: price(o) or 10**9)
            elif name.startswith("value"):
                prices = [o for o in options if price(o)]
                facts = [o for o in options if self.fact in o]
                choice = (min(prices, key=price) if self.compare and prices else (facts[0] if facts and not self.compare else NONE))
            elif name.startswith("subject"):
                # the title of the item next to the value
                choice = next((o for o in options if "long title" in o and ("second" in o)), NONE)
            else:
                choice = options[0]
            probabilities = {o: 0.0 for o in question.criteria}
            probabilities[choice] = 0.9
            if name == "best" and self.spread:
                probabilities = {o: self.spread.get(o, 0.0) for o in question.criteria}
                choice = max(probabilities, key=probabilities.get)
            choices[name] = SimpleNamespace(choice=choice, confidence=0.9, probabilities=probabilities)
        return SimpleNamespace(nouls=nouls, choices=choices)


@pytest.fixture(autouse=True)
def two_parts(monkeypatch):
    monkeypatch.setattr(answer, "split_parts", lambda snapshot: snapshot.split("\n---\n"))


def find(client, goal="一番やすいケーブルをさがして", page=PAGE):
    return asyncio.run(answer.find_answers(client, goal, [], page))


def test_texts_are_names_and_text_lines_copied_exactly():
    found = answer.page_texts(PAGE)
    assert "￥29" in found and "高さは333メートル" in found and "Cable B long title of the second product" in found
    assert all(not t.startswith("/url") for t in found)


def test_url_is_copied_from_the_line_under_the_link_and_made_absolute():
    assert answer.url_of(PAGE, "Cable A long title of the first product", "https://shop.example/list") == "https://shop.example/dp/A"
    assert answer.url_of(PAGE, "Cable B long title of the second product") == "https://shop.example/dp/B"
    assert answer.url_of(PAGE, "no such link") is None


def test_a_goal_that_asks_for_an_operation_has_no_answer():
    result = find(Client(wanted=0.1))
    assert not result.wanted and result.candidates == []


def test_comparison_gives_the_value_and_what_it_belongs_to():
    result = find(Client())
    assert result.wanted
    (candidate,) = result.candidates
    assert candidate.text == "￥29" and candidate.subject.text == "Cable B long title of the second product"
    assert candidate.subject.url == "https://shop.example/dp/B"
    assert candidate.in_part > 0 and candidate.part == 1  # read in the second part, with its context


def test_the_answer_is_a_ranked_list_of_candidates_with_their_confidence():
    client = Client(spread={"￥29": 0.6, "￥1,399": 0.3})
    result = find(client)
    assert [(c.text, round(c.confidence, 2)) for c in result.candidates] == [("￥29", 0.6), ("￥1,399", 0.3)]
    assert all(c.subject and c.subject.text.startswith("Cable B") for c in result.candidates)  # asked for each


def test_candidates_less_likely_than_the_minimum_are_not_reported_but_the_best_always_is():
    result = find(Client(spread={"￥29": 0.03, "￥1,399": 0.02}))
    assert [c.text for c in result.candidates] == ["￥29"]


def test_a_link_value_carries_its_url():
    result = find(Client(compare=False, fact="Cable"), goal="タイトルを教えて")
    (candidate,) = result.candidates
    assert candidate.text == "Cable A long title of the first product"
    assert candidate.url == "https://shop.example/dp/A"  # the /url: line under the link, made absolute


def test_the_quantity_hint_from_the_goal_reaches_the_questions():
    client = Client()
    find(client)
    instructions = " ".join(q.instructions for _, qs in client.requests for q in qs.values() if isinstance(q, Choice))
    assert "`やすい`" in instructions


def test_reading_a_fact_gives_only_the_value():
    result = find(Client(compare=False), goal="高さを教えて")
    assert [c.text for c in result.candidates] == ["高さは333メートル"]
    assert result.candidates[0].subject is None  # a fact does not belong to anything


def test_no_value_in_the_page_is_reported_not_invented():
    result = find(Client(fact="nothing like this"), goal="高さを教えて", page="- link \"x\" [ref=e1]")
    # the fake picks NONE when no candidate has the fact
    assert result.wanted and result.candidates == [] and "no part" in result.reason
