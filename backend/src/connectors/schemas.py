from pydantic import BaseModel


class NotionPage(BaseModel):
    id: str
    title: str
    url: str
    last_edited: str
    path: str = ""
    content: str | None = None


# class Member(BaseModel):
#     id: str
#     first_name: str
#     last_name: str
#     phone_number: str
