"""削除・開始・公開操作で受け付ける ``Idempotency-Key`` ヘッダー（docs/07-api-design.md）。

ここでは形式の検証だけを行う。キーと結果の保存・再送時の同一応答は、
キーを使う最初の書込 API（AI作成・削除要求など）の Issue でテーブルと合わせて実装する。
"""

import re
from typing import Annotated

from fastapi import Header

from alter_api.errors import ApiError

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
# 印字可能な ASCII 8〜255 文字（UUID を推奨）
_KEY_PATTERN = re.compile(r"^[\x21-\x7e]{8,255}$")


def idempotency_key(
    key: Annotated[
        str | None,
        Header(
            alias=IDEMPOTENCY_KEY_HEADER,
            description="同じ操作の再送を識別するキー。印字可能な ASCII 8〜255 文字（UUID 推奨）",
        ),
    ] = None,
) -> str | None:
    if key is None:
        return None
    if not _KEY_PATTERN.fullmatch(key):
        raise ApiError(422, "VALIDATION_ERROR", "Idempotency-Key must be 8-255 printable ASCII")
    return key
