from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import MetadataMode

from src.ingestion.chunker import TURN_RE, Chunker, question_starts, section_heads
from src.ingestion.schemas import GENERAL, ChunkMetadata

FILLER = "Це довга відповідь про бюджет, партнерів і кортіму, щоб фрагмент заповнився. "


def meta(path: str) -> ChunkMetadata:
    return ChunkMetadata(
        source="gdrive",
        source_id="doc",
        title="Election XXIV Board",
        url="https://example.com",
        last_edited="2026-04-20T10:00:00Z",
        path=path,
        scope=GENERAL,
    )


def minutes(pairs: int = 12) -> str:
    lines = [
        "# Election XXIV Board",
        "",
        "## **Вибори секретаря**",
        "",
        "### **Q/A**",
        "",
    ]
    for i in range(pairs):
        lines.append(f"*(i)* **Петро Приклад:** питання номер {i}, що з SoDeep?")
        lines.append(
            f"**Олена Зразок:** відповідь {i}. " + FILLER * (3 + i % 5) + f"Кінець {i}."
        )
        lines.append("")
    lines += ["## **Вибори VP4PR**", "", "**Ганна Тестова:** промова кандидатки."]
    return "\n".join(lines)


def test_question_and_answer_in_one_chunk():
    nodes = Chunker().chunk(minutes(), meta("Documents/2. Minutes/24. XXIV/Election"))
    assert len(nodes) > 2
    for i in range(12):
        # кінець відповіді — у тому ж фрагменті, що й питання
        tails = [n for n in nodes if f"Кінець {i}." in n.text]
        assert tails and all(f"питання номер {i}," in n.text for n in tails)


def test_section_name_in_metadata_and_vector():
    nodes = Chunker().chunk(minutes(), meta("Documents/2. Minutes/24. XXIV/Election"))
    by_text = {n.text.split("\n")[0]: n for n in nodes}
    assert {n.metadata.get("section") for n in nodes} == {
        "Election XXIV Board",
        "Election XXIV Board › Вибори секретаря",
        "Election XXIV Board › Вибори VP4PR",
    }
    last = by_text["## **Вибори VP4PR**"]
    assert last.metadata["scope"] == "general"
    embedded = last.get_content(metadata_mode=MetadataMode.EMBED)
    assert "section: Election XXIV Board › Вибори VP4PR" in embedded
    assert "scope" not in embedded and "source_id" not in embedded


def test_section_heads_skip_generic_and_lowercase():
    text = (
        "11:40 — Звітність VP4HR — Марії Зразкової\r\n"
        "Q&A\n"
        "Ім'я: текст\n"
        "звітність віпішок так само як і минулого року\n"
        "Вибори секретаря\n"
    )
    assert [name for _, name in section_heads(text)] == [
        None,
        "Звітність VP4HR — Марії Зразкової",
        "Вибори секретаря",
    ]


def test_turn_forms():
    # форми реплік із 12 мінетсів: маркер до імені, після імені, ім'я через дефіс
    lines = {
        "*(i)* **Петро Приклад:** питання?": "Петро Приклад",
        "Іван Вигадко (іі): Як справлятись з інфою на драйвах?": "Іван Вигадко",
        "Тьома Тестенко(і): Чого не вистачає бейбікам?": "Тьома Тестенко",
        "Вітя (с): BKT це Біг КТ?": "Вітя",
        "Іра Прикладна дот: Чи проходили КТ?": "Іра Прикладна",
        "Павлик дотичне: Розкажи про мотивацію?": "Павлик",
        "*(бс)* **Аня Зразкова:** Постав по пріоритетності.": "Аня Зразкова",
        "*(іі) **Анна-Марія Тест**:* Орієнтуємось на 1,5 року.": "Анна-Марія Тест",
        "(Булшит) Захар: НАВЧАЛЬНО-РОЗВАЖАЛЬНИЙ КОМПЛЕКС": "Захар",
    }
    for line, name in lines.items():
        m = TURN_RE.match(line)
        assert m and m.group("name").strip(" *") == name, line
    for line in ("Лічильна комісія: Павло Зразок", "11:35 - Початок", "(bs): анонім"):
        assert not TURN_RE.match(line), line


def test_question_with_marker_after_name():
    # PDF: маркер після імені; без нього межі перед питанням не було б
    text = (
        "Q&A. Звітність Секретаря\r\n"
        "Артем Приклад (і): Що з перенесенням інформації з драйву?\r\n"
        "Андрюха Тестенко: Вона перенесена за минулого борда.\r\n"
        "Іван Вигадко (іі): Як справлятись, коли інфа МО на драйвах борду?\r\n"
        "Андрюха Тестенко: Вирішили в моменті. BCI тепер має свій драйв.\r\n"
    )
    assert question_starts(text) == {text.index("Артем"), text.index("Іван")}


def test_other_documents_cut_as_before():
    # не-мінетси ріжуться звичайним SentenceSplitter, без поля section
    text = minutes()
    md = meta("Documents/1. Action Plans/24. XXIV")
    nodes = Chunker().chunk(text, md)
    fields = md.model_dump()
    fields.pop("scope")
    doc = Document(text=text, metadata=fields)
    doc.excluded_embed_metadata_keys = ["source", "source_id", "url", "last_edited"]
    expected = SentenceSplitter(chunk_size=800, chunk_overlap=100)
    assert [n.text for n in nodes] == [
        n.get_content() for n in expected.get_nodes_from_documents([doc])
    ]
    assert all("section" not in n.metadata for n in nodes)
