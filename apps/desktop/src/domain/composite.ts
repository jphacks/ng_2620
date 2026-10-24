// 合成規則（contracts §5）の CPU 参照実装。WebGL のシェーダー（features/editor/compositor）と同じ式で、テストの正解を作る。
// プレビュー（アプリ）と書き出し（サーバー）で共通の規則：
//   out ← clean
//   for p in order（奥 → 手前）: a ← alpha_p, h ← effectiveHide(p), c ← h ? frame : avatar_p, out ← out·(1−a) + c·a

import type { OverrideMode } from "./overrides";

/** そのフレームで (personId, unitId) に効いている手修正のモード（無ければ null） */
export type ModeOf = (personId: number, unitId: number) => OverrideMode | null;

export interface PersonLayer {
  personId: number;
  /** アバター RGBA（ストレートアルファ）、w×h×4 */
  avatar: Uint8Array | Uint8ClampedArray;
  /** 自動の隠れマスク（0 / 255）、w×h。無ければ隠さない */
  hide?: Uint8Array | Uint8ClampedArray | null;
}

export interface CompositeInput {
  width: number;
  height: number;
  /** 元フレーム RGBA（隠す画素はこれ＝手前の実物を出す） */
  frame: Uint8Array | Uint8ClampedArray;
  /** クリーンプレート RGBA */
  clean: Uint8Array | Uint8ClampedArray;
  persons: PersonLayer[];
  /** 奥 → 手前の personId（layers.json の order[f]） */
  order: number[];
  /** 遮蔽物ユニットのラベル（0 = なし）、w×h。無ければ手修正は効かない */
  units?: Uint16Array | null;
  modeOf?: ModeOf;
}

/** 画素 i で人物 p を隠すか（contracts §5 effectiveHide。ブラシは P1） */
export function effectiveHide(
  layer: PersonLayer,
  i: number,
  units: Uint16Array | null | undefined,
  modeOf: ModeOf | undefined,
): boolean {
  let h = layer.hide ? layer.hide[i]! >= 128 : false;
  const u = units ? units[i]! : 0;
  if (u !== 0 && modeOf) {
    const m = modeOf(layer.personId, u);
    if (m === "front") h = layer.avatar[i * 4 + 3]! > 0;
    else if (m === "behind") h = false;
  }
  return h;
}

/** 1 フレームを合成して RGBA（不透明）を返す */
export function compositeFrame(input: CompositeInput): Uint8ClampedArray {
  const { width, height, frame, clean, persons, order, units, modeOf } = input;
  const n = width * height;
  const out = new Uint8ClampedArray(n * 4);
  const byId = new Map(persons.map((p) => [p.personId, p]));
  const layers = order.map((id) => byId.get(id)).filter((p): p is PersonLayer => !!p);
  for (let i = 0; i < n; i++) {
    const o = i * 4;
    let r = clean[o]!;
    let g = clean[o + 1]!;
    let b = clean[o + 2]!;
    for (const p of layers) {
      const a = p.avatar[o + 3]! / 255;
      if (a === 0) continue;
      const src = effectiveHide(p, i, units, modeOf) ? frame : p.avatar;
      r = r * (1 - a) + src[o]! * a;
      g = g * (1 - a) + src[o + 1]! * a;
      b = b * (1 - a) + src[o + 2]! * a;
    }
    out[o] = Math.round(r);
    out[o + 1] = Math.round(g);
    out[o + 2] = Math.round(b);
    out[o + 3] = 255;
  }
  return out;
}

/** 2 つの RGBA 画像の「差が threshold を超える画素」の割合（contracts §5 の一致テスト：24/255、0.5% 未満） */
export function diffRatio(a: ArrayLike<number>, b: ArrayLike<number>, threshold = 24): number {
  const n = Math.floor(Math.min(a.length, b.length) / 4);
  if (n === 0) return 0;
  let bad = 0;
  for (let i = 0; i < n; i++) {
    const o = i * 4;
    if (
      Math.abs(a[o]! - b[o]!) > threshold ||
      Math.abs(a[o + 1]! - b[o + 1]!) > threshold ||
      Math.abs(a[o + 2]! - b[o + 2]!) > threshold
    )
      bad++;
  }
  return bad / n;
}
