// 人物とキャスト（F06）。persons 成果物（contracts §4）から作る

import { hashValue } from "./hash";

export const MAX_TARGETS = 4;

export interface Person {
  personId: number; // パイプラインの pid（1 始まり）
  label: string; // ユーザーが付けた名前（既定「人物 n」）
  firstFrame: number;
  lastFrame: number;
  visibleRatio: number; // 0..1
  bestFrame?: number;
  bbox?: [number, number, number, number]; // bestFrame の proxy 座標 [x, y, w, h]
  thumb?: string; // プロジェクトフォルダからの相対パス
}

export interface CastEntry {
  personId: number;
  action: "replace"; // 将来 "erase" | "blur"（予約）
  avatarId: string;
}

export interface FinishParams {
  smooth?: number;
  footLock?: boolean;
  spring?: boolean;
}

export const DEFAULT_FINISH: Required<FinishParams> = { smooth: 0.05, footLock: true, spring: true };

/** キャスト画面の下書き：選んだがアバター未割当の人物も持てる */
export interface CastDraftRow {
  personId: number;
  selected: boolean;
  avatarId: string | null;
}

export type CanRender =
  | { ok: true; cast: CastEntry[] }
  | { ok: false; reason: "none_selected" }
  | { ok: false; reason: "missing_avatar"; personIds: number[] }
  | { ok: false; reason: "too_many"; limit: number };

export function canRender(draft: readonly CastDraftRow[], maxTargets = MAX_TARGETS): CanRender {
  const selected = draft.filter((r) => r.selected);
  if (selected.length === 0) return { ok: false, reason: "none_selected" };
  if (selected.length > maxTargets) return { ok: false, reason: "too_many", limit: maxTargets };
  const missing = selected.filter((r) => !r.avatarId).map((r) => r.personId);
  if (missing.length > 0) return { ok: false, reason: "missing_avatar", personIds: missing };
  return { ok: true, cast: toCast(draft) };
}

/** 選択済みでアバターのある行だけを CastEntry に（personId 順） */
export function toCast(draft: readonly CastDraftRow[]): CastEntry[] {
  return draft
    .filter((r): r is CastDraftRow & { avatarId: string } => r.selected && !!r.avatarId)
    .map((r) => ({ personId: r.personId, action: "replace" as const, avatarId: r.avatarId }))
    .sort((a, b) => a.personId - b.personId);
}

/** 保存済みのキャストと人物一覧から下書きを作る */
export function draftFromCast(persons: readonly Person[], cast: readonly CastEntry[]): CastDraftRow[] {
  return persons.map((p) => {
    const c = cast.find((x) => x.personId === p.personId);
    return { personId: p.personId, selected: !!c, avatarId: c?.avatarId ?? null };
  });
}

/** 描画に効く内容（キャスト＋仕上げ）のハッシュ。描画時点と違えば「再描画が必要」 */
export function castHash(cast: readonly CastEntry[], finish: FinishParams | undefined): string {
  const sorted = [...cast].sort((a, b) => a.personId - b.personId).map((c) => [c.personId, c.action, c.avatarId]);
  return hashValue({ cast: sorted, finish: { ...DEFAULT_FINISH, ...finish } });
}

interface RawPerson {
  personId?: unknown;
  firstFrame?: unknown;
  lastFrame?: unknown;
  visibleRatio?: unknown;
  bestFrame?: unknown;
  bbox?: unknown;
  thumb?: unknown;
}

const num = (v: unknown, d = 0): number => (typeof v === "number" && Number.isFinite(v) ? v : d);

/**
 * persons.json を Person[] に。
 * @param previous 既存の人物（ユーザーが付けた名前を引き継ぐ）
 * @param thumbRel thumb（"person_thumbs/1.jpg"）をプロジェクト相対に直す関数
 */
export function parsePersons(
  raw: unknown,
  opts: {
    previous?: readonly Person[];
    defaultLabel: (personId: number) => string;
    thumbRel?: (thumb: string) => string;
  },
): Person[] {
  if (!Array.isArray(raw)) return [];
  const out: Person[] = [];
  for (const r of raw as RawPerson[]) {
    const personId = num(r?.personId, NaN);
    if (!Number.isInteger(personId)) continue;
    const prev = opts.previous?.find((p) => p.personId === personId);
    const bbox =
      Array.isArray(r.bbox) && r.bbox.length === 4 ? (r.bbox.map((v) => num(v)) as Person["bbox"]) : undefined;
    out.push({
      personId,
      label: prev?.label ?? opts.defaultLabel(personId),
      firstFrame: num(r.firstFrame),
      lastFrame: num(r.lastFrame),
      visibleRatio: num(r.visibleRatio),
      bestFrame: typeof r.bestFrame === "number" ? r.bestFrame : undefined,
      bbox,
      thumb: typeof r.thumb === "string" ? (opts.thumbRel ? opts.thumbRel(r.thumb) : r.thumb) : undefined,
    });
  }
  return out.sort((a, b) => a.personId - b.personId);
}

export function renamePerson(persons: readonly Person[], personId: number, label: string, fallback: string): Person[] {
  const trimmed = label.trim();
  return persons.map((p) => (p.personId === personId ? { ...p, label: trimmed || fallback } : p));
}
