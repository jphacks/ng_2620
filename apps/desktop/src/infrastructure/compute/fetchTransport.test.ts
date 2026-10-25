import { describe, expect, it, vi } from "vitest";
import { AppError } from "@/domain/errors";
import { MemoryFs } from "../fs/memoryFs";
import { HttpComputeBackend } from "./HttpComputeBackend";
import { fetchTransport, startFromContentRange, totalFromContentRange } from "./fetchTransport";

const DEST = "/mem/cache/job1/layers.zip";
const data = (n: number) => Uint8Array.from({ length: n }, (_, i) => (i * 31 + 7) & 0xff);

interface Fake {
  /** 何バイト目で接続を切るか（1 回ごとに使い切る） */
  cutAt?: number[];
  ignoreRange?: boolean;
  status?: number;
  rangeStartOffBy?: number;
  chunk?: number;
}

/** Range 対応の偽サーバー。応答の本体は小さな断片に分けて流す */
function fakeServer(body: Uint8Array, f: Fake = {}) {
  const calls: { range: string | null }[] = [];
  const cuts = [...(f.cutAt ?? [])];
  const fetchImpl = (async (_url: unknown, init?: RequestInit) => {
    const range = new Headers(init?.headers).get("Range");
    calls.push({ range });
    if (f.status && f.status >= 400) return new Response("denied", { status: f.status });
    let start = 0;
    let status = 200;
    const headers: Record<string, string> = {};
    const m = /^bytes=(\d+)-$/.exec(range ?? "");
    if (m && !f.ignoreRange) {
      start = Number(m[1]);
      if (start >= body.length) {
        return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${body.length}` } });
      }
      status = 206;
      headers["Content-Range"] = `bytes ${start + (f.rangeStartOffBy ?? 0)}-${body.length - 1}/${body.length}`;
    }
    const slice = body.subarray(start);
    headers["Content-Length"] = String(slice.length);
    const cut = cuts.shift();
    const step = f.chunk ?? 1000;
    let at = 0;
    const stream = new ReadableStream<Uint8Array>({
      pull(c) {
        if (cut !== undefined && at >= cut) return c.error(new TypeError("network error"));
        if (at >= slice.length) return c.close();
        const end = Math.min(at + step, slice.length, cut ?? Infinity);
        c.enqueue(slice.subarray(at, end));
        at = end;
      },
    });
    return new Response(stream, { status, headers });
  }) as typeof fetch;
  return { fetchImpl, calls };
}

function setup(body: Uint8Array, f?: Fake, opts: { flushBytes?: number; retries?: number } = {}) {
  const fs = new MemoryFs();
  const append = vi.spyOn(fs, "appendBytes");
  const srv = fakeServer(body, f);
  const t = fetchTransport(fs, { backoffMs: 0, sleep: async () => {}, fetchImpl: srv.fetchImpl, ...opts });
  return { fs, append, t, srv };
}

describe("Content-Range の読み取り", () => {
  it("開始位置と総量", () => {
    expect(startFromContentRange("bytes 100-199/1000")).toBe(100);
    expect(totalFromContentRange("bytes 100-199/1000")).toBe(1000);
    expect(totalFromContentRange("bytes */1000")).toBe(1000);
    expect(totalFromContentRange("bytes 0-1/*")).toBeNull();
    expect(totalFromContentRange(null)).toBeNull();
    expect(startFromContentRange(null)).toBeNull();
  });
});

describe("fetchTransport.download", () => {
  it("断片ごとに .part へ追記し、完了で dest に rename する（途中経過は .part、dest は完成まで現れない）", async () => {
    const body = data(10_000);
    const { fs, append, t } = setup(body, { chunk: 1000 }, { flushBytes: 3000 });
    const seen: string[] = [];
    const orig = fs.appendBytes.bind(fs);
    fs.appendBytes = async (p, b, e) => {
      seen.push(`${p}:${(await fs.exists(DEST)) ? "dest-exists" : "no-dest"}`);
      return orig(p, b, e);
    };
    await t.download("http://x/a", {}, DEST);
    expect(new Uint8Array(await fs.readBytes(DEST))).toEqual(body);
    expect(await fs.exists(`${DEST}.part`)).toBe(false);
    expect(append.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(seen.every((s) => s === `${DEST}.part:no-dest`)).toBe(true);
  });

  it("メモリに抱えるのは flushBytes 程度で、全体を一度に書かない", async () => {
    const body = data(50_000);
    const { append, t } = setup(body, { chunk: 1000 }, { flushBytes: 8000 });
    await t.download("http://x/a", {}, DEST);
    const sizes = append.mock.calls.map((c) => c[1].length);
    expect(Math.max(...sizes)).toBeLessThanOrEqual(8000 + 1000);
    expect(sizes.reduce((a, b) => a + b, 0)).toBe(50_000);
  });

  it("進捗は単調に増え、最後は total に達する", async () => {
    const body = data(9_000);
    const { t } = setup(body);
    const seen: [number, number | null][] = [];
    await t.download("http://x/a", {}, DEST, (r, total) => seen.push([r, total]));
    const received = seen.map((s) => s[0]);
    expect(received).toEqual([...received].sort((a, b) => a - b));
    expect(seen.at(-1)).toEqual([9_000, 9_000]);
    expect(seen.every((s) => s[1] === 9_000)).toBe(true);
  });

  it("途中で切れたら .part の続きから Range で再開する", async () => {
    const body = data(10_000);
    const { fs, t, srv } = setup(body, { cutAt: [4_000, 2_000] }, { flushBytes: 1000 });
    await t.download("http://x/a", { Authorization: "Bearer t" }, DEST);
    expect(new Uint8Array(await fs.readBytes(DEST))).toEqual(body);
    expect(srv.calls.map((c) => c.range)).toEqual([null, "bytes=4000-", "bytes=6000-"]);
  });

  it("前回の .part があれば、その続きから取る", async () => {
    const body = data(10_000);
    const { fs, t, srv } = setup(body);
    await fs.appendBytes(`${DEST}.part`, body.subarray(0, 6_000));
    await t.download("http://x/a", {}, DEST);
    expect(srv.calls.map((c) => c.range)).toEqual(["bytes=6000-"]);
    expect(new Uint8Array(await fs.readBytes(DEST))).toEqual(body);
  });

  it("サーバーが Range を無視して全体を返したら、.part を捨てて最初から", async () => {
    const body = data(10_000);
    const { fs, t } = setup(body, { ignoreRange: true });
    await fs.appendBytes(`${DEST}.part`, new Uint8Array(3_000).fill(9));
    await t.download("http://x/a", {}, DEST);
    expect(new Uint8Array(await fs.readBytes(DEST))).toEqual(body);
  });

  it("違う位置から返ってきた 206 は使わず、最初から取り直す", async () => {
    const body = data(10_000);
    const { fs, t, srv } = setup(body, { rangeStartOffBy: 5 });
    await fs.appendBytes(`${DEST}.part`, body.subarray(0, 4_000));
    // 1 回目は不正な開始位置 → .part を捨てて Range 無しで再取得（再取得の応答は正しい 200）
    await t.download("http://x/a", {}, DEST);
    expect(srv.calls.map((c) => c.range)).toEqual(["bytes=4000-", null]);
    expect(new Uint8Array(await fs.readBytes(DEST))).toEqual(body);
  });

  it("416：.part が既に全体と同じ長さなら、そのまま完成させる", async () => {
    const body = data(5_000);
    const { fs, t } = setup(body);
    await fs.appendBytes(`${DEST}.part`, body);
    await t.download("http://x/a", {}, DEST);
    expect(new Uint8Array(await fs.readBytes(DEST))).toEqual(body);
  });

  it("416：.part がサーバーのファイルより長いなら、捨てて最初から", async () => {
    const body = data(5_000);
    const { fs, t } = setup(body);
    await fs.appendBytes(`${DEST}.part`, new Uint8Array(8_000));
    await t.download("http://x/a", {}, DEST);
    expect(new Uint8Array(await fs.readBytes(DEST))).toEqual(body);
  });

  it("再試行の上限を超えたら失敗し、.part は残す（次回の続きのため）", async () => {
    const body = data(10_000);
    const { fs, t, srv } = setup(body, { cutAt: [1_000, 1_000, 1_000, 1_000, 1_000] }, { retries: 2, flushBytes: 500 });
    await expect(t.download("http://x/a", {}, DEST)).rejects.toThrow("network error");
    expect(srv.calls).toHaveLength(3);
    expect(await fs.exists(DEST)).toBe(false);
    expect((await fs.stat(`${DEST}.part`)).size).toBeGreaterThan(0);
  });

  it("HTTP エラーは再試行せず『HTTP <status> <本文>』で投げる（HttpComputeBackend が読む形）", async () => {
    const { t, srv } = setup(data(10), { status: 403 });
    await expect(t.download("http://x/a", {}, DEST)).rejects.toThrow("HTTP 403 denied");
    expect(srv.calls).toHaveLength(1);
  });

  it("総量が分かるのに足りないまま終わったら、途中終了として再試行する", async () => {
    const body = data(6_000);
    const { fs, t, srv } = setup(body, { cutAt: [2_500] }, { flushBytes: 500 });
    // Content-Length 付きで 2500 バイトで正常終了したように見せる（TypeError にしない）
    const real = srv.fetchImpl;
    let first = true;
    const trunc = (async (u: unknown, i?: RequestInit) => {
      const res = await real(u as string, i);
      if (!first) return res;
      first = false;
      const reader = res.body!.getReader();
      const got = await reader.read();
      return new Response(got.value, { status: 200, headers: { "Content-Length": "6000" } });
    }) as typeof fetch;
    const t2 = fetchTransport(fs, { backoffMs: 0, sleep: async () => {}, fetchImpl: trunc, flushBytes: 500 });
    await t2.download("http://x/a", {}, DEST);
    expect(new Uint8Array(await fs.readBytes(DEST))).toEqual(body);
    void t;
  });

  it("追記できない FS では、メモリに溜めて書く（互換）", async () => {
    const body = data(5_000);
    const fs = new MemoryFs();
    (fs as { appendBytes?: unknown }).appendBytes = undefined;
    const t = fetchTransport(fs, { fetchImpl: fakeServer(body).fetchImpl });
    await t.download("http://x/a", {}, DEST);
    expect(new Uint8Array(await fs.readBytes(DEST))).toEqual(body);
  });
});

describe("HttpComputeBackend.uploadChunk の再試行", () => {
  function backend(fetchImpl: typeof fetch, tries = 3) {
    const fs = new MemoryFs();
    const slept: number[] = [];
    const b = new HttpComputeBackend({
      baseUrl: "http://gpu.example",
      profileId: "p",
      getToken: async () => "tok",
      transport: { fetch: fetchImpl, download: async () => {}, readChunk: (p, o, l) => fs.readChunk(p, o, l) },
      uploadRetry: { tries, backoffMs: 100, sleep: async (ms) => void slept.push(ms) },
    });
    return { b, fs, slept };
  }

  it("通信エラーは同じ断片を送り直す（待ち時間は倍々）", async () => {
    let n = 0;
    const f = (async () => {
      if (++n < 3) throw new TypeError("Failed to fetch");
      return new Response("{}", { status: 200 });
    }) as typeof fetch;
    const { b, slept } = backend(f);
    await b.uploadChunk("u1", 0, new Uint8Array([1, 2, 3]));
    expect(n).toBe(3);
    expect(slept).toEqual([100, 200]);
  });

  it("上限を超えたら NETWORK で失敗する", async () => {
    const f = (async () => {
      throw new TypeError("Failed to fetch");
    }) as typeof fetch;
    const { b } = backend(f, 2);
    await expect(b.uploadChunk("u1", 0, new Uint8Array(1))).rejects.toMatchObject({ code: "NETWORK" });
  });

  it("サーバーが返したエラー（401/409/422 など）は再試行しない", async () => {
    let n = 0;
    const f = (async () => {
      n++;
      return new Response(JSON.stringify({ error: { code: "UNAUTHORIZED", message: "no" } }), { status: 401 });
    }) as typeof fetch;
    const { b, slept } = backend(f);
    const e = await b.uploadChunk("u1", 0, new Uint8Array(1)).catch((x: unknown) => x);
    expect(e).toBeInstanceOf(AppError);
    expect(n).toBe(1);
    expect(slept).toEqual([]);
  });

  it("ファイルの範囲を readChunk で読んで PUT する（丸ごと読まない）", async () => {
    const bodies: number[] = [];
    const f = (async (_u: unknown, init?: RequestInit) => {
      bodies.push((init?.body as Uint8Array).length);
      return new Response("{}", { status: 200 });
    }) as typeof fetch;
    const { b, fs } = backend(f);
    await fs.writeBytes("/mem/video.mp4", data(100));
    const spy = vi.spyOn(fs, "readChunk");
    await b.uploadChunk("u1", 1, { path: "/mem/video.mp4", offset: 40, length: 30 });
    expect(spy).toHaveBeenCalledWith("/mem/video.mp4", 40, 30);
    expect(bodies).toEqual([30]);
  });
});
