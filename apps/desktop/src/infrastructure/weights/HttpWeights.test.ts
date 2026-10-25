import { describe, expect, it } from "vitest";
import { weightsFraction } from "@/domain/weights";
import { LocalApi, type LocalInfo } from "../local/localApi";
import { HttpWeights } from "./HttpWeights";

const info: LocalInfo = {
  mode: "local",
  version: "0",
  apiVersion: 1,
  port: 1,
  spa: true,
  token: "tok",
  dirs: { projects: "/p", appData: "/a", config: "/c" },
};

function make(reply: (url: string, init?: RequestInit) => Response) {
  const calls: { url: string; method?: string; body?: string; auth: string | null }[] = [];
  const f = (async (url: string, init?: RequestInit) => {
    calls.push({
      url,
      method: init?.method,
      body: init?.body as string | undefined,
      auth: new Headers(init?.headers).get("Authorization"),
    });
    return reply(url, init);
  }) as unknown as typeof fetch;
  return { w: new HttpWeights(new LocalApi(info, "", f)), calls };
}

describe("HttpWeights", () => {
  it("status：GET /v1/local/weights", async () => {
    const { w, calls } = make(() => new Response(JSON.stringify({ allReady: false, files: [], job: { state: "idle" } })));
    expect((await w.status()).allReady).toBe(false);
    expect(calls[0]).toMatchObject({ url: "/v1/local/weights", auth: "Bearer tok" });
  });

  it("start：トークンは本文にだけ載せる（URL・空白）", async () => {
    const { w, calls } = make(() => new Response(JSON.stringify({ state: "running" }), { status: 202 }));
    await w.start("  hf_abc  ");
    expect(calls[0].url).toBe("/v1/local/weights/start");
    expect(calls[0].url).not.toContain("hf_abc");
    expect(JSON.parse(calls[0].body!)).toEqual({ hfToken: "hf_abc" });
  });

  it("start：トークンが空なら本文に hfToken を入れない", async () => {
    const { w, calls } = make(() => new Response("{}", { status: 202 }));
    await w.start("  ");
    expect(JSON.parse(calls[0].body!)).toEqual({});
  });

  it("start：実行中は WEIGHTS_BUSY のエラーになる", async () => {
    const { w } = make(
      () => new Response(JSON.stringify({ error: { code: "WEIGHTS_BUSY", message: "実行中" } }), { status: 409 }),
    );
    await expect(w.start()).rejects.toMatchObject({ code: "WEIGHTS_BUSY" });
  });

  it("watch：SSE の progress を順に渡し、done で終わる", async () => {
    const sse =
      `event: progress\ndata: {"state":"running","fileIndex":0,"fileCount":2,"bytesDone":5,"bytesTotal":10}\n\n` +
      `event: done\ndata: {"state":"succeeded"}\n\n`;
    const { w, calls } = make(() => new Response(sse, { headers: { "Content-Type": "text/event-stream" } }));
    const seen: string[] = [];
    const last = await w.watch((j) => seen.push(j.state));
    expect(seen).toEqual(["running", "succeeded"]);
    expect(last?.state).toBe("succeeded");
    expect(calls[0].url).toBe("/v1/local/weights/events");
  });

  it("cancel：POST /cancel", async () => {
    const { w, calls } = make(() => new Response("{}"));
    await w.cancel();
    expect(calls[0]).toMatchObject({ url: "/v1/local/weights/cancel", method: "POST" });
  });
});

describe("weightsFraction", () => {
  it("ファイル数と今のファイルの割合から全体を出す", () => {
    expect(weightsFraction({ state: "running", fileIndex: 1, fileCount: 4, bytesDone: 50, bytesTotal: 100 })).toBeCloseTo(0.375);
    expect(weightsFraction({ state: "running", fileIndex: 0, fileCount: 4 })).toBe(0);
    expect(weightsFraction({ state: "succeeded" })).toBe(1);
    expect(weightsFraction({ state: "idle" })).toBeNull();
  });
});
