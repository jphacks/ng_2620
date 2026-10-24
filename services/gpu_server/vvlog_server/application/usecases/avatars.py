"""VRM registration (F07): the id is the sha256 prefix, so the same file registers once."""
from __future__ import annotations

from ...domain.errors import VvlogError
from ..deps import Deps

MAX_VRM_BYTES = 256 * 1024 * 1024


def upload_avatar(d: Deps, data: bytes, filename: str) -> dict:
    if not data or len(data) > MAX_VRM_BYTES:
        raise VvlogError("AVATAR_INVALID", "VRM ファイルの大きさが不正です", {"size": len(data)})
    avatar_id, meta = d.avatars.put(data, filename)
    return {"avatarId": avatar_id, "meta": meta}


def get_avatar(d: Deps, avatar_id: str) -> dict:
    _, meta = d.avatars.get(avatar_id)
    return {"avatarId": avatar_id, "meta": meta}
