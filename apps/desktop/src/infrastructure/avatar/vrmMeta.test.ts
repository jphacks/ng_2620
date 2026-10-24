import { describe, expect, it } from "vitest";
import { licenseBadge } from "@/domain/avatar";
import { readVrm, REQUIRED_BONES } from "./vrmMeta";

/** テスト用に最小の GLB を組み立てる（VRM ファイルはリポジトリに置かない） */
function glb(json: object, bin?: Uint8Array): Uint8Array {
  const enc = new TextEncoder();
  let j = enc.encode(JSON.stringify(json));
  const padJ = (4 - (j.length % 4)) % 4;
  j = new Uint8Array([...j, ...new Array(padJ).fill(0x20)]);
  const binPadded = bin ? new Uint8Array([...bin, ...new Array((4 - (bin.length % 4)) % 4).fill(0)]) : undefined;
  const total = 12 + 8 + j.length + (binPadded ? 8 + binPadded.length : 0);
  const out = new Uint8Array(total);
  const v = new DataView(out.buffer);
  v.setUint32(0, 0x46546c67, true);
  v.setUint32(4, 2, true);
  v.setUint32(8, total, true);
  v.setUint32(12, j.length, true);
  v.setUint32(16, 0x4e4f534a, true);
  out.set(j, 20);
  if (binPadded) {
    const p = 20 + j.length;
    v.setUint32(p, binPadded.length, true);
    v.setUint32(p + 4, 0x004e4942, true);
    out.set(binPadded, p + 8);
  }
  return out;
}

const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 1, 2, 3]);
const images = { images: [{ bufferView: 0, mimeType: "image/png" }], bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: PNG.length }] };

describe("readVrm", () => {
  it("VRM 1.0 のメタ情報とサムネイル", () => {
    const humanBones = Object.fromEntries(REQUIRED_BONES.map((b, i) => [b, { node: i }]));
    const bytes = glb(
      {
        asset: { version: "2.0" },
        ...images,
        extensions: {
          VRMC_vrm: {
            meta: {
              name: "Sample A",
              authors: ["Alice"],
              licenseUrl: "https://vrm.dev/licenses/1.0/",
              commercialUsage: "corporation",
              avatarPermission: "everyone",
              allowRedistribution: false,
              modification: "prohibited",
              thumbnailImage: 0,
            },
            humanoid: { humanBones },
          },
        },
      },
      PNG,
    );
    const r = readVrm(bytes);
    expect(r.meta).toEqual({
      name: "Sample A",
      authors: ["Alice"],
      version: "1.0",
      license: {
        url: "https://vrm.dev/licenses/1.0/",
        name: "VRM Public License 1.0",
        commercial: "corporation",
        avatarPermission: "everyone",
        redistribution: false,
        modification: "prohibited",
      },
    });
    expect(r.thumbnail?.mimeType).toBe("image/png");
    expect([...r.thumbnail!.bytes]).toEqual([...PNG]);
    expect(licenseBadge({ ...r.meta })).toBe("commercial_ok");
  });

  it("VRM 0.x のメタ情報（サムネイルは texture 経由）", () => {
    const humanBones = REQUIRED_BONES.map((bone, node) => ({ bone, node }));
    const bytes = glb(
      {
        asset: { version: "2.0" },
        ...images,
        textures: [{ source: 0 }],
        extensions: {
          VRM: {
            meta: { title: "Old", author: "Bob", licenseName: "CC_BY_NC", commercialUssageName: "Disallow", allowedUserName: "OnlyAuthor", texture: 0 },
            humanoid: { humanBones },
          },
        },
      },
      PNG,
    );
    const r = readVrm(bytes);
    expect(r.meta).toMatchObject({ name: "Old", authors: ["Bob"], version: "0.x", license: { name: "CC_BY_NC", commercial: "Disallow" } });
    expect(r.thumbnail?.bytes.length).toBe(PNG.length);
    expect(licenseBadge({ ...r.meta })).toBe("commercial_ng");
  });

  it("必須ボーンが無い・VRM 拡張が無い・GLB でないものは AVATAR_INVALID", () => {
    const noHead = Object.fromEntries(REQUIRED_BONES.filter((b) => b !== "head").map((b, i) => [b, { node: i }]));
    expect(() => readVrm(glb({ extensions: { VRMC_vrm: { meta: {}, humanoid: { humanBones: noHead } } } }))).toThrowError(
      expect.objectContaining({ code: "AVATAR_INVALID", detail: { missing: ["head"] } }),
    );
    expect(() => readVrm(glb({ asset: { version: "2.0" } }))).toThrowError(expect.objectContaining({ code: "AVATAR_INVALID" }));
    expect(() => readVrm(new TextEncoder().encode("not a glb at all, really"))).toThrowError(expect.objectContaining({ code: "AVATAR_INVALID" }));
  });
});
