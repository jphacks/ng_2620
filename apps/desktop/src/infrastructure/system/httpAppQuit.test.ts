import { describe, expect, it, vi } from "vitest";
import { LocalApi, type LocalInfo } from "../local/localApi";
import { HttpAppQuit } from "./httpAppQuit";

const info: LocalInfo = {
  mode: "local",
  version: "0",
  apiVersion: 1,
  port: 8765,
  spa: true,
  token: "tok",
  dirs: { projects: "/p", appData: "/a", config: "/c" },
};
const api = (f: unknown) => new LocalApi(info, "", f as typeof fetch);

describe("HttpAppQuit", () => {
  it("POST /v1/local/shutdown をトークン付きで呼ぶ", async () => {
    const fetchImpl = vi.fn(
      async (_u: string, _i: RequestInit) => new Response('{"status":"shutting-down"}', { status: 202 }),
    );
    await new HttpAppQuit(api(fetchImpl)).quit();
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("/v1/local/shutdown");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok");
  });

  it("サーバーが拒否したら例外にする", async () => {
    const fetchImpl = vi.fn(
      async () => new Response('{"error":{"code":"SHUTDOWN_LOCAL_ONLY","message":"x"}}', { status: 403 }),
    );
    await expect(new HttpAppQuit(api(fetchImpl)).quit()).rejects.toThrow();
  });
});
