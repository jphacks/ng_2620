import { describe, expect, it } from "vitest";
import { AppError } from "@/domain/errors";
import type { SseMessage } from "./sse";
import { resilientSse, SseParser } from "./sse";

function streamOf(chunks: (string | Error)[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  let i = 0;
  return new ReadableStream({
    pull(c) {
      const next = chunks[i++];
      if (next === undefined) c.close();
      else if (next instanceof Error) c.error(next);
      else c.enqueue(enc.encode(next));
    },
  });
}

describe("SseParser", () => {
  it("断片の境界・改行コード・複数行 data・コメントを扱う", () => {
    const p = new SseParser();
    const out: SseMessage[] = [];
    for (const chunk of [
      ": hello\n",
      "id: 1\r",
      '\nevent: progress\ndata: {"a":',
      "1}\n\n",
      "id: 2\nevent: x\ndata: l1\ndata: l2\r\n\r\n",
      "data: no-event\n\n",
    ]) {
      out.push(...p.push(chunk));
    }
    expect(out).toEqual([
      { id: "1", event: "progress", data: '{"a":1}' },
      { id: "2", event: "x", data: "l1\nl2" },
      { id: "2", event: "message", data: "no-event" }, // id は前のものを引き継ぐ
    ]);
  });
});

describe("resilientSse", () => {
  it("途中で切れたら Last-Event-ID で再開し、重複・欠落なく終了まで届く", async () => {
    const opened: (string | undefined)[] = [];
    const ev = (id: number, type: string) => `id: ${id}\nevent: ${type}\ndata: {}\n\n`;
    const responses = [
      () => new Response(streamOf([ev(1, "snapshot"), ev(2, "progress"), new Error("connection reset")])),
      () => new Response(streamOf([ev(3, "progress"), ev(4, "job_finished"), ev(5, "heartbeat")])),
    ];
    const got: string[] = [];
    const retries: number[] = [];
    for await (const m of resilientSse({
      open: async (lastId) => {
        opened.push(lastId);
        return responses.shift()!();
      },
      isTerminal: (m) => m.event === "job_finished",
      sleep: async () => {},
      onRetry: (n) => retries.push(n),
    })) {
      got.push(`${m.id}:${m.event}`);
    }
    expect(opened).toEqual([undefined, "2"]);
    expect(got).toEqual(["1:snapshot", "2:progress", "3:progress", "4:job_finished"]);
    expect(retries).toEqual([1]);
  });

  it("401 は再接続せずに投げる", async () => {
    let opens = 0;
    const it = resilientSse({
      open: async () => {
        opens++;
        return new Response("{}", { status: 401 });
      },
      toError: async () => new AppError("UNAUTHORIZED", "token", { status: 401 }),
      isTerminal: () => false,
      sleep: async () => {},
    });
    await expect(it.next()).rejects.toMatchObject({ code: "UNAUTHORIZED" });
    expect(opens).toBe(1);
  });

  it("失敗が続けば諦める（呼び出し側がポーリングに落とす）", async () => {
    let opens = 0;
    const it = resilientSse({
      open: async () => {
        opens++;
        throw new TypeError("fetch failed");
      },
      isTerminal: () => false,
      maxRetries: 2,
      sleep: async () => {},
    });
    await expect(it.next()).rejects.toThrow("fetch failed");
    expect(opens).toBe(3);
  });
});
