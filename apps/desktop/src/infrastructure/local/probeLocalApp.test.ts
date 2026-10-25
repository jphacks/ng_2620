import { describe, expect, it, vi } from "vitest";
import { canProbeFromHttps, createLocalApp, localAppUrl, probeLocalApp } from "./probeLocalApp";

const CHROME =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";
const EDGE = `${CHROME} Edg/131.0.0.0`;
const SAFARI =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15";
const FIREFOX = "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0";

describe("probeLocalApp", () => {
  it("つながれば（応答が何であれ）見つかった。CORS に頼らず no-cors で呼ぶ", async () => {
    const f = vi.fn(async () => new Response(null, { status: 403 })) as unknown as typeof fetch;
    expect(await probeLocalApp({ fetchImpl: f })).toEqual({ found: true, url: "http://127.0.0.1:8765/" });
    const [url, init] = (f as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls[0]!;
    expect(url).toBe("http://127.0.0.1:8765/v1/health");
    expect(init).toMatchObject({ mode: "no-cors", cache: "no-store" });
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("つながらなければ（動いていない・ブラウザが止めた）見つからない", async () => {
    const f = (async () => {
      throw new TypeError("Failed to fetch");
    }) as typeof fetch;
    expect(await probeLocalApp({ fetchImpl: f })).toEqual({ found: false });
  });

  it("複数のポートは順に試し、最初につながったものを返す", async () => {
    const tried: string[] = [];
    const f = (async (u: string) => {
      tried.push(u);
      if (!u.includes(":8766")) throw new TypeError("x");
      return new Response(null);
    }) as unknown as typeof fetch;
    expect(await probeLocalApp({ ports: [8765, 8766, 8767], fetchImpl: f })).toEqual({
      found: true,
      url: "http://127.0.0.1:8766/",
    });
    expect(tried).toHaveLength(2);
  });

  it("時間切れも見つからない扱い（待ち続けない）", async () => {
    const f = ((_u: string, init: RequestInit) =>
      new Promise((_, reject) =>
        init.signal!.addEventListener("abort", () => reject(new DOMException("t", "TimeoutError"))),
      )) as unknown as typeof fetch;
    const t0 = Date.now();
    expect(await probeLocalApp({ fetchImpl: f, timeoutMs: 50 })).toEqual({ found: false });
    expect(Date.now() - t0).toBeLessThan(1000);
  });
});

describe("探せるブラウザの判定", () => {
  it("Chrome・Edge は探せる。Safari・Firefox は案内リンクだけ（#28 の決定）", () => {
    expect(canProbeFromHttps(CHROME)).toBe(true);
    expect(canProbeFromHttps(EDGE)).toBe(true);
    expect(canProbeFromHttps(SAFARI)).toBe(false);
    expect(canProbeFromHttps(FIREFOX)).toBe(false);
    expect(createLocalApp(SAFARI)).toMatchObject({ url: localAppUrl(), canProbe: false });
    expect(createLocalApp(CHROME).canProbe).toBe(true);
  });
});
