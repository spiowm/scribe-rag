"""Маскування карток та IBAN перед індексацією.

Файли з паролями й таблиці контактів, позначені як інфобуки, пропускаються
за назвою. Номери телефонів у решті документів зберігаються.
"""

import re
from collections.abc import Callable

# За назвою файлу: вміст таких файлів маскуванням не врятувати.
SECRET_FILE = re.compile(
    r"password|парол|info(?:rmation)?[ _-]?book|інфо[ _-]?бук", re.IGNORECASE
)

# Межі — не лише цифри, а й латинські літери: інакше цифровий відрізок
# усередині hex-id сторінки може пройти Luhn і пошкодити посилання.
CARD = re.compile(r"(?<![0-9A-Za-z])(?:\d[ -]?){12,18}\d(?![0-9A-Za-z])")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _iban_valid(raw: str) -> bool:
    """Контрольна сума ISO 13616: без неї під шаблон лізуть коди й артикули."""
    s = raw.replace(" ", "")
    moved = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1


def _valid_prefix(raw: str, valid: Callable[[str], bool]) -> int:
    """Довжина найдовшого префікса за групами, що проходить перевірку; 0 — немає.

    Шаблони жадібні й захоплюють сусіднє число чи слово («… 1111 50 грн»,
    «UA… EUR»): сума тоді не сходиться, а коротший збіг re.sub уже не пробує.
    """
    ends = [i for i in range(len(raw) - 1, 0, -1) if raw[i] in " -"]
    for end in [len(raw), *ends]:
        if valid(raw[:end]):
            return end
    return 0


def is_secret_file(name: str) -> bool:
    return bool(SECRET_FILE.search(name))


def mask(text: str) -> tuple[str, int]:
    """Текст із замаскованими картками й IBAN і скільки замін зроблено."""
    hits = 0

    def iban(m: re.Match) -> str:
        nonlocal hits
        raw = m.group()
        end = _valid_prefix(
            raw, lambda s: len(s.replace(" ", "")) >= 15 and _iban_valid(s)
        )
        if not end:
            return raw
        hits += 1
        return "[IBAN]" + raw[end:]

    def card(m: re.Match) -> str:
        nonlocal hits
        raw = m.group()

        # Перша цифра та довжини груп допомагають відрізнити номер картки
        # від ідентифікаторів, двійкових чисел і сум із сусідніх клітинок.
        def valid(s: str) -> bool:
            digits = re.sub(r"\D", "", s)
            groups = [len(g) for g in re.split(r"[ -]", s)]
            return (
                13 <= len(digits) <= 19
                and (digits[0] in "23456" or digits.startswith("9804"))
                and (
                    len(groups) == 1
                    or groups == [4, 6, 5]
                    or (set(groups[:-1]) == {4} and groups[-1] <= 4)
                )
                and _luhn(digits)
            )

        end = _valid_prefix(raw, valid)
        if not end:
            return raw
        hits += 1
        return "[номер картки]" + raw[end:]

    # IBAN першим: у ньому самому є довгий ряд цифр, який міг би пройти Luhn
    text = IBAN.sub(iban, text)
    text = CARD.sub(card, text)
    return text, hits
