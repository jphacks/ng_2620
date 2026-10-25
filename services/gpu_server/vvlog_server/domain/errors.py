"""Error codes of the API (designdocs/architecture/contracts.md §2.3)."""

HTTP_STATUS = {
    "UNAUTHORIZED": 401,
    "NOT_FOUND": 404,
    "VALIDATION_FAILED": 422,
    "MEDIA_TOO_LONG": 422,
    "MEDIA_TOO_LARGE": 422,
    "MEDIA_UNSUPPORTED": 422,
    "UPLOAD_INCOMPLETE": 409,
    "CHECKSUM_MISMATCH": 409,
    "AVATAR_INVALID": 422,
    "DEPENDENCY_NOT_READY": 409,
    "NOT_IMPLEMENTED": 501,
    # local app server file API (/v1/local/fs, #23)
    "PATH_NOT_ALLOWED": 403,
    "FS_CONFLICT": 409,
    "FS_FAILED": 500,
    # job failures: reported through the job, not as an HTTP status
    "NO_PERSON_FOUND": 200,
    "GPU_OOM": 200,
    "PIPELINE_FAILED": 200,
    "INTERRUPTED": 200,
    "CANCELED": 200,
}


class VvlogError(Exception):
    def __init__(self, code: str, message: str, detail: dict | None = None):
        assert code in HTTP_STATUS, code
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.detail = code, message, detail or {}

    @property
    def http_status(self) -> int:
        return HTTP_STATUS[self.code]

    def to_dict(self) -> dict:
        d = {"code": self.code, "message": self.message}
        if self.detail:
            d["detail"] = self.detail
        return d


def not_found(what: str, ident: str) -> VvlogError:
    return VvlogError("NOT_FOUND", f"{what} が見つかりません", {what: ident})
