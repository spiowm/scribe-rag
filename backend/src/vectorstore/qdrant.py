import asyncio
import logging
import re
from datetime import UTC, datetime

from llama_index.core.schema import MetadataMode, TextNode
from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    CreateAlias,
    CreateAliasOperation,
    DeleteAlias,
    DeleteAliasOperation,
    Distance,
    FieldCondition,
    Filter,
    IsEmptyCondition,
    KeywordIndexParams,
    KeywordIndexType,
    MatchAny,
    MatchText,
    MatchValue,
    PayloadField,
    PointStruct,
    SearchParams,
    TextIndexParams,
    TextIndexType,
    TokenizerType,
    VectorParams,
)

from src.ingestion.schemas import GENERAL, ChunkPayload, SearchHit
from src.text import APOSTROPHES

logger = logging.getLogger(__name__)

# Токенізатор WORD вважає `ʼ` (U+02BC) літерою, а решту апострофів — пробілом:
# `ІЯКʼ17` одне слово, `ІЯК’17` два, `ІЯК17` знову одне. У документах трапляються
# усі три (ІЯК: 24 склеєних, 20 з `ʼ`, 60 з іншими апострофами), тож фільтр за
# однією формою губить решту.
_IN_WORD_APOSTROPHE = re.compile(rf"(?<=\w)[{APOSTROPHES}](?=\w)")
# Літери й рівно дві цифри в кінці: склеєна хвиля чи рік (`ІЯК17`, `HACK10`).
_GLUED_YEAR = re.compile(r"^(.*[^\W\d_])(\d{2})$")


def term_forms(term: str) -> list[str]:
    """Написання назви, під якими вона може бути в тексті."""
    if _IN_WORD_APOSTROPHE.search(term):
        parts = _IN_WORD_APOSTROPHE.split(term)
    elif match := _GLUED_YEAR.match(term):
        parts = list(match.groups())
    else:
        return [term]
    forms = ["’".join(parts), "ʼ".join(parts), "".join(parts)]
    return list(dict.fromkeys(forms))


class QdrantRepository:
    # Надбавка до скору загального (Notion і загальний диск), коли в пошуку є
    # і робочі диски: інакше списки й чернетки подій витісняють хендбуки з
    # питань, що до події не стосуються. Заміряно з усім диском BTW, разом із
    # таблицями (08.11): у питаннях не про події фрагментів диска з 25 без
    # надбавки 6.5, з 0.01 — 3.5, з 0.02 — 1.8, і топ-5 без диска зберігається
    # повністю; еталон питань про випуски BTW той самий за всіх трьох (31/32 у
    # топ-25, 29/32 у топ-5). У пілоті 0.04 уже ламав пошук по випусках.
    # Перезаміряти з кожним новим диском.
    GENERAL_BONUS = 0.02
    # У скільки разів більше кандидатів брати, щоб надбавці було з чого вибирати.
    BONUS_POOL = 3
    # Точний перебір замість HNSW: на 7 тис. точок той самий час (медіана 17 мс
    # з дисками), а HNSW з типовим ef міняв топ-5 в 11 із 201 справжнього
    # питання, і щоразу по-іншому після reindex — заміри бонусу між прогонами
    # тоді не порівнянні (`research/qdrant-exact/`). Переглянути від ~100 тис. точок.
    EXACT = SearchParams(exact=True)

    def __init__(
        self,
        url: str,
        collection_name: str,
        embedding_model: GoogleGenAIEmbedding,
    ) -> None:
        self.alias = collection_name
        self.qdrant_client = AsyncQdrantClient(url=url)
        self.embedding_model = embedding_model

    async def search(
        self,
        query: str,
        scopes: list[str],
        terms: list[str] | None = None,
        limit: int = 25,
    ) -> list[SearchHit]:
        """Векторний пошук плюс окремий запит на кожну власну назву.

        Назви вектор знаходить погано: вони самі нічого не означають.
        Тому на кожну робимо той самий векторний запит, але з фільтром
        «фрагмент містить це слово» — там кандидатів одиниці, і потрібний
        виринає. Окремий запит на назву, а не один спільний фільтр: спільний
        фільтр пропускає обʼєднання, і часта назва знову витісняє рідку.

        Скори порівнянні, бо вектор один і той самий — фільтр змінює не
        оцінювання, а те, які фрагменти до нього допускаються. Тому злиття
        це просте сортування, без RRF.

        `scopes` — що цьому користувачу можна бачити. Без значення за
        замовчуванням навмисно: забутий аргумент має падати, а не відкривати.
        """
        allowed = self._scope_filter(scopes)
        bonus = self.GENERAL_BONUS
        # лише загальне — порівнювати нема з чим, і поведінка як до дисків
        mixed = bonus > 0 and any(s != GENERAL for s in scopes)

        def ranked(point) -> float:
            general = (point.payload or {}).get("scope") == GENERAL
            return point.score + (bonus if mixed and general else 0.0)

        query_vector = await self.embedding_model.aget_query_embedding(query)

        results = await self.qdrant_client.query_points(
            collection_name=self.alias,
            query=query_vector,
            limit=limit * self.BONUS_POOL if mixed else limit,
            query_filter=Filter(must=[allowed]),
            search_params=self.EXACT,
        )
        points = sorted(results.points, key=ranked, reverse=True)[:limit]
        seen = {point.id for point in points}

        for term in await self._rare_terms(terms or [], limit * 4, allowed):
            filtered = await self.qdrant_client.query_points(
                collection_name=self.alias,
                query=query_vector,
                limit=limit,
                query_filter=Filter(must=[allowed, self._term_filter(term)]),
                search_params=self.EXACT,
            )
            for point in filtered.points:
                if point.id not in seen:
                    seen.add(point.id)
                    points.append(point)

        points.sort(key=ranked, reverse=True)
        return [
            SearchHit(
                score=point.score, chunk=ChunkPayload.model_validate(point.payload)
            )
            for point in points
            if point.payload is not None
        ]

    @staticmethod
    def _term_filter(term: str) -> Filter:
        """Фрагмент містить назву в будь-якому з написань (`term_forms`)."""
        return Filter(
            should=[
                FieldCondition(key="text", match=MatchText(text=form))
                for form in term_forms(term)
            ]
        )

    @staticmethod
    def _scope_filter(scopes: list[str]) -> FieldCondition:
        if not scopes:
            raise ValueError("порожній scopes: пошук не має що дозволити")
        return FieldCondition(key="scope", match=MatchAny(any=list(scopes)))

    async def ensure_collection(self) -> None:
        if not await self.qdrant_client.collection_exists(self.alias):
            collection_name = await self._create_collection()
            await self._switch_alias(collection_name)
        await self._backfill_scope()

    async def _backfill_scope(self) -> None:
        """Точки з часів до доступів — без `scope`. Усе тоді було загальним.

        Фільтр за `scope` таку точку просто не пропустить, тож без цього
        після деплою зник би весь індекс. Ембединг не чіпається — лише payload.
        """
        current = await self._current_collection()
        if current is None:
            return
        info = await self.qdrant_client.get_collection(current)
        schema = info.payload_schema or {}
        if "scope" not in schema:
            await self._index_scope(current)
        text_index = schema.get("text")
        tokenizer = (
            getattr(text_index.params, "tokenizer", None) if text_index else None
        )
        if tokenizer != self.TEXT_INDEX.tokenizer:
            # payload-індекс перебудовується з тих самих точок, вектори не чіпаються
            if text_index:
                await self.qdrant_client.delete_payload_index(current, "text")
            await self._index_text(current)
            logger.info("text-індекс перебудовано: %s -> word", tokenizer)
        missing = Filter(must=[IsEmptyCondition(is_empty=PayloadField(key="scope"))])
        count = (await self.qdrant_client.count(current, count_filter=missing)).count
        if count:
            await self.qdrant_client.set_payload(
                collection_name=current,
                payload={"scope": GENERAL},
                points=missing,
                wait=True,
            )
            logger.info("scope=%s дописано %d точкам", GENERAL, count)

    async def _index_scope(self, collection_name: str) -> None:
        await self.qdrant_client.create_payload_index(
            collection_name=collection_name,
            field_name="scope",
            field_schema=KeywordIndexParams(
                type=KeywordIndexType.KEYWORD, is_tenant=True
            ),
        )

    async def index_state(self, source: str) -> dict[str, tuple[str, str, str]]:
        """{source_id: (last_edited, scope, path)} — що зараз у живій колекції."""
        current = await self._current_collection()
        if not current:
            return {}

        state: dict[str, tuple[str, str, str]] = {}
        offset = None
        batch_size = 500

        scroll_filter = Filter(
            must=[
                FieldCondition(
                    key="source",
                    match=MatchValue(value=source),
                )
            ]
        )

        while True:
            records, next_offset = await self.qdrant_client.scroll(
                collection_name=current,
                offset=offset,
                limit=batch_size,
                scroll_filter=scroll_filter,
                with_payload=["source_id", "last_edited", "scope", "path"],
                with_vectors=False,
            )

            for r in records:
                if r.payload:
                    source_id = r.payload.get("source_id")
                    last_edited = r.payload.get("last_edited")
                    if source_id and last_edited:
                        scope = r.payload.get("scope", GENERAL)
                        path = r.payload.get("path", "")
                        state[source_id] = (last_edited, scope, path)

            if next_offset is None:
                break
            offset = next_offset

        return state

    async def reindex(self, fresh_nodes: list[TextNode], stale_ids: set[str]) -> int:
        current = await self._current_collection()
        new = await self._create_collection()
        copied = await self._copy_points(current, new, stale_ids) if current else 0
        added = await self._upsert_nodes(new, fresh_nodes)
        total = copied + added

        if current:
            before = (await self.qdrant_client.get_collection(current)).points_count
            if before and total < before * 0.5:
                await self.qdrant_client.delete_collection(new)
                raise RuntimeError(
                    f"нова колекція має {total} точок проти {before} — аліас не перемкнено"
                )

        await self._switch_alias(new)
        try:
            await self._cleanup_old_collections()
        except Exception:
            logger.exception("не вдалось прибрати старі колекції")
        return copied + added

    async def _create_collection(self) -> str:
        # мілісекунди: дві колекції в ту саму секунду (створення на старті й
        # одразу синк) інакше отримували одне ім'я, і друга падала з 409
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")[:-3]
        name = f"{self.alias}_{stamp}"
        await self.qdrant_client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(
                size=3072,
                distance=Distance.COSINE,
            ),
        )
        await self._index_text(name)
        await self._index_scope(name)
        return name

    # `word`, а не `multilingual`: той викидав окремі числа, тож `CTF'24`
    # фільтрував по всьому CTF, а `2019` не знаходив нічого (F9, пілот 12.10).
    TEXT_INDEX = TextIndexParams(
        type=TextIndexType.TEXT,
        tokenizer=TokenizerType.WORD,
        min_token_len=2,
        max_token_len=20,
        lowercase=True,
    )

    async def _index_text(self, collection_name: str) -> None:
        await self.qdrant_client.create_payload_index(
            collection_name=collection_name,
            field_name="text",
            field_schema=self.TEXT_INDEX,
        )

    async def _current_collection(self) -> str | None:
        """Колекція, на яку зараз вказує аліас."""
        aliases = await self.qdrant_client.get_aliases()
        for a in aliases.aliases:
            if a.alias_name == self.alias:
                return a.collection_name
        return None

    async def _switch_alias(self, collection_name: str) -> None:
        await self.qdrant_client.update_collection_aliases(
            change_aliases_operations=[
                DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=self.alias)),
                CreateAliasOperation(
                    create_alias=CreateAlias(
                        collection_name=collection_name, alias_name=self.alias
                    )
                ),
            ]
        )

    async def _copy_points(self, src: str, dst: str, stale_ids: set[str]) -> int:
        """Копіює точки з однієї колекції в іншу, крім документів зі stale_ids."""
        total_copied = 0
        offset = None
        batch_size = 128  # такий самий розмір батчу, як і в _upsert_nodes

        while True:
            records, next_offset = await self.qdrant_client.scroll(
                collection_name=src,
                offset=offset,
                limit=batch_size,
                with_payload=True,
                with_vectors=True,
            )

            # Відкидаємо точки, чий документ застарів чи оновлюється
            valid_points = [
                PointStruct(id=r.id, vector=r.vector, payload=r.payload)
                for r in records
                if r.payload and r.payload.get("source_id") not in stale_ids
            ]

            if valid_points:
                await self.qdrant_client.upsert(
                    collection_name=dst,
                    points=valid_points,
                )
                total_copied += len(valid_points)

            if next_offset is None:
                break
            offset = next_offset

        return total_copied

    async def _upsert_nodes(self, collection_name: str, nodes: list[TextNode]) -> int:
        if not nodes:
            return 0

        points: list[PointStruct] = []

        texts = [node.get_content(metadata_mode=MetadataMode.EMBED) for node in nodes]
        vectors = await self.embedding_model.aget_text_embedding_batch(texts)

        for i, node in enumerate(nodes):
            payload = ChunkPayload(
                text=node.text,
                **node.metadata,
            )
            points.append(
                PointStruct(
                    id=node.node_id,
                    vector=vectors[i],
                    payload=payload.model_dump(),
                )
            )

        batch_size = 128
        for i in range(0, len(points), batch_size):
            await self.qdrant_client.upsert(
                collection_name=collection_name,
                points=points[i : i + batch_size],
            )

        return len(points)

    async def _rare_terms(
        self, terms: list[str], max_df: int, allowed: FieldCondition
    ) -> list[str]:
        """Лишає до двох найрідкісніших назв із тих, що взагалі є в базі.

        Поріг не про якість — окремий запит на кожну назву й так не дає
        частій витіснити рідку. Він про те, чи є сенс платити за назву
        місцем у контексті. Запит із фільтром повертає щонайбільше limit
        із df тих чанків, що містять слово, тобто показує min(df, limit) / df
        від усіх згадок. При max_df = 4 * limit це щонайменше чверть — ще
        пошук. За df 324 («Notion») це 8%, тобто вже просто перетасовка
        того, що й так знайшлося без фільтра.

        Нуль відсікаємо з іншої причини: якщо модель дала форму, якої в базі
        немає, запит із таким фільтром вернув би порожнечу.

        Рахується лише в дозволеному: інакше частота слова в закритих
        дисках і впливала б на пошук, і видавала б, що таке слово там є.
        """
        if not terms:
            return []

        async def _count(term: str) -> tuple[str, int]:
            res = await self.qdrant_client.count(
                collection_name=self.alias,
                count_filter=Filter(must=[allowed, self._term_filter(term)]),
            )
            return term, res.count

        counts = await asyncio.gather(*(_count(t) for t in dict.fromkeys(terms)))
        usable = [(term, cnt) for term, cnt in counts if 0 < cnt <= max_df]
        usable.sort(key=lambda pair: pair[1])
        return [term for term, _ in usable[:2]]

    async def _cleanup_old_collections(self) -> int:
        """Видаляє старі колекції цього аліасу, крім поточної.

        Лише `<аліас>_<час>` з `_create_collection`: за самим префіксом
        `general_` прибирання `general` знесло б і `general_ctx_<час>` —
        колекцію іншого аліасу поруч.
        """
        current = await self._current_collection()

        if current is None:
            return 0
        own = re.compile(rf"{re.escape(self.alias)}_\d{{8}}_\d{{6}}(?:_\d{{3}})?")
        cols = await self.qdrant_client.get_collections()
        collections_names = [
            col.name for col in cols.collections if own.fullmatch(col.name)
        ]

        deleted_count = 0
        for collection_name in collections_names:
            if collection_name != current:
                await self.qdrant_client.delete_collection(
                    collection_name=collection_name
                )
                deleted_count += 1

        return deleted_count
