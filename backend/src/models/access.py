from datetime import datetime

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class Drive(Base):
    """Спільний диск і те, кому Google видав до нього доступ.

    Права читаються з Drive API (`POST /admin/drives/refresh`), а не пишуться
    руками: назви груп не збігаються з дисками (`DAT` ← `drive-best-lviv-mw@`).
    Таблиця, а не кеш у памʼяті, бо MCP-процес Drive API не має.
    """

    __tablename__ = "drives"

    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    # Пошти груп і окремих людей із прав диска, у нижньому регістрі.
    principals: Mapped[list[str]] = mapped_column(ARRAY(String))
    refreshed_at: Mapped[datetime] = mapped_column(server_default=func.now())


class GroupMember(Base):
    """Рядок експорту Google-групи. Імпорт замінює групу цілком.

    Google не дає читати членство споживчих груп через API, тому джерело —
    експорт CSV, який власник періодично кидає в бота.
    """

    __tablename__ = "group_members"

    group_email: Mapped[str] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(primary_key=True, index=True)
    role: Mapped[str]  # owner / manager / member
    imported_at: Mapped[datetime] = mapped_column(server_default=func.now())


class DriveOptOut(Base):
    """Диск, який людина вимкнула собі з пошуку. Доступні диски ввімкнені за
    замовчуванням (рішення автора 08.11), тож зберігається виняток, а не вибір.

    Не доступ: доступ рахується щоразу з прав і експортів, тож людина, яку
    прибрали з групи, втрачає диск незалежно від цієї таблиці.
    """

    __tablename__ = "drive_opt_outs"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    drive_id: Mapped[str] = mapped_column(primary_key=True)
