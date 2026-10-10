"""Зведення для власника: скільком бот помагає, як швидко і що оцінили.

Запити тут навмисно сирим SQL: це аналітика з віконними й ordered-set
функціями (`lag`, `percentile_cont`), і на ORM вона читалася б утричі довше,
ніж сама ідея.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Скільки останніх оцінених відповідей показувати.
RATED_LIMIT = 6

SUMMARY = text("""
with bounds as (
    -- Вікно рухоме, від «зараз». А «сьогодні» — київське, не UTC: база пише
    -- час у UTC, тож із півночі до третьої ночі «сьогодні» по UTC означало б
    -- учорашній день.
    select now() - make_interval(days => :days) as since,
           date_trunc('day', now() at time zone 'Europe/Kyiv')
               at time zone 'Europe/Kyiv' as today
),
paired as (
    select m.role, m.created_at, m.rating,
           -- Тривалість генерації: питання штампується на початку запиту,
           -- відповідь — у кінці, тож різниця пари і є часом відповіді.
           extract(epoch from m.created_at
                   - lag(m.created_at) over (partition by m.session_id
                                             order by m.id)) as secs
    from messages m
)
select
    (select count(*) from users) as users,
    (select count(distinct s.user_id)
       from chat_sessions s
       join messages m on m.session_id = s.id
      where m.role = 'user'
        and m.created_at >= (select since from bounds)) as users_active,
    count(*) filter (where role = 'user') as questions,
    count(*) filter (where role = 'user'
                       and created_at >= (select since from bounds))
        as questions_window,
    count(*) filter (where role = 'user'
                       and created_at >= (select today from bounds))
        as questions_today,
    count(*) filter (where rating = 1) as likes,
    count(*) filter (where rating = -1) as dislikes,
    percentile_cont(0.5) within group (order by secs)
        filter (where role = 'assistant'
                  and created_at >= (select since from bounds))
        as median_secs
from paired
""")

# Питання до оціненої відповіді — найближче попереднє повідомлення юзера в тій
# самій сесії. Саме воно й цікаве: без нього «👎» нічого не каже.
RATED = text("""
select a.rating, a.created_at, a.content as answer,
       u.telegram_username,
       (select q.content from messages q
         where q.session_id = a.session_id
           and q.role = 'user'
           and q.id < a.id
         order by q.id desc
         limit 1) as question
from messages a
join chat_sessions s on s.id = a.session_id
join users u on u.id = s.user_id
where a.rating is not null
order by a.id desc
limit :limit
""")


async def overview(db: AsyncSession, days: int = 7) -> dict:
    summary = (await db.execute(SUMMARY, {"days": days})).mappings().one()
    rated = (await db.execute(RATED, {"limit": RATED_LIMIT})).mappings().all()
    return {
        "days": days,
        **dict(summary),
        "rated": [dict(row) for row in rated],
    }
