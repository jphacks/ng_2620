// エラー → 画面に出す文言（見出し・本文）。コードごとの対処は呼び出し側がボタンで出す

import type { ApiErrorBody, AppErrorCode } from "@/domain/errors";
import { AppError, isApiErrorCode, toAppError } from "@/domain/errors";
import type { StageName } from "@/domain/job";
import type { Violation } from "@/domain/validation";
import { ja } from "./i18n/ja";

export interface ErrorText {
  code: AppErrorCode;
  title: string;
  body: string;
}

type Detail = Record<string, unknown>;

export function errorText(e: unknown): ErrorText {
  const err = toAppError(e);
  const entry = ja.errors[err.code] ?? ja.errors.UNKNOWN;
  const detail: Detail = { ...err.detail, message: err.message };
  if (typeof detail.stage === "string" && detail.stage in ja.stage) detail.stage = ja.stage[detail.stage as StageName];
  return { code: err.code, title: entry.title, body: (entry.body as (d: Detail) => string)(detail) || err.message };
}

/** ジョブの error（job_failed）を文言に */
export function jobErrorText(error: ApiErrorBody | undefined): ErrorText {
  if (!error) return errorText(new AppError("UNKNOWN", ""));
  const code = isApiErrorCode(error.code) ? error.code : "UNKNOWN";
  return errorText(new AppError(code, error.message, { detail: error.detail }));
}

export function violationText(v: Violation): ErrorText {
  return errorText(new AppError(v.code, "", { detail: v as unknown as Detail }));
}
