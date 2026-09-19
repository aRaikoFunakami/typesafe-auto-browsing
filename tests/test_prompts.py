from pathlib import Path

import pytest

from typesafe_auto_browsing import _read_goal, goal_from_file

PROMPTS = sorted((Path(__file__).parent.parent / "prompts").glob("*.txt"))


def test_comments_and_blank_lines_are_not_part_of_the_goal(tmp_path):
    file = tmp_path / "goal.txt"
    file.write_text("# note\n\nhttps://example.com/ を開いて\n  見出しを教えて  \n# end\n", encoding="utf-8")
    assert goal_from_file(file) == "https://example.com/ を開いて 見出しを教えて"


def test_a_file_is_read_instead_of_words(tmp_path):
    file = tmp_path / "goal.txt"
    file.write_text("goal from file", encoding="utf-8")
    assert _read_goal([], file) == "goal from file"


def test_words_and_a_file_together_are_refused(tmp_path):
    file = tmp_path / "goal.txt"
    file.write_text("goal", encoding="utf-8")
    with pytest.raises(SystemExit, match="either as words or with --file"):
        _read_goal(["a", "goal"], file)


def test_a_file_with_only_comments_or_a_missing_file_is_refused(tmp_path):
    file = tmp_path / "goal.txt"
    file.write_text("# only a note\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="has no goal"):
        _read_goal([], file)
    with pytest.raises(SystemExit, match="cannot read"):
        _read_goal([], tmp_path / "missing.txt")


def test_there_are_sample_prompts():
    assert len(PROMPTS) >= 10


@pytest.mark.parametrize("file", PROMPTS, ids=lambda p: p.name)
def test_every_sample_prompt_has_a_goal_with_a_url_and_a_note(file):
    goal = goal_from_file(file)
    assert goal.startswith("https://") and "\n" not in goal  # the goal must contain the URL to open
    assert file.read_text(encoding="utf-8").startswith("#")  # what it checks and what to expect
