import json
from dataclasses import dataclass, field

from google.genai import types

NOTE = (
    "Не запускай команд, не читай і не пиши файлів, не шукай в інтернеті — "
    "такі дії заборонені. Прочитай і одразу поверни структурований вивід.\n\n"
)
PROTOCOL = (
    "\n\n=== Протокол ===\n"
    "На кожне моє повідомлення відповідай ОДНИМ JSON: "
    '{"action": "call_tools", "tool_calls": [{"name": "…", "args_json": "…"}]} '
    'або {"action": "answer", "answer": "…"}. '
    "Результати інструментів я надсилатиму наступними повідомленнями."
)
RULE_TOOLS = (
    "\n\n=== Твій хід ===\n"
    "Якщо потрібні дані — action=call_tools і tool_calls (args_json — JSON-рядок "
    "аргументів; кілька незалежних викликів можна разом). Якщо даних досить — "
    "action=answer і повна відповідь користувачу в answer."
)

RULE_ANSWER = (
    "\n\n=== Твій хід ===\n"
    "Інструментів більше немає. Поверни action=answer і повну відповідь "
    "користувачу в answer."
)


def render_tools_doc(tools: list[types.Tool]) -> str:
    """Описує інструменти текстом: agy не приймає декларацій API, лише текст.

    Беремо ті самі декларації, що йдуть у Gemini API, тож опис ніколи
    не розійдеться з кодом.
    """
    blocks = []
    for tool in tools:
        for decl in tool.function_declarations or []:
            params = (
                decl.parameters.model_dump(exclude_none=True, mode="json")
                if decl.parameters
                else {}
            )
            required = set(params.get("required", []))
            lines = []
            for name, spec in params.get("properties", {}).items():
                kind = spec.get("type", "").lower()
                if kind == "array":
                    kind = f"array<{spec.get('items', {}).get('type', '').lower()}>"
                flag = ", обовʼязковий" if name in required else ""
                enum = (
                    f" (одне з: {', '.join(spec['enum'])})" if spec.get("enum") else ""
                )
                desc = spec.get("description", "").replace("\n", " ")
                lines.append(f"- `{name}` ({kind}{flag}){enum}: {desc}")
            blocks.append(
                f"### `{decl.name}`\n\n{decl.description or ''}\n\n"
                "Аргументи:\n" + "\n".join(lines)
            )
    return "\n\n".join(blocks)


def build_schema(tools: list[types.Tool]) -> dict:
    """JSON-схема відповіді моделі: або виклики інструментів, або текст.

    Імена інструментів беремо з декларацій, тож модель не зможе назвати
    інструмент, якого немає.
    """
    names = [decl.name for tool in tools for decl in tool.function_declarations or []]
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["call_tools", "answer"]},
            "tool_calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": names},
                        "args_json": {"type": "string"},
                    },
                    "required": ["name", "args_json"],
                },
            },
            "answer": {"type": "string"},
        },
        "required": ["action"],
    }


def first_message(
    system_instruction: str,
    tools_doc: str,
    history: list[tuple[str, str]],
    message: str,
) -> str:
    """Перше повідомлення розмови: усе, що модель має знати, одним текстом."""
    who = {"user": "користувач", "assistant": "ти"}
    lines = [f"[{who.get(role, role)}] {text}" for role, text in history]
    lines.append(f"[користувач] {message}")
    return (
        NOTE
        + "=== Системні інструкції бота ===\n"
        + system_instruction
        + "\n\n=== Інструменти ===\n"
        + tools_doc
        + PROTOCOL
        + "\n\n=== Розмова ===\n"
        + "\n\n".join(lines)
        + RULE_TOOLS
    )


def tool_results_message(results: list[tuple[str, dict]], final: bool = False) -> str:
    """Наступні повідомлення розмови: результати інструментів і правило ходу.

    `final` — останній крок циклу: інструментів більше не даємо, лише відповідь.
    """
    lines = [
        f"[результат {name}] {json.dumps(result, ensure_ascii=False, default=str)}"
        for name, result in results
    ]
    return (
        "=== Результати інструментів ===\n"
        + "\n\n".join(lines)
        + (RULE_ANSWER if final else RULE_TOOLS)
    )


def _decode(result: dict) -> dict:
    """Дістає JSON моделі з result agy — із запасним шляхом, якщо схема не спрацювала."""
    data = result.get("structured_output")
    if isinstance(data, dict):
        return data
    # Схема не спрацювала: шукаємо {…} у звичайному тексті відповіді.
    text = (result.get("response") or "").strip()
    if "{" in text and "}" in text:
        try:
            data = json.loads(text[text.index("{") : text.rindex("}") + 1])
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            return data
    # Зовсім не JSON — вважаємо весь текст відповіддю людині.
    return {"action": "answer", "answer": text}


def _unwrap(answer: str) -> str:
    """Модель інколи кладе весь JSON усередину answer — дістаємо справжній текст."""
    for _ in range(3):
        stripped = answer.strip()
        if not stripped.startswith("{"):
            break
        try:
            inner = json.loads(stripped)
        except json.JSONDecodeError:
            break
        if not isinstance(inner, dict) or "answer" not in inner:
            break
        answer = str(inner.get("answer") or "")
    return answer


@dataclass
class Step:
    """Що модель вирішила на цьому кроці."""

    calls: list[tuple[str, dict]] = field(default_factory=list)
    errors: list[tuple[str, dict]] = field(default_factory=list)
    answer: str = ""

    @property
    def empty(self) -> bool:
        """Ні викликів, ні помилок, ні тексту — хід треба повторити."""
        return not self.calls and not self.errors and not self.answer.strip()


def parse_step(result: dict) -> Step:
    """Перетворює result agy на рішення моделі: виклики інструментів або відповідь."""
    data = _decode(result)
    if data.get("action") != "call_tools":
        return Step(answer=_unwrap(str(data.get("answer") or "")))

    step = Step()
    for call in data.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        name = str(call.get("name") or "")
        try:
            args = json.loads(call.get("args_json") or "{}")
        except json.JSONDecodeError:
            args = None
        if not isinstance(args, dict):
            # Не виконуємо навмання з порожніми аргументами, а кажемо моделі,
            # що не так, — наступним кроком вона виправиться сама.
            step.errors.append((name, {"error": "args_json має бути JSON-обʼєктом"}))
            continue
        step.calls.append((name, args))
    return step
