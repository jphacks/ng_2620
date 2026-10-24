import { describe, expect, it } from "vitest";
import { canRender, castHash, draftFromCast, parsePersons, renamePerson, toCast } from "./cast";

describe("canRender", () => {
  it("1 人以上を選び、全員にアバターがあれば描画できる", () => {
    expect(canRender([{ personId: 1, selected: false, avatarId: null }])).toEqual({
      ok: false,
      reason: "none_selected",
    });
    expect(
      canRender([
        { personId: 1, selected: true, avatarId: "av_a" },
        { personId: 2, selected: true, avatarId: null },
      ]),
    ).toEqual({ ok: false, reason: "missing_avatar", personIds: [2] });
    expect(
      canRender([
        { personId: 2, selected: true, avatarId: "av_b" },
        { personId: 1, selected: true, avatarId: "av_a" },
        { personId: 3, selected: false, avatarId: "av_c" },
      ]),
    ).toEqual({
      ok: true,
      cast: [
        { personId: 1, action: "replace", avatarId: "av_a" },
        { personId: 2, action: "replace", avatarId: "av_b" },
      ],
    });
  });

  it("上限は 4 人", () => {
    const rows = [1, 2, 3, 4, 5].map((id) => ({ personId: id, selected: true, avatarId: "av" }));
    expect(canRender(rows)).toEqual({ ok: false, reason: "too_many", limit: 4 });
  });
});

describe("castHash", () => {
  it("並び順に依らず、アバターか仕上げが変わると変わる", () => {
    const a = [
      { personId: 1, action: "replace" as const, avatarId: "av_a" },
      { personId: 2, action: "replace" as const, avatarId: "av_b" },
    ];
    expect(castHash(a, {})).toBe(castHash([...a].reverse(), { smooth: 0.05 }));
    expect(castHash(a, {})).not.toBe(castHash([{ ...a[0]!, avatarId: "av_c" }, a[1]!], {}));
    expect(castHash(a, {})).not.toBe(castHash(a, { footLock: false }));
  });
});

describe("parsePersons", () => {
  it("persons.json を読み、既存の名前を引き継ぐ", () => {
    const raw = [
      {
        personId: 2,
        firstFrame: 90,
        lastFrame: 210,
        visibleRatio: 0.41,
        bestFrame: 150,
        bbox: [1, 2, 3, 4],
        thumb: "person_thumbs/2.jpg",
      },
      { personId: 1, firstFrame: 0, lastFrame: 293, visibleRatio: 0.97, thumb: "person_thumbs/1.jpg" },
      { firstFrame: 0 },
    ];
    const persons = parsePersons(raw, {
      previous: [{ personId: 1, label: "わたし", firstFrame: 0, lastFrame: 0, visibleRatio: 0 }],
      defaultLabel: (id) => `人物 ${id}`,
      thumbRel: (t) => `cache/job_a/${t}`,
    });
    expect(persons.map((p) => [p.personId, p.label, p.thumb])).toEqual([
      [1, "わたし", "cache/job_a/person_thumbs/1.jpg"],
      [2, "人物 2", "cache/job_a/person_thumbs/2.jpg"],
    ]);
    expect(persons[1]?.bbox).toEqual([1, 2, 3, 4]);
    expect(parsePersons({}, { defaultLabel: String })).toEqual([]);
  });

  it("名前の変更（空なら既定に戻す）", () => {
    const persons = [{ personId: 1, label: "人物 1", firstFrame: 0, lastFrame: 1, visibleRatio: 1 }];
    expect(renamePerson(persons, 1, "  ギターの人 ", "人物 1")[0]?.label).toBe("ギターの人");
    expect(renamePerson(persons, 1, "  ", "人物 1")[0]?.label).toBe("人物 1");
  });
});

describe("下書き", () => {
  it("保存済みキャストから下書きを作り、戻せる", () => {
    const persons = [1, 2].map((id) => ({ personId: id, label: "", firstFrame: 0, lastFrame: 1, visibleRatio: 1 }));
    const draft = draftFromCast(persons, [{ personId: 2, action: "replace", avatarId: "av_b" }]);
    expect(draft).toEqual([
      { personId: 1, selected: false, avatarId: null },
      { personId: 2, selected: true, avatarId: "av_b" },
    ]);
    expect(toCast(draft)).toEqual([{ personId: 2, action: "replace", avatarId: "av_b" }]);
  });
});
