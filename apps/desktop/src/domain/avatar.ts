// アバター（VRM）とライセンス表示（F07、contracts §7）

export interface AvatarLicense {
  name?: string; // 0.x licenseName / 1.0 licenseUrl から推定した名前
  url?: string;
  commercial?: string; // 0.x commercialUssageName / 1.0 commercialUsage（生の値）
  avatarPermission?: string; // 0.x allowedUserName / 1.0 avatarPermission
  redistribution?: boolean;
  modification?: string; // 1.0 modification（アプリ表示用の追加項目）
}

export interface AvatarMeta {
  name: string;
  authors: string[];
  version: "0.x" | "1.0";
  license: AvatarLicense;
  /** glTF 内のサムネイル画像（ライブラリでは取り出して avatars/<sha>.<ext> に置き、そのパス） */
  thumbnail?: string;
}

export interface Avatar {
  avatarId: string; // av_<sha256 先頭 16>
  sha256: string;
  file: string; // ライブラリ内のコピーの絶対パス
  meta: AvatarMeta;
  importedAt: string;
}

export type LicenseBadge = "commercial_ok" | "commercial_ng" | "unknown";

export function avatarIdFromSha256(sha256: string): string {
  return `av_${sha256.toLowerCase().slice(0, 16)}`;
}

const COMMERCIAL_OK = new Set(["allow", "personalprofit", "corporation"]);
const COMMERCIAL_NG = new Set(["disallow", "personalnonprofit"]);

export function licenseBadge(meta: AvatarMeta): LicenseBadge {
  const c = meta.license.commercial?.toLowerCase();
  if (c && COMMERCIAL_OK.has(c)) return "commercial_ok";
  if (c && COMMERCIAL_NG.has(c)) return "commercial_ng";
  // 0.x の licenseName が CC 系なら名前から判断できる
  const name = meta.license.name?.toUpperCase().replace(/[-\s]/g, "_");
  if (name) {
    if (name.includes("NC")) return "commercial_ng";
    if (name === "CC0" || name.startsWith("CC_BY")) return "commercial_ok";
  }
  return "unknown";
}

/** 改変可否：true = 可、false = 不可、null = 不明 */
export function modificationAllowed(meta: AvatarMeta): boolean | null {
  const m = meta.license.modification?.toLowerCase();
  if (m === "prohibited") return false;
  if (m === "allowmodification" || m === "allowmodificationredistribution") return true;
  const name = meta.license.name?.toUpperCase().replace(/[-\s]/g, "_");
  if (name?.includes("ND")) return false;
  if (name === "CC0" || name?.startsWith("CC_BY")) return true;
  return null;
}
