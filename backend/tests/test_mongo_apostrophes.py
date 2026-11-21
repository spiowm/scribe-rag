import re

from src.connectors.mongo import _prefix_pattern
from src.text import fold_apostrophes


def test_prefix_pattern_matches_any_apostrophe():
    for query in ["Дар'я", "Дарʼя", "Дар’я"]:
        pattern = re.compile(_prefix_pattern(query)["$regex"], re.IGNORECASE)
        for name in ["Дар'я", "Дарʼя", "Дар’я", "Дар`я"]:
            assert pattern.match(name)
        assert not pattern.match("Дарина")


def test_prefix_pattern_ignores_missing_apostrophe():
    without = re.compile(_prefix_pattern("Даря")["$regex"], re.IGNORECASE)
    for name in ["Дар'я", "Дарʼя", "Дар’я", "Даря"]:
        assert without.match(name)
    assert not without.match("Дарія")
    assert not without.match("Дарина")
    # і навпаки: в запиті апостроф є, в довіднику немає
    with_apostrophe = re.compile(_prefix_pattern("Дар'я")["$regex"], re.IGNORECASE)
    assert with_apostrophe.match("Даря")


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
    assert _partial_match_note("Марян Прикладяк", person) is None
    assert _partial_match_note("Марʼян Зразокʼюк", person) is not None
