"""Дати, записані лише кольором клітинок, — текстом.

У плануваннях дні часто не підписані: смуга ганта — залиті порожні клітинки
під датами в шапці, а в річному календарі день — число з тією самою заливкою
чи рамкою, що й назва події в легенді. Значеннями аркуша цього не видно, і бот
відповідав «у таблиці лише помісячно».

Два правила, обидва без моделі:

- **гант**: рядок із назвою зліва і залитими порожніми клітинками праворуч →
  «назва — дата–дата» за підписами колонок над смугами;
- **календар**: число 1–31 із заливкою чи рамкою → найближча текстова клітинка
  з тим самим стилем або з квадратиком цього кольору зліва (легенда) і місяць
  над числом.

Гант — лише під шапкою з днями (13.05, 2024-05-06, число під місяцем),
календар — лише під назвою місяця: в інших таблицях колір значить що завгодно,
і без цих умов статуси й підсвічені суми ставали «датами»
(`.notes/research/sheet-colors/`).
"""

import re
from collections import Counter, defaultdict
from datetime import date, datetime

from openpyxl.worksheet.worksheet import Worksheet

MONTHS = re.compile(
    r"\b(січ|лют|берез|квіт|трав|черв|лип|серп|верес|жовт|листоп|груд|"
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",
    re.IGNORECASE,
)
DAY_NAMES = {
    "ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "НД",
    "MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN",
}  # fmt: skip
# 13.05, 1.06.2024, 2024-05-06; не 763.5
DATE = re.compile(r"\b(\d{1,2}[./]\d{1,2}([./]\d{2,4})?|\d{4}-\d{2}-\d{2})\b")
WHITE = {"FFFFFFFF", "00000000"}
MAX_ROWS = 400
MAX_COLS = 400
HEAD = "Позначено кольором клітинок:"

Mark = tuple[str, str]  # («заливка» чи «рамка», колір)


def _color(color) -> str | None:
    if color is None:
        return None
    if color.type == "rgb" and isinstance(color.rgb, str):
        value = color.rgb
    elif color.type == "theme":
        value = f"theme{color.theme}{color.tint:+.2f}"
    else:
        return None
    return None if value in WHITE else value


def _fill(cell) -> str | None:
    fill = cell.fill
    if fill is None or fill.fill_type != "solid":
        return None
    return _color(fill.fgColor)


def _borders(cell) -> set[str]:
    out = set()
    border = cell.border
    if border is None:
        return out
    for side in (border.left, border.right, border.top, border.bottom):
        if side is not None and side.style:
            color = _color(side.color)
            if color:
                out.add(color)
    return out


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _day(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    return value if isinstance(value, int) and 1 <= value <= 31 else None


def _spans(numbers: list[int]) -> str:
    """[1, 2, 3, 9] → «1–3, 9»."""
    out, start = [], None
    for i, n in enumerate(numbers):
        if start is None:
            start = n
        if i + 1 == len(numbers) or numbers[i + 1] != n + 1:
            out.append(str(start) if start == n else f"{start}–{n}")
            start = None
    return ", ".join(out)


class _Grid:
    """Значення й стиль клітинок; обʼєднана клітинка віддає значення лівої верхньої."""

    def __init__(self, ws: Worksheet):
        self.cells = {}
        for row in ws.iter_rows(
            max_row=min(ws.max_row, MAX_ROWS), max_col=min(ws.max_column, MAX_COLS)
        ):
            for c in row:
                self.cells[(c.row, c.column)] = c
        self.owner: dict[tuple[int, int], tuple[int, int]] = {}
        self.span: dict[tuple[int, int], tuple[int, int]] = {}
        for rng in ws.merged_cells.ranges:
            top = (rng.min_row, rng.min_col)
            self.span[top] = (rng.min_col, rng.max_col)
            for r in range(rng.min_row, min(rng.max_row, MAX_ROWS) + 1):
                for c in range(rng.min_col, min(rng.max_col, MAX_COLS) + 1):
                    self.owner[(r, c)] = top
        self.rows = max((r for r, _ in self.cells), default=0)
        self.cols = max((c for _, c in self.cells), default=0)

    def raw(self, r: int, c: int):
        cell = self.cells.get((r, c))
        return None if cell is None else cell.value

    def value(self, r: int, c: int):
        return self.raw(*self.owner.get((r, c), (r, c)))

    def merged(self, r: int, c: int) -> bool:
        return self.owner.get((r, c), (r, c)) != (r, c)


def _norm(text: str) -> str:
    """«С І Ч Е Н Ь» → «СІЧЕНЬ»: місяць по літері в клітинці."""
    return text.replace(" ", "") if re.fullmatch(r"(\w )+\w", text) else text


def _named(text: str) -> bool:
    """«I», «-----», «09:00» — роздільники й час, а не назва події."""
    return re.search(r"[^\W\d_]{2}", text) is not None


def _swatch(g: _Grid, r: int, c: int) -> bool:
    """Залита порожня клітинка з підписом праворуч — квадратик легенди."""
    cell, nxt = g.cells.get((r, c)), g.cells.get((r, c + 1))
    return (
        cell is not None
        and cell.value is None
        and nxt is not None
        and _fill(nxt) is None
        and bool(_text(nxt.value))
        and _day(nxt.value) is None
    )


def _gantt(g: _Grid, title: str) -> list[str]:
    def fills(r: int) -> list[tuple[int, str]]:
        return [
            (c, f)
            for c in range(1, g.cols + 1)
            if (cell := g.cells.get((r, c))) is not None
            and not g.merged(r, c)
            and (f := _fill(cell))
        ]

    def dated(r: int) -> bool:
        return (
            sum(
                1
                for c in range(1, g.cols + 1)
                if _day(v := g.value(r, c)) is not None
                or MONTHS.search(t := _text(v))
                or DATE.search(t)
            )
            >= 3
        )

    # Шапка — суцільний блок верхніх рядків, де кілька дат чи місяців (рядок
    # місяців, днів тижня, чисел). Одна дата в рядку — дедлайн задачі, а
    # числа в рядку задачі нижче — вже не шапка.
    top = [r for r in range(1, min(g.rows, 12) + 1) if dated(r)]
    if not top:
        return []
    end = top[0]
    for r in top[1:]:
        if r - end > 2:
            break
        end = r
    first = end + 1

    def header(c: int) -> str:
        """Найнижча дата в шапці колонки; число без місяця — з місяцем над ним."""
        parts = [_norm(_text(g.value(r, c))) for r in range(1, first)]
        parts = [p for p in parts if p and p.upper() not in DAY_NAMES]
        # День, а не місяць: «січень '26» над тижнем — підпис блоку, не дати.
        found = [i for i, p in enumerate(parts) if DATE.search(p) or _day(p)]
        if not found:
            return ""
        d = parts[found[-1]]
        if DATE.search(d):
            return d
        month = next((p for p in reversed(parts[: found[-1]]) if MONTHS.search(p)), "")
        # Аркуш на місяць: «Листопад», а в шапці лише 1…30.
        month = month or (title if MONTHS.search(title) else "")
        return f"{month} {d}" if month else ""

    heads = {c: h for c in range(1, g.cols + 1) if (h := header(c))}
    if not heads:
        return []
    start = min(heads)

    rows: dict[int, tuple[str, list[tuple[int, str]]]] = {}
    for r in range(first, g.rows + 1):
        # Назва — увесь текст лівіше дат: «FR · Пошук партнерів».
        left = [
            (c, t)
            for c in range(1, start)
            if (t := _text(g.value(r, c))) and _day(g.value(r, c)) is None
        ]
        if not any(_named(t) for _, t in left):
            continue
        own = {_fill(cell) for c, _ in left if (cell := g.cells.get((r, c)))}
        # Залиті кольором самої назви клітинки — плашка легенди, не смуга.
        bar = [
            (c, f)
            for c, f in fills(r)
            if c in heads and f not in own and not _swatch(g, r, c)
        ]
        if bar:
            rows[r] = (" · ".join(dict.fromkeys(t for _, t in left)), bar)
    if not rows:
        return []

    # Колонка «сьогодні», вихідних чи самого івенту залита в шапці або майже
    # в усіх рядках — це виділення колонки, а не смуга задачі.
    per_column = Counter(cf for _, bar in rows.values() for cf in bar)
    highlight = {
        (c, f)
        for c in heads
        for f in {f for (cc, f) in per_column if cc == c}
        if per_column[(c, f)] >= max(3, 0.8 * len(rows))
        or any(
            (cell := g.cells.get((r, c))) is not None and _fill(cell) == f
            for r in range(1, first)
        )
    }

    lines = []
    for r in sorted(rows):
        label, bar = rows[r]
        cells = [cf for cf in bar if cf not in highlight]
        runs, begin = [], None
        for i, (c, _) in enumerate(cells):
            if begin is None:
                begin = c
            nxt = cells[i + 1] if i + 1 < len(cells) else None
            if nxt is None or nxt[0] != c + 1:
                a, b = heads[begin], heads[c]
                runs.append(a if a == b else f"{a} – {b}")
                begin = None
        if runs:
            lines.append(f"{label} — {', '.join(runs)}")
    return lines


def _gantt_header(g: _Grid) -> set[tuple[int, int]]:
    """Числа з ряду 8+ днів підряд у рядку — шапка ганта, а не тиждень календаря."""
    out: set[tuple[int, int]] = set()
    for r in range(1, g.rows + 1):
        run: list[tuple[int, int]] = []
        prev = None
        for c in range(1, g.cols + 2):
            day = _day(g.raw(r, c))
            if day is not None and prev is not None and day == prev + 1:
                run.append((r, c))
            else:
                if len(run) >= 8:
                    out.update(run)
                run = [(r, c)] if day is not None else []
            prev = day
    return out


def _calendar(g: _Grid) -> list[str]:
    grid_border = Counter(
        b for cell in g.cells.values() for b in _borders(cell)
    ).most_common(1)
    grid = grid_border[0][0] if grid_border else None

    def marks(cell) -> set[Mark]:
        out = {("рамка", b) for b in _borders(cell) if b != grid}
        if fill := _fill(cell):
            out.add(("заливка", fill))
        return out

    legend: dict[Mark, list[tuple[int, int, int, str]]] = defaultdict(list)
    days = []
    header = _gantt_header(g)
    for (r, c), cell in g.cells.items():
        if g.merged(r, c):
            continue
        m = marks(cell)
        if not m:
            continue
        if (day := _day(cell.value)) is not None:
            days.append((r, c, day, m))
            continue
        if _swatch(g, r, c) and (fill := _fill(cell)):
            lo, hi = g.span.get((r, c + 1), (c + 1, c + 1))
            if _named(text := _text(g.value(r, c + 1))):
                legend[("заливка", fill)].append((r, lo, hi, text))
            continue
        text = _text(cell.value)
        if _named(text) and text.upper() not in DAY_NAMES:
            lo, hi = g.span.get((r, c), (c, c))
            for mark in m:
                legend[mark].append((r, lo, hi, text))
    if not days or not legend:
        return []

    def month(r: int, c: int) -> str | None:
        for rr in range(r - 1, 0, -1):
            text = _text(g.value(rr, c))
            if text and _day(g.value(rr, c)) is None and MONTHS.search(text):
                return text
        return None

    # Назва місяця в рамці — заголовок блоку, а не подія легенди.
    heads = {month(r, c) for r, c, _, _ in days}
    legend = {
        mark: kept
        for mark, entries in legend.items()
        if (kept := [e for e in entries if e[3] not in heads])
    }

    # Колір більшості днів — фон календаря, а не подія.
    total = sum(1 for cell in g.cells.values() if _day(cell.value) is not None)
    spread = Counter(mark for _, _, _, m in days for mark in m)
    background = {mark for mark, n in spread.items() if n > total / 2}
    events: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    in_calendar: set[str] = set()  # події, що трапились не лише в шапці ганта
    order: dict[str, tuple[int, int]] = {}
    months: dict[str, tuple[int, int]] = {}  # місяць → місце першого числа
    for r, c, day, m in days:
        name = month(r, c)
        if name is None:
            continue
        months[name] = min(months.get(name, (c, r)), (c, r))
        for mark in m - background:
            if mark not in legend:
                continue
            # У шапці ганта колір числа — день івенту («Anniversary»), але
            # тим самим кольором там часто залиті всі назви задач.
            if (r, c) in header and len({e[3] for e in legend[mark]}) > 3:
                continue
            # Найближча легенда: спершу та, під чиєю шириною стоїть число.
            lr, lo, hi, text = min(
                legend[mark],
                key=lambda e: (max(e[1] - c, c - e[2], 0), abs(e[0] - r)),
            )
            events[text][name].add(day)
            order.setdefault(text, (lo, lr))
            if (r, c) not in header:
                in_calendar.add(text)

    def short(text: str) -> bool:
        """Подія з шапки ганта — кілька днів одним-двома шматками; вихідні
        щотижня чи весь місяць — виділення колонок, а не подія."""
        days = [sorted(d) for d in events[text].values()]
        runs = sum(_spans(d).count(",") + 1 for d in days)
        return sum(map(len, days)) <= 10 and runs <= 2

    lines = []
    for text in sorted(events, key=lambda t: order[t]):
        if text not in in_calendar and not short(text):
            continue
        parts = [
            f"{m} {_spans(sorted(d))}"
            for m, d in sorted(events[text].items(), key=lambda kv: months[kv[0]])
        ]
        lines.append(f"{text} — {'; '.join(parts)}")
    return lines


def colored_lines(ws: Worksheet) -> list[str]:
    """Рядки «подія — дні» з кольорів аркуша; порожньо, якщо кольори не про дати."""
    g = _Grid(ws)
    lines = _gantt(g, ws.title) + _calendar(g)
    return [HEAD, *lines] if lines else []
