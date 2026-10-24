// 遮蔽物ユニットと前後関係の手修正（F10、contracts §4.1・§6.2、ADR-0006）。外部依存なし。
// 手修正は「人物 × ユニット × 区間 → 前 / 後ろ」。同じ人物・ユニットで区間が重なるときは新しい方が勝つ。

import type { OverrideMode, UnitOverride } from "./overrides";

export type AutoDecision = "front" | "behind" | "none";

export interface PerPersonDecision {
  auto: AutoDecision;
  why: string;
  confidence: number;
  overlapFrames: [number, number] | [];
}

export interface OccluderUnit {
  unitId: number;
  frames: [number, number];
  label: "object" | "person";
  areaPx: number;
  perPerson: Record<string, PerPersonDecision>;
}

export type Range = [number, number];

/** units.json を読む（不正な要素は捨てる） */
export function parseUnits(raw: unknown): OccluderUnit[] {
  const list = raw && typeof raw === "object" ? (raw as { units?: unknown }).units : null;
  if (!Array.isArray(list)) return [];
  return list.flatMap((u): OccluderUnit[] => {
    if (!u || typeof u !== "object") return [];
    const x = u as Record<string, unknown>;
    if (typeof x.unitId !== "number" || !Array.isArray(x.frames) || x.frames.length !== 2) return [];
    return [
      {
        unitId: x.unitId,
        frames: [Number(x.frames[0]), Number(x.frames[1])],
        label: x.label === "person" ? "person" : "object",
        areaPx: typeof x.areaPx === "number" ? x.areaPx : 0,
        perPerson: (x.perPerson && typeof x.perPerson === "object" ? x.perPerson : {}) as Record<
          string,
          PerPersonDecision
        >,
      },
    ];
  });
}

/** 修正の既定の区間：その人物と重なる区間（overlapFrames）、無ければユニットの出現区間 */
export function defaultRange(unit: OccluderUnit, personId: number): Range {
  const o = unit.perPerson[String(personId)]?.overlapFrames;
  return o && o.length === 2 ? [o[0], o[1]] : [unit.frames[0], unit.frames[1]];
}

export function autoDecision(unit: OccluderUnit, personId: number): AutoDecision {
  return unit.perPerson[String(personId)]?.auto ?? "none";
}

const inRange = (f: number, r: Range) => r[0] <= f && f <= r[1];

/** フレーム f で効いている手修正：キー `${personId}:${unitId}` → モード（後に追加したものが勝つ） */
export function resolveModes(overrides: readonly UnitOverride[], f: number): Map<string, OverrideMode> {
  const m = new Map<string, OverrideMode>();
  for (const o of overrides) if (inRange(f, o.range)) m.set(modeKey(o.personId, o.unitId), o.mode);
  return m;
}

export const modeKey = (personId: number, unitId: number) => `${personId}:${unitId}`;

/** 区間 r から cut を除いた残り（0〜2 個） */
export function subtractRange(r: Range, cut: Range): Range[] {
  if (cut[1] < r[0] || cut[0] > r[1]) return [r];
  const out: Range[] = [];
  if (r[0] < cut[0]) out.push([r[0], cut[0] - 1]);
  if (cut[1] < r[1]) out.push([cut[1] + 1, r[1]]);
  return out;
}

/**
 * 手修正を追加する。同じ人物・ユニットの既存の修正は、新しい区間と重なる部分を削り（分割されることもある）、
 * 新しい修正を末尾に置く。隣り合う同じモードの区間は 1 つにまとめる。
 * newId：分割で増えるレコードの ID を作る
 */
export function upsertOverride(list: readonly UnitOverride[], o: UnitOverride, newId: () => string): UnitOverride[] {
  const out: UnitOverride[] = [];
  for (const x of list) {
    if (x.personId !== o.personId || x.unitId !== o.unitId) {
      out.push(x);
      continue;
    }
    subtractRange(x.range, o.range).forEach((r, k) => out.push({ ...x, id: k === 0 ? x.id : newId(), range: r }));
  }
  out.push(o);
  return mergeAdjacent(out);
}

/** 人物・ユニットの区間 cut にある修正を取り除く（区間の一部なら削る） */
export function removeRange(
  list: readonly UnitOverride[],
  personId: number,
  unitId: number,
  cut: Range,
  newId: () => string,
): UnitOverride[] {
  const out: UnitOverride[] = [];
  for (const x of list) {
    if (x.personId !== personId || x.unitId !== unitId) {
      out.push(x);
      continue;
    }
    subtractRange(x.range, cut).forEach((r, k) => out.push({ ...x, id: k === 0 ? x.id : newId(), range: r }));
  }
  return out;
}

/** 区間の端を動かす（並び順＝優先順位は保つ）。区間は [0, last] に収め、空にはしない */
export function setRange(list: readonly UnitOverride[], id: string, range: Range, last: number): UnitOverride[] {
  const a = Math.max(0, Math.min(range[0], range[1], last));
  const b = Math.min(last, Math.max(range[0], range[1], 0));
  return list.map((x) => (x.id === id ? { ...x, range: [a, b] } : x));
}

function mergeAdjacent(list: UnitOverride[]): UnitOverride[] {
  const out: UnitOverride[] = [];
  for (const x of list) {
    const k = out.findIndex(
      (y) =>
        y.personId === x.personId &&
        y.unitId === x.unitId &&
        y.mode === x.mode &&
        (y.range[1] + 1 === x.range[0] || x.range[1] + 1 === y.range[0]),
    );
    if (k >= 0) {
      const y = out[k]!;
      out.splice(k, 1);
      out.push({ ...x, id: y.id, range: [Math.min(x.range[0], y.range[0]), Math.max(x.range[1], y.range[1])] });
    } else out.push(x);
  }
  return out;
}

/** 判定理由（units.json の why）を平易な文に（F10 受け入れ条件） */
export function explainWhy(why: string, auto: AutoDecision): string {
  switch (why) {
    case "D+":
      return "奥行きの差から手前と判定";
    case "S+":
      return "形の重なりから手前と判定";
    case "M+":
      return "人物どうしの位置から手前と判定";
    case "D-":
    case "S-":
      return "後ろと判定";
    case "Dinf":
      return "遠くの背景と判定";
    case "OVR":
      return "手修正";
    default:
      return auto === "none" ? "この人物とは重ならない" : "";
  }
}
