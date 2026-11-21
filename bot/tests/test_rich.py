"""Підготовка markdown до rich-повідомлення: приклади з корпусу відповідей."""

from src.rich import plain, prepare

SOURCES = (
    "<details><summary>📚 Джерела</summary>\n\n"
    "- [Як писати мінетси](https://example.test/meeting-guide)\n"
    "</details>"
)
TOOLS = (
    "<details><summary><sub>_Думання_</sub></summary>\n"
    "<sub>_↳ 🔎 Пошук в базі: мінетси<br>↳ 📄 Читання документа_</sub>\n"
    "</details>\n\n"
)
FOOTER = "\n\n<sub>_agy | gemini-3.8-flash-high | 18s | Ctx: 10 messages_</sub>"


def test_ordinary_answer_is_untouched():
    reply = TOOLS + "**Мінетси** — протокол зборів.\n\n" + SOURCES + FOOTER
    assert prepare(reply) == reply


def test_quoted_table_rows_become_text():
    # 466: `<tr>`/`<td>` з Notion, процитовані без `<table>`.
    reply = "> *«Розподіл регіонів…  \n> <tr>  \n> <td>Region F</td>  \n> </tr>»*"
    assert prepare(reply) == (
        "> *«Розподіл регіонів…  \n> &lt;tr>  \n> &lt;td>Region F&lt;/td>  \n"
        "> &lt;/tr>»*"
    )


def test_details_glued_to_list_item_gets_blank_line():
    # 474 і 552: `<details>` наступним рядком після пункту списку.
    reply = (
        "1. **За скільки днів до зборів?**\n"
        "<details><summary>Відповідь</summary>\nНе менше ніж за 3 дні.\n</details>"
    )
    assert prepare(reply) == (
        "1. **За скільки днів до зборів?**\n\n"
        "<details><summary>Відповідь</summary>\nНе менше ніж за 3 дні.\n</details>"
    )


def test_details_mid_line_moves_to_own_block():
    reply = (
        "2. **Розкладний блок:** <details><summary>Тисни</summary>\n\nтекст\n</details>"
    )
    assert prepare(reply) == (
        "2. **Розкладний блок:**\n\n<details><summary>Тисни</summary>\n\nтекст\n</details>"
    )


def test_closing_details_mid_line_moves_to_own_line():
    reply = "<details><summary>a</summary>\n\nтекст</details>"
    assert prepare(reply) == "<details><summary>a</summary>\n\nтекст\n</details>"


def test_indented_details_stays_in_list_item():
    reply = "- пункт\n  <details><summary>a</summary>\n\n  b\n  </details>"
    assert (
        prepare(reply)
        == "- пункт\n\n  <details><summary>a</summary>\n\n  b\n  </details>"
    )


def test_tags_in_code_are_not_touched():
    # 474: модель пояснює синтаксис і пише `<details>` у зворотних лапках.
    reply = "Через `<details>` або\n\n```html\n<details><tr>\n```\n"
    assert prepare(reply) == reply


def test_unclosed_and_crossed_tags_become_text():
    assert prepare("<b>жирне <i>курсив</b> далі</i>") == (
        "<b>жирне &lt;i>курсив</b> далі&lt;/i>"
    )
    assert prepare("текст <details><summary>a</summary>\n\nb") == (
        "текст &lt;details>&lt;summary>a&lt;/summary>\n\nb"
    )


def test_block_inside_inline_tag_becomes_text():
    assert prepare("<sub><details><summary>a</summary>b</details></sub>") == (
        "<sub>&lt;details>&lt;summary>a&lt;/summary>b&lt;/details></sub>"
    )


def test_query_with_markup_in_tools_block():
    # Запит пошуку пише модель, і бекенд вставляє його як є.
    reply = TOOLS.replace("мінетси", "<script> & <table>") + "Відповідь."
    out = prepare(reply)
    assert "&lt;script>" in out and "&lt;table>" in out
    assert out.startswith("<details><summary><sub>")


def test_autolinks_are_not_tags():
    reply = "Пиши на <a@b.com> або <https://t.me/best>"
    assert prepare(reply) == reply


def test_plain_drops_markup_but_keeps_content():
    reply = TOOLS + "Через `<details>`, а в документі <tr>.\n\n" + SOURCES + FOOTER
    out = plain(reply)
    assert "&lt;details&gt;" in out  # із коду
    assert "&lt;tr&gt;" in out  # процитований текст
    assert "<" not in out
    assert "📚 Джерела" in out and "Пошук в базі: мінетси\n↳" in out
    assert "summary" not in out.replace("`&lt;details&gt;`", "")
