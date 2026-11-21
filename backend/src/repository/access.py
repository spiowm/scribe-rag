"""Хто які диски бачить: права дисків із Drive API × експорти Google-груп."""

import csv
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.connectors.mongo import MongoConnector
from src.ingestion.schemas import GENERAL
from src.models.access import Drive, DriveOptOut, GroupMember
from src.models.user import User

# Запрошений (`invited`) ще не прийняв запрошення — доступу до диска в нього
# немає. Тому перелік дозволених статусів, а не заборонених: невідомий статус
# доступу не дає.
MEMBER_STATUSES = {"owner", "manager", "member"}
HEADER_PREFIX = "Members for group "
# Позначка в записі довідника, яку ставить `caller`: адміну бота відкрито всі
# диски з таблиці `drives`, хоч би які групи в нього були.
BOT_ADMIN = "bot_admin"

# Група повних членів може читатися за статусом довідника.
full_members_group = settings.GOOGLE_DRIVE_FULL_MEMBERS_GROUP
STATUS_GROUPS = {full_members_group: ("Full", "Alumni")} if full_members_group else {}
# Адмінська група не показується як спосіб отримати доступ.
ADMIN_GROUPS = {settings.GOOGLE_DRIVE_ADMIN_GROUP} - {""}


def email_key(email: str) -> str:
    """Пошта для зіставлення експорту групи з довідником.

    Gmail не зважає на крапки й `+…` у локальній частині: `john.doe+best@gmail.com`
    в експорті й `johndoe@gmail.com` у довіднику — один акаунт.
    """
    local, at, domain = email.strip().lower().partition("@")
    if domain == "gmail.com":
        local = local.split("+", 1)[0].replace(".", "")
    return local + at + domain


@dataclass
class Export:
    group: str  # як група названа в першому рядку файлу; може бути порожнім
    members: dict[str, str] = field(default_factory=dict)  # email_key -> роль
    skipped: dict[str, int] = field(default_factory=dict)  # статус -> скільки


def parse_export(content: str) -> Export:
    """Розбирає CSV експорту учасників Google Groups.

    Формат: `Members for group <назва>`, далі шапка з `Email address` і
    `Group status`. Колонки шукаються за назвою, не за позицією.
    """
    lines = content.lstrip("﻿").splitlines()
    group = ""
    if lines and lines[0].startswith(HEADER_PREFIX):
        group = lines[0][len(HEADER_PREFIX) :].strip().strip(",")
        lines = lines[1:]
    rows = list(csv.DictReader(lines))
    if not rows or "Email address" not in rows[0] or "Group status" not in rows[0]:
        raise ValueError("немає колонок «Email address» і «Group status»")

    export = Export(group=group)
    for row in rows:
        email = (row.get("Email address") or "").strip()
        status = (row.get("Group status") or "").strip().lower()
        if not email:
            continue
        if status in MEMBER_STATUSES:
            export.members[email_key(email)] = status
        else:
            export.skipped[status] = export.skipped.get(status, 0) + 1
    return export


def resolve_group(name: str, groups: set[str]) -> str | None:
    """Адреса групи з прав дисків, якій відповідає назва з файлу або підпису.

    Повна адреса — як є; голе ім'я — за локальною частиною. Лише точний збіг:
    група, якої нема в правах жодного диска, доступу й так не дає.
    """
    name = name.strip().lower()
    if not name:
        return None
    if "@" in name:
        return name if name in groups else None
    matches = [g for g in groups if g.split("@")[0] == name]
    return matches[0] if len(matches) == 1 else None


async def known_groups(db: AsyncSession) -> set[str]:
    """Усі пошти з прав дисків. Груп від людей тут не відрізнити, але імпорт
    під пошту людини нічого не зламає: її рядки просто дублюють право."""
    principals = await db.scalars(
        select(func.unnest(Drive.principals)).where(Drive.id.not_in(settings.PAUSED_DRIVES))
    )
    return set(principals)


async def import_group(db: AsyncSession, group: str, members: dict[str, str]) -> None:
    """Замінює членство групи цілком: хто зник з експорту — втрачає доступ."""
    await db.execute(delete(GroupMember).where(GroupMember.group_email == group))
    if members:
        await db.execute(
            insert(GroupMember).values(
                [
                    {"group_email": group, "email": email, "role": role}
                    for email, role in members.items()
                ]
            )
        )
    await db.commit()


async def save_drives(db: AsyncSession, drives: list[dict]) -> None:
    """Оновлює права активних дисків, зберігаючи записи призупинених."""
    if drives:
        stmt = insert(Drive).values(drives)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Drive.id],
            set_={
                "name": stmt.excluded.name,
                "principals": stmt.excluded.principals,
                "refreshed_at": func.now(),
            },
        )
        await db.execute(stmt)
    known = {d["id"] for d in drives} | settings.PAUSED_DRIVES
    await db.execute(delete(Drive).where(Drive.id.not_in(known)))
    await db.commit()


def status_groups(member: dict) -> set[str]:
    return {
        g for g, statuses in STATUS_GROUPS.items() if member.get("status") in statuses
    }


async def member_principals(db: AsyncSession, member: dict | None) -> set[str]:
    """Пошти, від імені яких член отримує доступ: групи з експортів, групи,
    замінені статусом, і власна пошта."""
    if not member:
        return set()
    groups = status_groups(member)
    email = member.get("email")
    if isinstance(email, str) and email.strip():
        email = email.strip().lower()
        via = select(GroupMember.group_email).where(
            GroupMember.email == email_key(email)
        )
        groups |= set(await db.scalars(via))
        # Власна пошта — лише якщо це не адреса групи: інакше запис довідника
        # з поштою групи відкрив би її диски без жодного експорту.
        if not email.endswith("@googlegroups.com"):
            groups.add(email)
    return groups


async def access_via(db: AsyncSession, member: dict | None) -> dict[str, list[str]]:
    """Диск, до якого в члена є доступ → пошти з прав диска, що його дають:
    його пошта прямо в правах диска, або група з експорту, або група,
    замінена статусом. Адміну бота — усі диски з причиною `BOT_ADMIN`."""
    paused = settings.PAUSED_DRIVES
    if member and member.get(BOT_ADMIN):
        drives = await db.scalars(select(Drive.id))
        return {d: [BOT_ADMIN] for d in drives if d not in paused}
    groups = await member_principals(db, member)
    if not groups:
        return {}
    drives = await db.scalars(
        select(Drive).where(Drive.principals.overlap(list(groups)))
    )
    return {
        d.id: sorted(set(d.principals) & groups) for d in drives if d.id not in paused
    }


async def allowed_drives(db: AsyncSession, member: dict | None) -> set[str]:
    """Диски, до яких у члена є доступ."""
    return set(await access_via(db, member))


async def overview(db: AsyncSession) -> dict:
    """Для адмінки: диски, їхні групи й свіжість експортів."""
    paused = settings.PAUSED_DRIVES
    rows = await db.scalars(select(Drive).order_by(Drive.name))
    drives = [d for d in rows if d.id not in paused]
    principals = {p for d in drives for p in d.principals}
    exported = {
        group: (count, imported_at)
        for group, count, imported_at in await db.execute(
            select(
                GroupMember.group_email,
                func.count(),
                func.max(GroupMember.imported_at),
            ).group_by(GroupMember.group_email)
        )
    }
    return {
        "drives": [
            {"id": d.id, "name": d.name, "principals": d.principals} for d in drives
        ],
        "refreshed_at": max((d.refreshed_at for d in drives), default=None),
        "groups": {
            g: {"members": count, "imported_at": at}
            for g, (count, at) in exported.items()
            if not paused or g in principals
        },
        "status_groups": {
            g: sorted(s) for g, s in STATUS_GROUPS.items() if not paused or g in principals
        },
    }


async def opted_out(db: AsyncSession, user_id: int) -> set[str]:
    rows = await db.scalars(
        select(DriveOptOut.drive_id).where(DriveOptOut.user_id == user_id)
    )
    return set(rows)


async def search_scopes(
    db: AsyncSession, user_id: int, member: dict | None
) -> list[str]:
    """Що шукати для цього користувача: загальне + доступні диски, крім вимкнених."""
    allowed = await allowed_drives(db, member)
    return [GENERAL, *sorted(allowed - await opted_out(db, user_id))]


async def caller(
    db: AsyncSession, mongo: MongoConnector, user: User
) -> tuple[dict | None, list[str]]:
    """Запис довідника і що цьому користувачу можна шукати — для бота,
    `/drives` і MCP однаково.

    Пошта для доступів — з довідника за підтвердженим телефоном, а не від
    клієнта: бот знає людину лише за телефоном. Адміна бота позначено
    `BOT_ADMIN`, якщо він є в довіднику.
    """
    member = await mongo.find_member_by_phone(user.phone_number)
    admin = settings.ADMIN_TELEGRAM_ID
    if member is not None and admin is not None and user.telegram_id == admin:
        member = {**member, BOT_ADMIN: True}
    return member, await search_scopes(db, user.id, member)


def drive_label(name: str) -> str:
    """`BEST Lviv - BTW` → `BTW`: префікс однаковий у всіх дисків осередку."""
    return name.removeprefix("BEST Lviv - ")


def _reason(principal: str, member: dict) -> str:
    if principal == BOT_ADMIN:
        return "адмін бота"
    if principal in status_groups(member):
        return f"статус {member.get('status')}"
    if principal.endswith("@googlegroups.com"):
        return f"група {principal.split('@')[0]}"
    return "особиста пошта в правах диска"


@dataclass
class DriveFacts:
    """Диски очима однієї людини — для моделі й позначок біля джерел."""

    recognized: bool  # чи знайшовся запис довідника
    connected: dict[str, str]  # індексовані диски: id → коротка назва
    reasons: dict[str, list[str]]  # підключені диски з доступом: id → чому
    off: set[str]  # з доступних вимкнені в /drives

    @property
    def enabled(self) -> list[str]:
        return [d for d in self.connected if d in self.reasons and d not in self.off]


async def drive_facts(
    db: AsyncSession, user_id: int, member: dict | None, indexed: list[str]
) -> DriveFacts:
    """Ті самі доступ і вимкнення, що в `search_scopes`, плюс назви й причини."""
    via = await access_via(db, member)
    paused = settings.PAUSED_DRIVES
    active = [drive for drive in indexed if drive not in paused]
    rows = await db.execute(select(Drive.id, Drive.name).where(Drive.id.in_(active)))
    names = sorted(
        ((i, drive_label(n)) for i, n in rows.tuples()), key=lambda r: r[1].lower()
    )
    connected = dict(names)
    reasons = {
        d: list(dict.fromkeys(_reason(p, member or {}) for p in via[d]))
        for d in connected
        if d in via
    }
    off = (await opted_out(db, user_id)) & set(reasons) if reasons else set()
    return DriveFacts(member is not None, connected, reasons, off)


def _grants(principals: list[str]) -> list[str]:
    """Що відкрило б диск: статус, якщо в правах група статусу, інакше групи
    диска, крім спільної адмінської."""
    for group, statuses in STATUS_GROUPS.items():
        if group in principals:
            return ["статус " + " / ".join(statuses)]
    return [
        _reason(p, {})
        for p in sorted(principals)
        if p.endswith("@googlegroups.com") and p not in ADMIN_GROUPS
    ]


async def drive_view(
    db: AsyncSession, user_id: int, member: dict | None, indexed: list[str]
) -> dict:
    """Для `/drives`: що шукається завжди, підключені диски з доступом
    (увімкнені чи ні й чому) і без доступу (що його дало б)."""
    facts = await drive_facts(db, user_id, member, indexed)
    closed = [d for d in facts.connected if d not in facts.reasons]
    general = settings.GOOGLE_DRIVE_GENERAL_ID
    rows = await db.execute(
        select(Drive.id, Drive.name, Drive.principals).where(
            Drive.id.in_([*closed, general])
        )
    )
    found = {i: (name, principals) for i, name, principals in rows.tuples()}
    locked = ["Notion", "загальний диск"]
    if general in found:
        locked[1] += " " + drive_label(found[general][0])
    if settings.GENERAL_FOREIGN_DRIVES:
        locked.append(settings.GOOGLE_DRIVE_GENERAL_FOREIGN_LABEL)
    return {
        "recognized": facts.recognized,
        "locked": locked,
        "drives": [
            {
                "id": d,
                "name": facts.connected[d],
                "on": d not in facts.off,
                "via": facts.reasons[d],
            }
            for d in facts.connected
            if d in facts.reasons
        ],
        "no_access": [
            {"name": facts.connected[d], "how": _grants(found[d][1])} for d in closed
        ],
    }


async def toggle_drive(db: AsyncSession, user_id: int, drive_id: str) -> None:
    """Вимикає диск, якщо ввімкнений, і навпаки. Доступ перевіряє той, хто кличе."""
    row = await db.get(DriveOptOut, (user_id, drive_id))
    if row is None:
        db.add(DriveOptOut(user_id=user_id, drive_id=drive_id))
    else:
        await db.delete(row)
    await db.commit()
