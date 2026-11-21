from src.vectorstore.qdrant import term_forms


def test_apostrophe_forms_cover_each_other():
    expected = ["ІЯК’17", "ІЯКʼ17", "ІЯК17"]
    for term in ["ІЯК’17", "ІЯК'17", "ІЯК`17", "ІЯКʼ17", "ІЯК17"]:
        assert term_forms(term) == expected


def test_apostrophe_inside_word():
    assert term_forms("обʼєднання") == ["об’єднання", "обʼєднання", "обєднання"]


def test_other_terms_unchanged():
    for term in ["Мінчак", "SoDeep", "BEST::HACKath0n", "EBEC2024", "17", "R&D"]:
        assert term_forms(term) == [term]
