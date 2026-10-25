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
    "QUEUE_FULL": 429,            # VVLOG_MAX_QUEUED_JOBS reached
    # local app server file API (/v1/local/fs, #23)
    "PATH_NOT_ALLOWED": 403,
    "FS_CONFLICT": 409,
    "FS_FAILED": 500,
    "DIALOG_LOCAL_ONLY": 403,     # /v1/local/dialog: only the app served by this server may open a dialog
    "SHUTDOWN_LOCAL_ONLY": 403,   # POST /v1/local/shutdown: only the app served by this server may stop it
    "DIALOG_BUSY": 409,
    "DIALOG_UNAVAILABLE": 501,
    "DIALOG_FAILED": 500,
    "FFPROBE_UNAVAILABLE": 503,   # /v1/local/media/probe: the app falls back to the <video> element
    # weights download (/v1/local/weights, #33). Most of these appear as the job's `error.code`, not as an HTTP status
    "WEIGHTS_BUSY": 409,
    "WEIGHTS_DISK_FULL": 507,
    "HF_TOKEN_REQUIRED": 401,
    "HF_TOKEN_INVALID": 401,
    "HF_ACCESS_DENIED": 403,
    "WEIGHTS_NETWORK": 502,
    "WEIGHTS_NOT_FOUND": 404,
    "WEIGHTS_CHECKSUM_MISMATCH": 409,
    "WEIGHTS_IO_ERROR": 500,
    "WEIGHTS_FAILED": 500,
    # first-run bootstrap (/v1/local/bootstrap, #32). Most of these appear as the job's `error.code`
    "BOOTSTRAP_BUSY": 409,
    "BOOTSTRAP_UNSUPPORTED": 409,   # no usable NVIDIA GPU/driver: the app points to restricted mode or a BYO GPU server
    "BOOTSTRAP_UV_MISSING": 503,
    "BOOTSTRAP_COMMAND_FAILED": 500,
    "BOOTSTRAP_NO_WHEEL": 500,
    "BOOTSTRAP_DISK_FULL": 507,
    "BOOTSTRAP_INTERNAL": 500,
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
