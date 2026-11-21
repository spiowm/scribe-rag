import re

from src.connectors.mongo import _prefix_pattern
from src.text import fold_apostrophes


def test_prefix_pattern_matches_any_apostrophe():
    for query in ["Дар'я", "Дарʼя", "Дар’я"]:
        pattern = re.compile(_prefix_pattern(query)["$regex"], re.IGNORECASE)
        for name in ["Дар'я", "Дарʼя", "Дар’я", "Дар`я"]:
            assert pattern.match(name)
        assert not pattern.match("Дарина")


def test_fold_makes_modifier_apostrophe_a_separator():
    assert re.findall(r"\w+", fold_apostrophes("MO MW Autumnʼ26")) == [
        "MO",
        "MW",
        "Autumn",
        "26",
    ]


def test_partial_match_note_ignores_apostrophe_kind():
    from src.generation.tools import _partial_match_note

    person = [{"first_name": "Мар'ян", "last_name": "Приклад'як"}]
    for query in ["Марʼян Приклад'як", "Мар’ян Прикладʼяк", "Мар'ян Приклад'як"]:
        assert _partial_match_note(query, person) is None
    assert _partial_match_note("Марʼян Зразокʼюк", person) is not None
