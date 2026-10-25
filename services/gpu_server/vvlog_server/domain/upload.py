"""Chunked, resumable upload (contracts §2.1, ADR-0012 §4). No I/O here."""
from __future__ import annotations

from dataclasses import dataclass, field

from .errors import VvlogError
from .job import new_id, now_iso

CHUNK_SIZE = 8 * 1024 * 1024
MAX_SOURCE_BYTES = 4 * 1024 ** 3


@dataclass
class Upload:
    uploadId: str
    projectId: str
    kind: str
    filename: str
    size: int
    sha256: str
    chunkSize: int = CHUNK_SIZE
    receivedChunks: list[int] = field(default_factory=list)
    createdAt: str = field(default_factory=now_iso)
    mediaId: str | None = None

    @classmethod
    def create(cls, project_id: str, kind: str, filename: str, size: int, sha256: str,
               limit: int = MAX_SOURCE_BYTES) -> "Upload":
        if kind != "source":
            raise VvlogError("VALIDATION_FAILED", "kind は source のみです", {"kind": kind})
        if size <= 0 or size > limit:
            raise VvlogError("MEDIA_TOO_LARGE", "ファイルが大きすぎます", {"size": size, "limit": limit})
        if len(sha256) != 64:
            raise VvlogError("VALIDATION_FAILED", "sha256 は 16 進 64 桁です", {"sha256": sha256})
        return cls(uploadId=new_id("upl"), projectId=project_id, kind=kind, filename=filename, size=size,
                   sha256=sha256.lower())

    @property
    def chunk_count(self) -> int:
        return (self.size + self.chunkSize - 1) // self.chunkSize

    def expected_length(self, index: int) -> int:
        if not 0 <= index < self.chunk_count:
            raise VvlogError("VALIDATION_FAILED", "断片の番号が範囲外です", {"index": index, "count": self.chunk_count})
        return min(self.chunkSize, self.size - index * self.chunkSize)

    def receive(self, index: int, length: int) -> None:
        want = self.expected_length(index)
        if length != want:
            raise VvlogError("VALIDATION_FAILED", "断片の長さが違います", {"index": index, "length": length,
                                                                            "expected": want})
        if index not in self.receivedChunks:
            self.receivedChunks = sorted(self.receivedChunks + [index])

    def missing(self) -> list[int]:
        got = set(self.receivedChunks)
        return [i for i in range(self.chunk_count) if i not in got]

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}
