// 入力制約（ADR-0011）：180 秒以下・長辺 1920px 以下・mp4 / mov（H.264 / HEVC）

import type { LocalMediaInfo } from "./media";
import { normalizeCodec } from "./media";

export interface MediaLimits {
  maxDurationSec: number;
  maxLongEdge: number;
}

/** 既定値。サーバーの capabilities.limits があれば上書きする */
export const DEFAULT_LIMITS: MediaLimits = { maxDurationSec: 180, maxLongEdge: 1920 };

export const ACCEPTED_EXTENSIONS = ["mp4", "mov"] as const;
export const ACCEPTED_CODECS = ["h264", "hevc"] as const;

export type Violation =
  | { code: "MEDIA_TOO_LONG"; durationSec: number; limitSec: number }
  | { code: "MEDIA_TOO_LARGE"; width: number; height: number; limitPx: number }
  | { code: "MEDIA_UNSUPPORTED"; reason: "extension" | "codec"; value: string };

export function fileNameOf(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] ?? path;
}

export function extensionOf(path: string): string {
  const name = fileNameOf(path);
  const i = name.lastIndexOf(".");
  return i < 0 ? "" : name.slice(i + 1).toLowerCase();
}

export function isAcceptedVideoPath(path: string): boolean {
  return (ACCEPTED_EXTENSIONS as readonly string[]).includes(extensionOf(path));
}

/** 秒は 1/100 で丸めてから比べる（コンテナの端数で 30.000001 秒になるのを許す） */
function roundSec(sec: number): number {
  return Math.round(sec * 100) / 100;
}

export function validateMedia(
  path: string,
  info: LocalMediaInfo | null,
  limits: MediaLimits = DEFAULT_LIMITS,
): Violation[] {
  const out: Violation[] = [];
  const ext = extensionOf(path);
  if (!(ACCEPTED_EXTENSIONS as readonly string[]).includes(ext)) {
    out.push({ code: "MEDIA_UNSUPPORTED", reason: "extension", value: ext || path });
    return out;
  }
  if (!info) return out;

  const codec = normalizeCodec(info.codec);
  if (codec && !(ACCEPTED_CODECS as readonly string[]).includes(codec)) {
    out.push({ code: "MEDIA_UNSUPPORTED", reason: "codec", value: codec });
  }
  if (roundSec(info.durationSec) > limits.maxDurationSec) {
    out.push({ code: "MEDIA_TOO_LONG", durationSec: roundSec(info.durationSec), limitSec: limits.maxDurationSec });
  }
  if (Math.max(info.width, info.height) > limits.maxLongEdge) {
    out.push({ code: "MEDIA_TOO_LARGE", width: info.width, height: info.height, limitPx: limits.maxLongEdge });
  }
  return out;
}

export function mergeLimits(base: MediaLimits, override: Partial<MediaLimits> | undefined): MediaLimits {
  return {
    maxDurationSec: override?.maxDurationSec ?? base.maxDurationSec,
    maxLongEdge: override?.maxLongEdge ?? base.maxLongEdge,
  };
}
