"""一覧 API の共通ページング（docs/07-api-design.md）。

``limit`` は 1〜50（既定 20）、``cursor`` は不透明な文字列。応答は ``{items, next_cursor}``。
cursor の中身（並び順キーなど）は各一覧の実装が決め、クライアントは解釈しない。
"""

from typing import Annotated

from fastapi import Query
from pydantic import BaseModel

DEFAULT_LIMIT = 20
MAX_LIMIT = 50
MAX_CURSOR_LENGTH = 512


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


class PageParams(BaseModel):
    limit: int
    cursor: str | None


def page_params(
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(min_length=1, max_length=MAX_CURSOR_LENGTH)] = None,
) -> PageParams:
    return PageParams(limit=limit, cursor=cursor)
