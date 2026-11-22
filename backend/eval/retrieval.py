"""Регресійна оцінка пошуку: питання → відомий правильний документ → hit@k і MRR.

Міряє лише `QdrantRepository.search`, тобто те, що виконує інструмент
`search_knowledge_base`. Відповідь моделі тут не оцінюється.

Потрібні Qdrant із проіндексованою базою та GEMINI_API_KEY: кожне питання
перетворюється на ембединг, це витрачає квоту Gemini API. Запуск із `backend/`:

    uv run --env-file ../.env python eval/retrieval.py
    uv run --env-file ../.env python eval/retrieval.py --cases eval/cases.json --cases extra.json
    uv run --env-file ../.env python eval/retrieval.py --show-misses --save out.json

Формат набору (`eval/cases.json`): `category`, `question`, `terms` (власні назви,
які бот передав би інструменту) і `gold` — список однаково правильних документів.
Документ задано точним заголовком `title` і, за потреби, підрядком шляху `path`:
на відміну від id, вони переживають переіндексацію.

Пошук повертає фрагменти, а питання в тому, чи знайдено документ. Тому документ
рахується один раз, на найвищому своєму фрагменті, і ранг — це позиція серед
унікальних документів.
"""

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llama_index.embeddings.google_genai import GoogleGenAIEmbedding

from src.config import settings
from src.vectorstore.qdrant import QdrantRepository

KS = (1, 3, 5, 10)
DEFAULT_CASES = Path(__file__).with_name("cases.json")


def load_cases(paths: list[Path]) -> list[dict]:
    cases = []
    for path in paths:
        cases += json.loads(path.read_text(encoding="utf-8"))
    return cases


def matches(title: str, path: str, gold: dict) -> bool:
    return title.strip() == gold["title"] and gold.get("path", "") in path


async def validate_gold(repo: QdrantRepository, cases: list[dict]) -> None:
    """Кожен еталон мусить бути в індексі, інакше промах був би опискою в наборі."""
    present: set[tuple[str, str]] = set()
    offset = None
    while True:
        records, offset = await repo.qdrant_client.scroll(
            repo.alias,
            limit=500,
            offset=offset,
            with_payload=["title", "path"],
            with_vectors=False,
        )
        present |= {
            (r.payload["title"].strip(), r.payload.get("path", "")) for r in records
        }
        if offset is None:
            break
    missing = [
        gold
        for case in cases
        for gold in case["gold"]
        if not any(matches(title, path, gold) for title, path in present)
    ]
    if missing:
        raise SystemExit(f"Документів із набору немає в індексі: {missing}")


def ranked_docs(hits) -> list[tuple[str, str, str, float]]:
    """Унікальні документи в порядку першої появи: (source_id, title, path, score)."""
    seen, docs = set(), []
    for hit in hits:
        if hit.chunk.source_id in seen:
            continue
        seen.add(hit.chunk.source_id)
        docs.append(
            (hit.chunk.source_id, hit.chunk.title.strip(), hit.chunk.path, hit.score)
        )
    return docs


async def run(
    repo: QdrantRepository, cases: list[dict], use_terms: bool, limit: int
) -> list[dict]:
    rows = []
    for case in cases:
        hits = await repo.search(
            case["question"],
            ["general"],
            terms=case["terms"] if use_terms else None,
            limit=limit,
        )
        docs = ranked_docs(hits)
        rank = next(
            (
                i
                for i, (_, title, path, _) in enumerate(docs, 1)
                if any(matches(title, path, gold) for gold in case["gold"])
            ),
            None,
        )
        rows.append(
            {
                "category": case["category"],
                "question": case["question"],
                "terms": case["terms"],
                "rank": rank,
                "n_docs": len(docs),
                "top": [(d[1][:50], d[2][-40:], round(d[3], 3)) for d in docs[:5]],
            }
        )
    return rows


def report(rows: list[dict], label: str, show_misses: bool) -> None:
    def stats(group):
        n = len(group)
        hit = {k: sum(1 for r in group if r["rank"] and r["rank"] <= k) / n for k in KS}
        mrr = sum(1 / r["rank"] for r in group if r["rank"]) / n
        return n, hit, mrr

    print(f"\n=== {label} ===")
    print(
        f"{'category':<8} {'n':>3}  " + "  ".join(f"hit@{k:<2}" for k in KS) + "   MRR"
    )
    by_category = defaultdict(list)
    for row in rows:
        by_category[row["category"]].append(row)
    for category, group in [*sorted(by_category.items()), ("ALL", rows)]:
        n, hit, mrr = stats(group)
        print(
            f"{category:<8} {n:>3}  "
            + "  ".join(f"{hit[k]:>6.2f}" for k in KS)
            + f"  {mrr:.3f}"
        )
    found = sum(1 for r in rows if r["rank"])
    print(f"\nfound in results: {found} of {len(rows)}")
    missed = [r for r in rows if not r["rank"] or r["rank"] > 5]
    print(f"outside top 5: {len(missed)} of {len(rows)}")
    for row in missed:
        print(f"  [{row['category']}] rank={row['rank']}  {row['question'][:80]}")
        if show_misses:
            for top in row["top"]:
                print(f"       -> {top}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases",
        type=Path,
        action="append",
        help="JSON-набір питань; можна кілька, типово eval/cases.json",
    )
    parser.add_argument(
        "--limit", type=int, default=25, help="кількість фрагментів, як у боті"
    )
    parser.add_argument(
        "--save", type=Path, help="зберегти рядки для порівняння прогонів"
    )
    parser.add_argument("--show-misses", action="store_true")
    args = parser.parse_args()

    cases = load_cases(args.cases or [DEFAULT_CASES])
    embedding = GoogleGenAIEmbedding(
        model_name=settings.GEMINI_EMBEDDING_MODEL,
        api_key=settings.GEMINI_API_KEY,
        embed_batch_size=10,
    )
    repo = QdrantRepository(
        url=settings.QDRANT_URL,
        collection_name=settings.QDRANT_COLLECTION_NAME,
        embedding_model=embedding,
    )
    await validate_gold(repo, cases)

    vector = await run(repo, cases, use_terms=False, limit=args.limit)
    report(vector, "vector only", args.show_misses)
    hybrid = await run(repo, cases, use_terms=True, limit=args.limit)
    report(hybrid, "vector + terms (as the bot calls it)", args.show_misses)
    if args.save:
        args.save.write_text(
            json.dumps(
                {"vector": vector, "hybrid": hybrid}, ensure_ascii=False, indent=1
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    asyncio.run(main())
