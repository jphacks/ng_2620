import { describe, expect, it } from "vitest";
import { compositeFrame, diffRatio } from "./composite";
import { History } from "./history";
import { defaultRange, explainWhy, modeKey, parseUnits, removeRange, resolveModes, setRange, subtractRange, upsertOverride } from "./occlusion";
import type { UnitOverride } from "./overrides";

const ov = (id: string, range: [number, number], mode: "front" | "behind", personId = 1, unitId = 5): UnitOverride => ({
  id, kind: "unit", personId, unitId, range, mode, createdAt: "t",
});

function ids() {
  let n = 0;
  return () => `ovr_new${++n}`;
}

describe("compositeFrame（contracts §5）", () => {
  // 1×3 画素：0 = アバターなし、1 = アバター（隠れマスクなし）、2 = アバター（隠れマスクあり、ユニット 7）
  const frame = new Uint8Array([10, 10, 10, 255, 20, 20, 20, 255, 200, 0, 0, 255]);
  const clean = new Uint8Array([1, 1, 1, 255, 2, 2, 2, 255, 3, 3, 3, 255]);
  const avatar = new Uint8Array([0, 0, 0, 0, 0, 0, 255, 255, 0, 255, 0, 255]);
  const hide = new Uint8Array([0, 0, 255]);
  const units = new Uint16Array([0, 7, 7]);
  const base = { width: 3, height: 1, frame, clean, persons: [{ personId: 1, avatar, hide }], order: [1], units };

  it("アバターの無い画素はクリーンプレート、隠れマスクの画素は元フレーム", () => {
    const out = compositeFrame(base);
    expect(Array.from(out)).toEqual([1, 1, 1, 255, 0, 0, 255, 255, 200, 0, 0, 255]);
  });

  it("手修正 front：ユニット上のアバター画素は元フレーム、behind：隠さない", () => {
    const front = compositeFrame({ ...base, modeOf: () => "front" });
    expect(Array.from(front.slice(4, 8))).toEqual([20, 20, 20, 255]);
    const behind = compositeFrame({ ...base, modeOf: () => "behind" });
    expect(Array.from(behind.slice(8, 12))).toEqual([0, 255, 0, 255]);
  });

  it("半透明は奥から順に重ねる", () => {
    const a1 = new Uint8Array([100, 0, 0, 128]);
    const a2 = new Uint8Array([0, 0, 200, 128]);
    const one = { width: 1, height: 1, frame: new Uint8Array([0, 0, 0, 255]), clean: new Uint8Array([0, 0, 0, 255]) };
    const out = compositeFrame({ ...one, persons: [{ personId: 1, avatar: a1 }, { personId: 2, avatar: a2 }], order: [1, 2] });
    const a = 128 / 255;
    expect(Array.from(out)).toEqual([Math.round(100 * a * (1 - a)), 0, Math.round(200 * a), 255]);
  });

  it("diffRatio は閾値を超える画素の割合", () => {
    expect(diffRatio([0, 0, 0, 255, 0, 0, 0, 255], [30, 0, 0, 255, 20, 0, 0, 255])).toBe(0.5);
  });
});

describe("手修正の区間演算", () => {
  it("subtractRange", () => {
    expect(subtractRange([0, 10], [3, 5])).toEqual([[0, 2], [6, 10]]);
    expect(subtractRange([0, 10], [0, 10])).toEqual([]);
    expect(subtractRange([0, 10], [20, 30])).toEqual([[0, 10]]);
  });

  it("重なる修正は新しい方が勝ち、古い方は分割される", () => {
    const list = upsertOverride([ov("a", [0, 100], "front")], ov("b", [40, 60], "behind"), ids());
    expect(list.map((x) => [x.id, x.range, x.mode])).toEqual([
      ["a", [0, 39], "front"],
      ["ovr_new1", [61, 100], "front"],
      ["b", [40, 60], "behind"],
    ]);
    expect(resolveModes(list, 50).get(modeKey(1, 5))).toBe("behind");
    expect(resolveModes(list, 70).get(modeKey(1, 5))).toBe("front");
    expect(resolveModes(list, 200).size).toBe(0);
  });

  it("隣り合う同じモードはまとめ、別の人物・ユニットには触らない", () => {
    let list = upsertOverride([ov("x", [0, 10], "front", 2)], ov("a", [0, 9], "front"), ids());
    list = upsertOverride(list, ov("b", [10, 20], "front"), ids());
    expect(list.filter((x) => x.personId === 1).map((x) => [x.id, x.range])).toEqual([["a", [0, 20]]]);
    expect(list.find((x) => x.personId === 2)!.range).toEqual([0, 10]);
  });

  it("removeRange と setRange", () => {
    const list = removeRange([ov("a", [0, 100], "front")], 1, 5, [10, 20], ids());
    expect(list.map((x) => x.range)).toEqual([[0, 9], [21, 100]]);
    expect(setRange(list, "a", [5, 2], 50)[0]!.range).toEqual([2, 5]);
    expect(setRange(list, "a", [-5, 900], 50)[0]!.range).toEqual([0, 50]);
  });
});

describe("units.json", () => {
  const units = parseUnits({
    units: [
      { unitId: 3, frames: [10, 80], label: "object", areaPx: 900, tracks: [1, 2],
        perPerson: { "1": { auto: "front", why: "D+", confidence: 0.8, overlapFrames: [20, 40] }, "2": { auto: "none", why: "", confidence: 0, overlapFrames: [] } } },
      { bad: true },
    ],
  });

  it("不正な要素は捨てる、既定の区間は overlapFrames → frames", () => {
    expect(units.map((u) => u.unitId)).toEqual([3]);
    expect(defaultRange(units[0]!, 1)).toEqual([20, 40]);
    expect(defaultRange(units[0]!, 2)).toEqual([10, 80]);
  });

  it("判定理由を平易な文に", () => {
    expect(explainWhy("D+", "front")).toBe("奥行きの差から手前と判定");
    expect(explainWhy("", "none")).toBe("この人物とは重ならない");
  });
});

describe("History", () => {
  it("undo / redo、push でやり直しは消える", () => {
    const h = new History(0);
    h.push(1);
    h.push(2);
    expect(h.undo()).toBe(1);
    expect(h.undo()).toBe(0);
    expect(h.canUndo).toBe(false);
    expect(h.redo()).toBe(1);
    h.push(5);
    expect(h.canRedo).toBe(false);
    expect(h.current).toBe(5);
  });
});
