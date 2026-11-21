from pydantic import BaseModel

# Мітка доступу того, що бачать усі: Notion і загальний диск. Решта — id диска.
GENERAL = "general"


class ChunkMetadata(BaseModel):
    source: str
    source_id: str
    title: str
    url: str
    last_edited: str
    path: str = ""
    # Хто може бачити фрагмент: GENERAL або id диска. Фільтр пошуку за ним
    # підставляє сервер з особи користувача, модель його не задає. Без
    # дефолту: фрагмент, створений без мітки, інакше мовчки відкрився б усім.
    scope: str


class ChunkPayload(ChunkMetadata):
    text: str
    # Назва розділу мінетсів («Вибори секретаря»): її фрагмент сам по собі
    # здебільшого не містить. Задається нарізкою; у решти корпусу порожнє, а
    # в точках, проіндексованих раніше, його немає зовсім.
    section: str | None = None


class SearchHit(BaseModel):
    score: float
    chunk: ChunkPayload
