from sqlalchemy import select


# async def upsert_user(db, telegram_id, username, first_name) -> User:
#     result = await db.execute(select(User).where(User.telegram_id == telegram_id))
#     user = result.scalar_one_or_none()
#     if user is None:
#         user = User(telegram_id=telegram_id, username=username, first_name=first_name)
#         db.add(user)
#         await db.commit()
#     return user
