// エラーコード（contracts §2.3）とアプリ内で起きるもの

export const API_ERROR_CODES = [
  "UNAUTHORIZED",
  "NOT_FOUND",
  "VALIDATION_FAILED",
  "MEDIA_TOO_LONG",
  "MEDIA_TOO_LARGE",
  "MEDIA_UNSUPPORTED",
  "UPLOAD_INCOMPLETE",
  "CHECKSUM_MISMATCH",
  "AVATAR_INVALID",
  "DEPENDENCY_NOT_READY",
  "NO_PERSON_FOUND",
  "GPU_OOM",
  "PIPELINE_FAILED",
  "INTERRUPTED",
  "CANCELED",
  "NOT_IMPLEMENTED",
] as const;

export type ApiErrorCode = (typeof API_ERROR_CODES)[number];

/** サーバーは返さず、アプリ内で判定するもの */
export type LocalErrorCode =
  | "NETWORK" // 届かない・切れた
  | "OFFLINE" // 接続なし（OfflineComputeBackend）
  | "API_VERSION_MISMATCH"
  | "SAVE_FAILED"
  | "PROJECT_TOO_NEW"
  | "PROJECT_INVALID"
  | "UNKNOWN";

export type AppErrorCode = ApiErrorCode | LocalErrorCode;

/** サーバーのエラー本文 `{"error": {...}}` の中身 */
export interface ApiErrorBody {
  code: string;
  message: string;
  detail?: Record<string, unknown>;
}

export class AppError extends Error {
  readonly code: AppErrorCode;
  readonly detail?: Record<string, unknown>;
  readonly status?: number;

  constructor(code: AppErrorCode, message: string, opts: { detail?: Record<string, unknown>; status?: number } = {}) {
    super(message);
    this.name = "AppError";
    this.code = code;
    this.detail = opts.detail;
    this.status = opts.status;
  }
}

export function isApiErrorCode(code: string): code is ApiErrorCode {
  return (API_ERROR_CODES as readonly string[]).includes(code);
}

/** サーバーのエラー本文を AppError に。未知のコードは UNKNOWN にして元のコードを detail に残す */
export function fromApiError(body: ApiErrorBody, status?: number): AppError {
  if (isApiErrorCode(body.code)) {
    return new AppError(body.code, body.message, { detail: body.detail, status });
  }
  return new AppError("UNKNOWN", body.message, { detail: { ...body.detail, originalCode: body.code }, status });
}

export function toAppError(e: unknown): AppError {
  if (e instanceof AppError) return e;
  if (e instanceof Error) return new AppError("UNKNOWN", e.message);
  return new AppError("UNKNOWN", String(e));
}

export function isAppError(e: unknown, code?: AppErrorCode): e is AppError {
  return e instanceof AppError && (code === undefined || e.code === code);
}
