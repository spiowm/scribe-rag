import itertools
import re

from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import TextNode
from llama_index.core.utils import get_tokenizer

from src.ingestion.schemas import ChunkMetadata
from src.text import APOSTROPHES

# Мінетси — протоколи зборів: репліки «Ім'я: текст» у розділах «Звітність …»,
# «Вибори …». Нарізка за репліками зберігає зв’язок питання з відповіддю,
# а заголовок розділу в кожному фрагменті вказує, кого й що обговорюють.
# Шлях загального диска — від його кореня, мінетси лежать у `Documents`.
MINUTES = "Documents/2. Minutes"

UP = "A-ZА-ЯІЇЄҐ"
LO = f"a-zа-яіїєґ{APOSTROPHES}\\-"
# Маркери (i)/(ii)/(bs)/(бс)/(Булшит) і зірочки markdown на початку рядка, далі
# 1–3 слова з великої (і через дефіс: «Анна-Марія») і двокрапка. У частині PDF
# маркер стоїть після імені: «Іван Вигадко (іі):», «Тьома(і):», «Іра Прикладна дот:».
MARK = r"\((?:[iіIІ]{1,3}|bs|бс|[бБ]улшит|[cCсС])\)"
WORD = rf"[{UP}][{LO}]*(?:-[{UP}][{LO}]*)*\.?"
TURN_RE = re.compile(
    rf"^[ \t*]*(?:{MARK}[ \t*]*)*"
    rf"(?P<name>[{UP}][{LO}]+(?:-[{UP}][{LO}]*)*\.?(?:[ \t*]+{WORD}){{0,2}})"
    rf"(?:[ \t]*{MARK}|[ \t]+дот(?:ичне)?)?[ \t*]*:[ \t*]*(?P<rest>.*)$",
    re.MULTILINE,
)
NOT_SPEAKERS = {
    "Результати", "Тривалість", "Голосування", "Присутні", "Подія", "Дата", "Початок",
    "Запізнення", "Промови", "Офіційні кандидати", "Номіновані", "Фасилітатор", "Кінець",
    "Мінетстейкери", "Відсутні", "Інше", "Плани", "Питання", "Відповідь", "Примітка",
    "Результати голосування", "Голосів", "За", "Проти", "Бланк", "Утримались",
}  # fmt: skip
# Початок розділу: markdown-заголовок (Google Docs) або рядок PDF
# «[11:40 — ]Звітність/Вибори …».
SECTION_RE = re.compile(
    r"^(?:#{1,3}[ \t]*\**[ \t]*\S.*|(?:\d{1,2}[:.]\d{2}[ \t]*[-—–][ \t]*)?"
    r"(?:Звітність|Вибори|Довибори)[ \t]+\S.{0,80})$",
    re.MULTILINE | re.IGNORECASE,
)
# Службові підзаголовки: межа розділу, але не назва — під «### Q&A» назвою
# лишається «Вибори VP4DS».
GENERIC_RE = re.compile(
    r"^(q\s*\\?&\s*a|q/a|голосування|промова|відгук ревкому|звітність (\S+ )?затвердж|"
    r"результат|раунд|кінець|початок)",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"^\d{1,2}[:.]\d{2}\s*[-—–]\s*")

TOKENIZER = get_tokenizer()


def ntok(text: str) -> int:
    return len(TOKENIZER(text))


def section_heads(text: str) -> list[tuple[int, str | None]]:
    """[(початок розділу, назва)]. Назва — шлях заголовків «А › Б» без службових.

    Рівень — кількість `#`; рядки PDF «Звітність …/Вибори …» — рівень 1.
    """
    out: list[tuple[int, str | None]] = [(0, None)]
    stack: list[tuple[int, str]] = []
    for m in SECTION_RE.finditer(text):
        raw = m.group()
        # `\r` — кінці рядків у тексті частини PDF, без нього назва несла б його далі
        title = raw.strip("#* \t\r\\:").replace("**", "").replace("\\", "").strip(" *:")
        title = TIME_RE.sub("", title)
        if len(title) <= 2:
            continue
        level = len(raw) - len(raw.lstrip("#"))
        # рядок PDF з малої літери — речення, а не заголовок («звітність віпішок так само…»)
        if not level and not title[0].isupper():
            continue
        level = level or 1
        stack = [(lv, t) for lv, t in stack if lv < level]
        if not GENERIC_RE.match(title):
            stack.append((level, title[:80]))
        out.append((m.start(), " › ".join(t for _, t in stack) or None))
    return out


def question_starts(text: str) -> set[int]:
    """Початки питань: репліка з «?», за якою одразу йде репліка іншої людини.

    Репліка триває до наступної репліки або початку розділу. Відповідь, у якій
    теж є «?», — не початок питання, інакше її відірвало б від свого.
    """
    sections = [
        m.start()
        for m in SECTION_RE.finditer(text)
        if len(m.group().strip("#* \t\\")) > 2
    ]
    marks = []
    for m in TURN_RE.finditer(text):
        name = re.sub(r"[ \t*]+", " ", m.group("name")).strip()
        if name not in NOT_SPEAKERS and m.group("rest").strip():
            marks.append((m.start(), name))
    turns = []
    for i, (start, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        end = next((s for s in sections if start < s < end), end)
        turns.append((start, end, name))
    questions, answers = set(), set()
    for q, a in itertools.pairwise(turns):
        if "?" in text[q[0] : q[1]] and a[2] != q[2] and q[1] == a[0]:
            questions.add(q[0])
            answers.add(a[0])
    return questions - answers


SHEET = "## Аркуш"  # початок аркуша в тексті таблиці (`sheets.HEAD`)
# Фрагмент таблиці: опис зазвичай 260 токенів (90% — до 700), тож 2000 лишає
# цілим майже все. Межа ембедингу gemini-embedding-2 — 8192, довше обрізається мовчки.
TABLE_CHUNK = 2000

# Бюджет фрагмента таблиці, що йде у вектор цілою: шапка + цілі рядки. Запис
# Бази подій — 100–700 символів, тож у фрагменті одна-дві події, а не пів десятка.
ROWS_BUDGET = 800


class Chunker:
    def __init__(self):
        self.parser = SentenceSplitter(chunk_size=800, chunk_overlap=100)

    def chunk(self, text: str, metadata: ChunkMetadata) -> list[TextNode]:
        if not text or not text.strip():
            return []

        # scope додається вже після нарізки: SentenceSplitter віднімає від
        # бюджету чанка довжину всіх метаданих, і нове поле зсунуло б межі
        # чанків відносно тих самих документів у наявному індексі.
        fields = metadata.model_dump()
        scope = fields.pop("scope")
        if metadata.path.startswith(MINUTES):
            nodes = self._minutes(text, fields)
        else:
            nodes = self._split(text, fields)
        for node in nodes:
            node.metadata["scope"] = scope
            node.excluded_embed_metadata_keys.append("scope")
        return nodes

    def _split(
        self, text: str, fields: dict, parser: SentenceSplitter | None = None
    ) -> list[TextNode]:
        doc = Document(text=text, metadata=fields)
        doc.excluded_embed_metadata_keys = [
            "source",
            "source_id",
            "url",
            "last_edited",
        ]
        return [
            node
            for node in (parser or self.parser).get_nodes_from_documents([doc])
            if isinstance(node, TextNode)
        ]

    def _minutes(self, text: str, fields: dict) -> list[TextNode]:
        """Межа лише на початку розділу й перед питанням, назва розділу — у `section`.

        Шматки від питання до питання пакуються жадібно, поки влазять у бюджет
        фрагмента. Завеликий шматок ріже SentenceSplitter, як і решту корпусу.
        Назва йде і у вектор, і в бюджет — як решта метаданих.
        """
        heads = section_heads(text)
        questions = question_starts(text)
        nodes: list[TextNode] = []
        for i, (start, section) in enumerate(heads):
            end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
            if not text[start:end].strip():
                continue
            meta = {**fields, "section": section} if section else fields
            # той самий рядок метаданих, який SentenceSplitter віднімає від бюджету
            budget = self.parser.chunk_size - ntok(
                self.parser._get_metadata_str(Document(text="", metadata=meta))
            )
            cuts = [start, *sorted(q for q in questions if start < q < end), end]
            groups: list[list[int]] = []
            for a, b in itertools.pairwise(cuts):
                if not text[a:b].strip():
                    continue
                if groups and ntok(text[groups[-1][0] : b]) <= budget:
                    groups[-1][1] = b
                else:
                    groups.append([a, b])
            for a, b in groups:
                nodes += self._split(text[a:b], meta)
        return nodes

    def chunk_table(
        self, text: str, metadata: ChunkMetadata, size: int = TABLE_CHUNK
    ) -> list[TextNode]:
        """Таблиця Drive (опис або ціла мала) — один фрагмент, а більша — за аркушами.

        SentenceSplitter на 800 токенів різав 10% описів посеред переліку колонок
        і малі таблиці посеред запису, а два фрагменти однієї таблиці займали два
        місця в топ-25. Тут фрагмент — рядок «Таблиця «…»» (якщо є) і цілі аркуші,
        поки влазять у `size`; завеликий аркуш — його шапка й цілі рядки
        (`.notes/research/table-chunks/`).
        """
        if not text or not text.strip():
            return []
        fields = metadata.model_dump()
        scope = fields.pop("scope")
        parser = SentenceSplitter(chunk_size=size, chunk_overlap=100)
        budget = size - ntok(
            parser._get_metadata_str(Document(text="", metadata=fields))
        )
        lines = text.strip().split("\n")
        prefix = [] if lines[0].startswith(SHEET) else [lines.pop(0)]
        sheets: list[list[str]] = []
        for line in lines:
            if line.startswith(SHEET) or not sheets:
                sheets.append([])
            sheets[-1].append(line)

        def fits(part: list[str]) -> bool:
            return ntok("\n".join(part)) <= budget

        units: list[list[str]] = []  # аркуш цілим або шапка аркуша + цілі рядки
        for sheet in sheets:
            if fits(prefix + sheet):
                units.append(sheet)
                continue
            head, rows = sheet[0], sheet[1:]
            units.append([head])
            for row in rows:
                if len(units[-1]) > 1 and not fits(prefix + units[-1] + [row]):
                    units.append([head])
                units[-1].append(row)
        parts: list[list[str]] = []
        for unit in units:
            if parts and fits(prefix + parts[-1] + unit):
                parts[-1] += unit
            else:
                parts.append(list(unit))
        nodes = [
            node
            for part in parts
            for node in self._split("\n".join(prefix + part), fields, parser)
        ]
        for node in nodes:
            node.metadata["scope"] = scope
            node.excluded_embed_metadata_keys.append("scope")
        return nodes

    def chunk_rows(
        self, text: str, metadata: ChunkMetadata, budget: int = ROWS_BUDGET
    ) -> list[TextNode]:
        """Таблиця цілою: кожен фрагмент — шапка й цілі рядки до `budget` символів.

        SentenceSplitter різав записи навпіл, і склад кортіми опинявся у
        фрагменті без назви події: «хто був у кортімі ІЯК 2026» його не
        знаходив. Рядок, довший за бюджет, іде окремим фрагментом.
        """
        if not text.strip():
            return []
        head, *rows = text.strip().splitlines()
        parts: list[list[str]] = []
        size = budget  # перший рядок завжди відкриває новий фрагмент
        for row in rows:
            if size + len(row) + 1 > budget:
                parts.append([])
                size = len(head)
            parts[-1].append(row)
            size += len(row) + 1
        if not parts:
            return self.chunk(head, metadata)
        return [
            node
            for part in parts
            for node in self.chunk("\n".join([head, *part]), metadata)
        ]
