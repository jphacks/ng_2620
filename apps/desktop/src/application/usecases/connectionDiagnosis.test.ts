import { describe, expect, it, vi } from "vitest";
import type { ComputeProfile } from "@/domain/connection";
import { hasUploadConsent, isMixedContent } from "@/domain/connection";
import { AppError } from "@/domain/errors";
import type { ComputeBackendFactory, ComputeBackendPort } from "../ports/compute";
import type { AppSettings, SettingsPort } from "../ports/settings";
import { defaultSettings } from "../ports/settings";
import type { NetworkProbePort } from "../ports/system";
import { RecordUploadConsent, TestConnection } from "./connection";

const profile = (baseUrl: string, kind: "remote" | "local" = "remote"): ComputeProfile => ({
  id: "p",
  name: "自前 GPU",
  kind,
  baseUrl,
  tokenRef: "p",
});

function factory(health: () => Promise<unknown>): ComputeBackendFactory {
  const backend = { health, capabilities: async () => ({}) } as unknown as ComputeBackendPort;
  return { forProfile: () => backend, offline: () => backend } as unknown as ComputeBackendFactory;
}
const down = () => Promise.reject(new AppError("NETWORK", "Failed to fetch"));

function net(pageOrigin: string, reachable: boolean): NetworkProbePort & { calls: string[] } {
  const calls: string[] = [];
  return {
    pageOrigin,
    calls,
    reachable: async (u) => {
      calls.push(u);
      return reachable;
    },
  };
}

describe("つながらなかった理由の推定", () => {
  it("https のページから http:// の宛先：混在コンテンツ（宛先には問い合わせない）", async () => {
    const n = net("https://jphacks.github.io", true);
    const r = await new TestConnection({ compute: factory(down), net: n }).execute(profile("http://gpu.example:8765"));
    expect(r).toMatchObject({
      ok: false,
      failedStep: "reach",
      reason: "mixed_content",
      pageOrigin: "https://jphacks.github.io",
    });
    expect(n.calls).toEqual([]);
  });

  it("loopback の http:// は混在コンテンツ扱いにしない", async () => {
    expect(isMixedContent("https://x.github.io", "http://127.0.0.1:8765")).toBe(false);
    expect(isMixedContent("https://x.github.io", "http://localhost:8765")).toBe(false);
    expect(isMixedContent("http://localhost:1420", "http://gpu.example")).toBe(false);
    expect(isMixedContent("https://x.github.io", "https://gpu.example")).toBe(false);
    expect(isMixedContent("https://x.github.io", "http://gpu.example")).toBe(true);
  });

  it("no-cors ではつながる：CORS で拒否されている（許可に加えるオリジンを返す）", async () => {
    const n = net("https://me.github.io", true);
    const r = await new TestConnection({ compute: factory(down), net: n }).execute(profile("https://gpu.example/"));
    expect(r).toMatchObject({ failedStep: "reach", reason: "cors", pageOrigin: "https://me.github.io" });
    expect(n.calls).toEqual(["https://gpu.example/v1/health"]); // 末尾の / と /v1 を整えて問い合わせる
  });

  it("no-cors でもつながらない：届かない", async () => {
    const r = await new TestConnection({ compute: factory(down), net: net("https://me.github.io", false) }).execute(
      profile("https://gpu.example"),
    );
    expect(r).toMatchObject({ failedStep: "reach", reason: "unreachable" });
  });

  it("net が無い環境（テストなど）では理由なしのまま、従来どおり", async () => {
    const r = await new TestConnection({ compute: factory(down) }).execute(profile("https://gpu.example"));
    expect(r).toMatchObject({ ok: false, failedStep: "reach" });
    expect((r as { reason?: string }).reason).toBeUndefined();
  });

  it("問い合わせ自体が例外でも、理由は『届かない』で結果を返す", async () => {
    const bad: NetworkProbePort = {
      pageOrigin: "https://x.github.io",
      reachable: () => Promise.reject(new Error("boom")),
    };
    const r = await new TestConnection({ compute: factory(down), net: bad }).execute(profile("https://gpu.example"));
    expect(r).toMatchObject({ reason: "unreachable" });
  });

  it("つながったときは、推定のための問い合わせをしない", async () => {
    const n = net("https://x.github.io", true);
    const ok = await new TestConnection({
      compute: factory(async () => ({ status: "ok", version: "1", apiVersion: 1 })),
      net: n,
    }).execute(profile("https://gpu.example"));
    expect(ok.ok).toBe(true);
    expect(n.calls).toEqual([]);
  });
});

describe("動画を送ってよいという確認", () => {
  it("外部サーバーは確認が要る。この PC（local）・開発用は要らない", () => {
    expect(hasUploadConsent({}, profile("https://gpu.example"))).toBe(false);
    expect(hasUploadConsent(undefined, profile("https://gpu.example"))).toBe(false);
    expect(hasUploadConsent({}, profile("http://127.0.0.1:8765", "local"))).toBe(true);
    expect(hasUploadConsent({}, { ...profile("https://x"), devOnly: true })).toBe(true);
  });

  it("確認した宛先と同じ URL のときだけ有効（末尾の / や /v1 の違いは無視、別の URL なら確認し直し）", () => {
    const c = { p: "https://gpu.example" };
    expect(hasUploadConsent(c, profile("https://gpu.example"))).toBe(true);
    expect(hasUploadConsent(c, profile("https://gpu.example/"))).toBe(true);
    expect(hasUploadConsent(c, profile("https://gpu.example/v1"))).toBe(true);
    expect(hasUploadConsent(c, profile("https://evil.example"))).toBe(false);
  });

  it("RecordUploadConsent：宛先を正規化して記録し、他の設定とほかの確認を壊さない", async () => {
    let saved: AppSettings = {
      ...defaultSettings(),
      activeProfileId: "x",
      uploadConsents: { other: "https://o.example" },
    };
    const settings: SettingsPort = {
      load: async () => structuredClone(saved),
      save: vi.fn(async (s) => void (saved = s)),
    };
    const s = await new RecordUploadConsent({ settings }).execute({ id: "p", baseUrl: "https://gpu.example/" });
    expect(s.uploadConsents).toEqual({ other: "https://o.example", p: "https://gpu.example" });
    expect(saved.activeProfileId).toBe("x");
    expect(hasUploadConsent(saved.uploadConsents, profile("https://gpu.example"))).toBe(true);
  });
});
