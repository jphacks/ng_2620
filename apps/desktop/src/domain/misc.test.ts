import { describe, expect, it } from "vitest";
import { avatarIdFromSha256, licenseBadge, modificationAllowed } from "./avatar";
import type { AvatarMeta } from "./avatar";
import {
  initialConnection,
  isInsecureRemote,
  nextConnectionState,
  normalizeBaseUrl,
  validateProfile,
} from "./connection";
import { chunkCount, chunkRange, missingChunks, receivedBytes } from "./upload";

describe("upload", () => {
  const MB = 1024 * 1024;
  it("8MB で区切り、最後だけ短い", () => {
    expect(chunkCount(20 * MB, 8 * MB)).toBe(3);
    expect(chunkCount(0, 8 * MB)).toBe(1);
    expect(chunkRange(2, 20 * MB, 8 * MB)).toEqual({ offset: 16 * MB, length: 4 * MB });
  });
  it("受信済みを除いた断片だけ送る", () => {
    expect(missingChunks(5, [0, 2, 2, 4])).toEqual([1, 3]);
    expect(receivedBytes([0, 2], 20 * MB, 8 * MB)).toBe(12 * MB);
  });
});

describe("connection", () => {
  it("失敗 2 回で再接続中、成功で接続", () => {
    let s = initialConnection("remote-win");
    s = nextConnectionState(s, true);
    expect(s.status).toBe("connected");
    s = nextConnectionState(s, false);
    expect(s.status).toBe("connected");
    s = nextConnectionState(s, false);
    expect(s.status).toBe("reconnecting");
    s = nextConnectionState(s, true);
    expect(s).toMatchObject({ status: "connected", failures: 0 });
  });
  it("一度もつながっていなければオフライン", () => {
    expect(nextConnectionState(initialConnection("x"), false).status).toBe("offline");
    expect(initialConnection(null).status).toBe("offline");
  });
  it("リモートの http:// は警告（loopback は除く）", () => {
    expect(isInsecureRemote({ kind: "remote", baseUrl: "http://gpu.example.ts.net" })).toBe(true);
    expect(isInsecureRemote({ kind: "remote", baseUrl: "https://gpu.example.ts.net" })).toBe(false);
    expect(isInsecureRemote({ kind: "remote", baseUrl: "http://127.0.0.1:8765" })).toBe(false);
    expect(isInsecureRemote({ kind: "remote", baseUrl: "http://localhost:8765" })).toBe(false);
    expect(isInsecureRemote({ kind: "local", baseUrl: "http://192.168.0.2:8765" })).toBe(false);
  });
  it("URL の正規化と検証", () => {
    expect(normalizeBaseUrl(" https://gpu.example.ts.net/v1/ ")).toBe("https://gpu.example.ts.net");
    expect(validateProfile({ name: "", baseUrl: "ftp://x" })).toEqual(["name_required", "url_invalid"]);
    expect(validateProfile({ name: "a", baseUrl: "http://127.0.0.1:8765" })).toEqual([]);
  });
});

describe("avatar", () => {
  const meta = (license: AvatarMeta["license"]): AvatarMeta => ({ name: "A", authors: [], version: "1.0", license });
  it("ID は sha256 の先頭 16 桁", () => {
    expect(avatarIdFromSha256("9A0C1234567890ABCDEF00")).toBe("av_9a0c1234567890ab");
  });
  it("商用可否のバッジ", () => {
    expect(licenseBadge(meta({ commercial: "corporation" }))).toBe("commercial_ok");
    expect(licenseBadge(meta({ commercial: "personalNonProfit" }))).toBe("commercial_ng");
    expect(licenseBadge(meta({ commercial: "Allow" }))).toBe("commercial_ok");
    expect(licenseBadge(meta({ commercial: "Disallow" }))).toBe("commercial_ng");
    expect(licenseBadge(meta({ name: "CC0" }))).toBe("commercial_ok");
    expect(licenseBadge(meta({ name: "CC_BY_NC" }))).toBe("commercial_ng");
    expect(licenseBadge(meta({}))).toBe("unknown");
  });
  it("改変可否", () => {
    expect(modificationAllowed(meta({ modification: "prohibited" }))).toBe(false);
    expect(modificationAllowed(meta({ modification: "allowModification" }))).toBe(true);
    expect(modificationAllowed(meta({ name: "CC_BY_ND" }))).toBe(false);
    expect(modificationAllowed(meta({}))).toBeNull();
  });
});
