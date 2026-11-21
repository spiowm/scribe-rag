import re

from src.connectors.mongo import VP_PATTERNS, roman_latin, telegram_nick, vp_short


def test_nick_forms():
    for form in (
        "some_nick",
        "@some_nick",
        " @Some_Nick ",
        "t.me/some_nick",
        "https://t.me/some_nick",
        "https://t.me//some_nick",
        "https://telegram.me/some_nick/",
    ):
        assert (telegram_nick(form) or "").lower() == "some_nick", form


def test_names_are_not_nicks():
    # кирилиця, кілька слів, телефон замість ніка, закороткий нік
    for text in (
        "Прикладенко",
        "Олена Прикладенко",
        "Fixture Member",
        "+380000000001",
        "@abc",
    ):
        assert telegram_nick(text) is None, text


def test_vp_full_forms_shorten():
    cases = {
        "Vice president for IT of XXIV board aka FIXTURE ALPHA": "VP4IT of XXIV board aka FIXTURE ALPHA",
        "Vice president for Informational Technology of XXIІ board": "VP4IT of XXIІ board",
        "Vice president for Human Resources of XXIІІ board": "VP4HR of XXIІІ board",
        "Vice president for Design (February - October) of XXIІІ board": "VP4DS (February - October) of XXIІІ board",
        "VP4Design of XX Board aka FIXTURE BETA": "VP4DS of XX Board aka FIXTURE BETA",
        "VP for PR": "VP4PR",
        "vice-president for corporate relations": "VP4CR",
        "vp4ir": "VP4IR",
    }
    for src, want in cases.items():
        assert vp_short(src) == want, src


def test_vp_short_leaves_other_titles():
    for title in (
        "VP4FR of XXI Board aka FIXTURE GAMMA",
        "VPS of XXXIX International Board",
        "Vice president",
        "IT Responsible LEoE 2026",
        "President of XXIV board aka FIXTURE ALPHA",
        "PR Responsible BTW 2026",
    ):
        assert vp_short(title) == title, title


def test_vp_pattern_matches_both_forms():
    # той самий шаблон іде в Mongo як $regex, щоб звузити вибірку
    pattern = re.compile(VP_PATTERNS["IT"], re.IGNORECASE)
    for text in (
        "VP4IT",
        "Vice president for IT of XXIV board",
        "Vice president for Informational Technology",
    ):
        assert pattern.search(text), text
    assert not pattern.search("IT Responsible")


def test_roman_numerals_with_cyrillic_lookalikes():
    assert roman_latin("xxiі") == "xxii"
    assert roman_latin("xxiіі") == "xxiii"
    # українське «і» й кириличні слова не чіпаються
    assert roman_latin("і") == "і"
    assert roman_latin("хмс") == "хмс"
    assert roman_latin("board") == "board"
