// VRM（GLB）のメタ情報を読む（contracts §7、F07）。
// glTF 拡張 VRM（0.x）/ VRMC_vrm（1.0）を AvatarMeta に正規化し、サムネイル画像とヒューマノイドの必須ボーンを確かめる

import type { AvatarMeta } from "@/domain/avatar";
import { AppError } from "@/domain/errors";

export const REQUIRED_BONES = [
  "hips",
  "spine",
  "head",
  "leftUpperArm",
  "leftLowerArm",
  "leftHand",
  "rightUpperArm",
  "rightLowerArm",
  "rightHand",
  "leftUpperLeg",
  "leftLowerLeg",
  "leftFoot",
  "rightUpperLeg",
  "rightLowerLeg",
  "rightFoot",
] as const;

const GLB_MAGIC = 0x46546c67; // "glTF"
const CHUNK_JSON = 0x4e4f534a;
const CHUNK_BIN = 0x004e4942;

type Json = Record<string, unknown>;
const isObj = (v: unknown): v is Json => !!v && typeof v === "object" && !Array.isArray(v);
const str = (v: unknown): string | undefined => (typeof v === "string" && v.trim() ? v : undefined);

export interface ParsedVrm {
  meta: Omit<AvatarMeta, "thumbnail">;
  thumbnail?: { bytes: Uint8Array; mimeType: string };
}

const invalid = (why: string) => new AppError("AVATAR_INVALID", `VRM として読めません：${why}`);

export function parseGlb(bytes: Uint8Array): { json: Json; bin?: Uint8Array } {
  if (bytes.length < 20) throw invalid("ファイルが短すぎます");
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (view.getUint32(0, true) !== GLB_MAGIC) throw invalid("GLB 形式ではありません");
  if (view.getUint32(4, true) !== 2) throw invalid("glTF 2.0 ではありません");
  const total = Math.min(view.getUint32(8, true), bytes.length);
  let p = 12;
  let json: Json | undefined;
  let bin: Uint8Array | undefined;
  while (p + 8 <= total) {
    const len = view.getUint32(p, true);
    const type = view.getUint32(p + 4, true);
    const data = bytes.subarray(p + 8, p + 8 + len);
    if (type === CHUNK_JSON && !json) {
      try {
        json = JSON.parse(new TextDecoder().decode(data)) as Json;
      } catch {
        throw invalid("glTF の JSON が壊れています");
      }
    } else if (type === CHUNK_BIN && !bin) {
      bin = data;
    }
    p += 8 + len + ((4 - (len % 4)) % 4);
  }
  if (!json) throw invalid("glTF の JSON チャンクがありません");
  return { json, bin };
}

function imageBytes(json: Json, bin: Uint8Array | undefined, imageIndex: unknown): ParsedVrm["thumbnail"] {
  if (typeof imageIndex !== "number" || !bin) return undefined;
  const images = Array.isArray(json.images) ? json.images : [];
  const img = images[imageIndex];
  if (!isObj(img) || typeof img.bufferView !== "number") return undefined;
  const views = Array.isArray(json.bufferViews) ? json.bufferViews : [];
  const bv = views[img.bufferView];
  if (!isObj(bv) || typeof bv.byteLength !== "number") return undefined;
  const off = typeof bv.byteOffset === "number" ? bv.byteOffset : 0;
  if (off + bv.byteLength > bin.length) return undefined;
  return { bytes: bin.slice(off, off + bv.byteLength), mimeType: str(img.mimeType) ?? "image/png" };
}

function textureImage(json: Json, textureIndex: unknown): number | undefined {
  if (typeof textureIndex !== "number") return undefined;
  const textures = Array.isArray(json.textures) ? json.textures : [];
  const t = textures[textureIndex];
  return isObj(t) && typeof t.source === "number" ? t.source : undefined;
}

function missingBones(bones: Set<string>): string[] {
  return REQUIRED_BONES.filter((b) => !bones.has(b));
}

export function readVrm(bytes: Uint8Array): ParsedVrm {
  const { json, bin } = parseGlb(bytes);
  const ext = isObj(json.extensions) ? json.extensions : {};

  // VRM 1.0
  if (isObj(ext.VRMC_vrm)) {
    const vrm = ext.VRMC_vrm;
    const meta = isObj(vrm.meta) ? vrm.meta : {};
    const humanBones = isObj(vrm.humanoid) && isObj(vrm.humanoid.humanBones) ? vrm.humanoid.humanBones : {};
    const missing = missingBones(new Set(Object.keys(humanBones).filter((k) => isObj(humanBones[k]))));
    if (missing.length) throw new AppError("AVATAR_INVALID", "必要なボーンがありません", { detail: { missing } });
    return {
      meta: {
        name: str(meta.name) ?? "",
        authors: Array.isArray(meta.authors) ? meta.authors.filter((a): a is string => typeof a === "string") : [],
        version: "1.0",
        license: {
          url: str(meta.licenseUrl),
          name: str(meta.licenseUrl)?.includes("vrm.dev/licenses/1.0") ? "VRM Public License 1.0" : undefined,
          commercial: str(meta.commercialUsage),
          avatarPermission: str(meta.avatarPermission),
          redistribution: typeof meta.allowRedistribution === "boolean" ? meta.allowRedistribution : undefined,
          modification: str(meta.modification),
        },
      },
      thumbnail: imageBytes(json, bin, meta.thumbnailImage),
    };
  }

  // VRM 0.x
  if (isObj(ext.VRM)) {
    const vrm = ext.VRM;
    const meta = isObj(vrm.meta) ? vrm.meta : {};
    const list = isObj(vrm.humanoid) && Array.isArray(vrm.humanoid.humanBones) ? vrm.humanoid.humanBones : [];
    const names = new Set(list.flatMap((b) => (isObj(b) && typeof b.bone === "string" && typeof b.node === "number" ? [b.bone] : [])));
    const missing = missingBones(names);
    if (missing.length) throw new AppError("AVATAR_INVALID", "必要なボーンがありません", { detail: { missing } });
    const licenseName = str(meta.licenseName);
    return {
      meta: {
        name: str(meta.title) ?? "",
        authors: str(meta.author) ? [str(meta.author)!] : [],
        version: "0.x",
        license: {
          name: licenseName === "Other" ? undefined : licenseName,
          url: str(meta.otherPermissionUrl) ?? str(meta.otherLicenseUrl),
          commercial: str(meta.commercialUssageName),
          avatarPermission: str(meta.allowedUserName),
          redistribution: licenseName === "Redistribution_Prohibited" ? false : undefined,
        },
      },
      thumbnail: imageBytes(json, bin, textureImage(json, meta.texture)),
    };
  }

  throw invalid("VRM の拡張（VRM / VRMC_vrm）がありません");
}
