"""Написання, що різняться лише символом: апострофи й кирилиця, схожа на латиницю.

Текст користувача чи моделі, що порівнюється з даними (регулярки, `startswith`,
`MatchText`), має проходити через ці функції.
"""

import re

# У даних змішано п'ять апострофів («Дар'я», «Дарʼя», «Мар’ян», `ІЯК`17`). `ʼ`
# (U+02BC) для `\w` і токенізатора Qdrant — літера, решта — роздільники.
APOSTROPHES = "'’‘`ʼ"
_APOSTROPHE = re.compile(f"[{APOSTROPHES}]")

# Кирилиця, схожа на латиницю: великі — у статусах CRM (`СTF'25` з кириличною С),
# малі — у римських номерах бордів (`XXIІ`, після `lower()`).
LOOKALIKE = str.maketrans("АВЕКМНОРСТХіхмс", "ABEKMHOPCTXixmc")


def fold_apostrophes(text: str) -> str:
    """Усі апострофи — `'`."""
    return _APOSTROPHE.sub("'", text)


def any_apostrophe_regex(text: str) -> str:
    """Регулярка для `text`, де апостроф — будь-який."""
    parts = (re.escape(p) for p in fold_apostrophes(text).split("'"))
    return f"[{APOSTROPHES}]".join(parts)
