"""Reads a VRM's meta (contracts §7) from its GLB JSON chunk, without a glTF library."""
from __future__ import annotations

import json
import struct

from ..domain.errors import VvlogError

_GLB_MAGIC, _JSON_CHUNK = 0x46546C67, 0x4E4F534A


def _invalid(reason: str) -> VvlogError:
    return VvlogError("AVATAR_INVALID", "VRM として読めません", {"reason": reason})


def read_meta(data: bytes) -> dict:
    if len(data) < 20:
        raise _invalid("too short")
    magic, version, _length = struct.unpack_from("<III", data, 0)
    if magic != _GLB_MAGIC or version != 2:
        raise _invalid("not a glTF 2.0 binary")
    clen, ctype = struct.unpack_from("<II", data, 12)
    if ctype != _JSON_CHUNK:
        raise _invalid("first chunk is not JSON")
    try:
        gltf = json.loads(data[20:20 + clen].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _invalid("broken JSON chunk")
    ext = gltf.get("extensions", {})
    if "VRMC_vrm" in ext:
        m = ext["VRMC_vrm"].get("meta", {})
        bones = ext["VRMC_vrm"].get("humanoid", {}).get("humanBones", {})
        names = set(bones)
        meta = {"name": m.get("name", ""), "authors": list(m.get("authors", [])), "version": "1.0",
                "license": {"url": m.get("licenseUrl"), "commercial": m.get("commercialUsage"),
                            "avatarPermission": m.get("avatarPermission"),
                            "redistribution": m.get("allowRedistribution")}}
    elif "VRM" in ext:
        m = ext["VRM"].get("meta", {})
        names = {b.get("bone") for b in ext["VRM"].get("humanoid", {}).get("humanBones", [])}
        meta = {"name": m.get("title", ""), "authors": [m["author"]] if m.get("author") else [], "version": "0.x",
                "license": {"name": m.get("licenseName"), "url": m.get("otherPermissionUrl"),
                            "commercial": m.get("commercialUssageName"),
                            "avatarPermission": m.get("allowedUserName")}}
    else:
        raise _invalid("no VRM / VRMC_vrm extension")
    required = {"hips", "spine", "head", "leftUpperArm", "rightUpperArm", "leftUpperLeg", "rightUpperLeg"}
    if not required <= names:
        raise VvlogError("AVATAR_INVALID", "VRM のボーンが足りません", {"missingBones": sorted(required - names)})
    meta["license"] = {k: v for k, v in meta["license"].items() if v is not None}
    return meta
