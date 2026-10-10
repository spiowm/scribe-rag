def embed_text(rec: dict) -> str:
    parts = []
    if rec["caption"]:
        parts.append(f"Підпис: {rec['caption']}")
    if rec["album_caption"]:
        parts.append(f"Підпис групи: {rec['album_caption']}")
    if rec["text_in_image"]:
        parts.append(f"Текст на картинці: {rec['text_in_image']}")
    parts.append(f"Про що жарт: {rec['joke']}")
    if rec["people"]:
        parts.append(f"Люди: {', '.join(rec['people'])}")
    parts.append(f"Теми: {', '.join(rec['topics'])}")
    return "\n".join(parts)


import logging
import random
import statistics
from collections import deque

from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
from qdrant_client import AsyncQdrantClient

from src.connectors.r2 import presigned_get

logger = logging.getLogger(__name__)

# Наскільки мем має вистрибувати над середнім, щоб вважатись влучним.
# Нижче цього беремо випадковий — мем їде завжди.
GATE = 3.0
# Скільки останніх мемів юзера пам'ятаємо, щоб не повторюватись одразу.
RECENT = 20
# Телеграм забирає файл одразу, тож підпису вистачає хвилин. Коли файл уже
# в нього, протермінований URL старих повідомлень не ламає.
URL_TTL = 120


class MemeRepository:
    def __init__(
        self,
        client: AsyncQdrantClient,
        collection: str,
        embedding_model: GoogleGenAIEmbedding,
    ) -> None:
        # Клієнт спільний із QdrantRepository: сервер один, пул з'єднань теж
        # має бути один. Різні тут лише колекція й політика відбору.
        self.client = client
        self.collection = collection
        self.embedding_model = embedding_model
        # Покази живуть у памʼяті: втрата на рестарті коштує одного повтору.
        self.recent: dict[int, deque[int]] = {}
        self.total = 0

    async def warm(self) -> int:
        """Розмір колекції. Потрібен, щоб брати скори по ВСІХ мемах за раз.

        Відсутня колекція — не помилка: меми це окраса, і бот мусить
        піднятись без них. Тоді total лишається нулем, а pick() віддає None.
        """
        try:
            info = await self.client.get_collection(self.collection)
        except Exception:
            logger.warning("колекції %s немає — меми вимкнені", self.collection)
            return 0
        self.total = info.points_count or 0
        return self.total

    async def pick(self, question: str, user_id: int) -> dict | None:
        """Мем до питання або None.

        Ніколи не кидає, і це тримається структурою, а не обіцянкою в
        докстрінгу: у `chat.py` виклик стоїть перед `try/finally`, тож виняток
        звідси не лише забрав би відповідь, а й не дав би звільнити слот
        юзера — той залип би на всі BUSY_TTL.
        """
        if not self.total:
            return None
        try:
            return await self._choose(question, user_id)
        except Exception:
            logger.exception("пошук мема впав: %r", question)
            return None

    async def _choose(self, question: str, user_id: int) -> dict | None:
        vector = await self.embedding_model.aget_query_embedding(question)
        found = await self.client.query_points(
            self.collection,
            query=vector,
            limit=self.total,
            with_payload=True,
        )

        points = found.points
        scores = [point.score for point in points]
        if len(scores) < 2:
            return None
        mu, sd = statistics.mean(scores), statistics.pstdev(scores)
        if not sd:
            return None

        seen = self.recent.setdefault(user_id, deque(maxlen=RECENT))

        # Ворота вирішують не «мем чи ні», а «влучний чи просто випадковий»:
        # правильного мема під питання не існує, а випадковий сам собою заходить.
        related = [point for point in points if (point.score - mu) / sd >= GATE]

        # Порядок уступок, від кращого до гіршого. Повтор влучного стоїть вище
        # за свіжий випадковий: на вузькій темі влучних буває два-три, вони
        # вичерпуються за кілька питань, і далі випадковий мем виглядає так,
        # ніби бот не зрозумів питання.
        for pool in (
            [point for point in related if point.id not in seen],
            related,
            [point for point in points if point.id not in seen],
            points,
        ):
            if pool:
                chosen = random.choice(pool)
                break
        seen.append(chosen.id)
        payload = chosen.payload or {}
        logger.info(
            "мем %s (%s, z=%.2f): %s",
            chosen.id,
            f"влучний з {len(related)}"
            if (chosen.score - mu) / sd >= GATE
            else "випадковий",
            (chosen.score - mu) / sd,
            payload.get("joke", "")[:80],
        )
        return {
            "url": presigned_get(f"memes/{chosen.id}.jpg", expires=URL_TTL),
            "caption": payload.get("caption", ""),
            "link": payload.get("link", ""),
        }
