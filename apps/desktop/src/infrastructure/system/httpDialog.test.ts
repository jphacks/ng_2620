import { describe, expect, it } from "vitest";
import { LocalApi, type LocalInfo } from "../local/localApi";
import { HttpDialog } from "./httpDialog";

const info: LocalInfo = {
  mode: "local",
  version: "0",
  apiVersion: 1,
  port: 1,
  spa: true,
  token: "tok",
  dirs: { projects: "/p", appData: "/a", config: "/c" },
};

function dialog(reply: (url: string, body: unknown) => Response) {
  const calls: { url: string; body: unknown; auth: string | null }[] = [];
  const f = (async (url: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body));
    calls.push({ url, body, auth: new Headers(init?.headers).get("Authorization") });
    return reply(url, body);
  }) as unknown as typeof fetch;
  return { d: new HttpDialog(new LocalApi(info, "", f)), calls };
}

const ok = (o: unknown) => new Response(JSON.stringify(o), { status: 200 });

describe("HttpDialog", () => {
  it("pickFiles：フィルターと複数選択を渡し、パスの配列を返す", async () => {
    const { d, calls } = dialog(() => ok({ paths: ["D:\\v\\a.mp4"] }));
    const r = await d.pickFiles({ filters: [{ name: "動画", extensions: ["mp4", "mov"] }], multiple: true });
    expect(r).toEqual(["D:\\v\\a.mp4"]);
    expect(calls[0]).toMatchObject({
      url: "/v1/local/dialog/open-files",
      auth: "Bearer tok",
      body: { filters: [{ name: "動画", extensions: ["mp4", "mov"] }], multiple: true },
    });
  });

  it("キャンセルは空配列／null（エラーにしない）", async () => {
    const { d } = dialog((url) => ok(url.endsWith("open-files") ? { paths: [] } : { path: null }));
    expect(await d.pickFiles({})).toEqual([]);
    expect(await d.pickDirectory()).toBeNull();
    expect(await d.saveFile({ defaultPath: "x.mp4" })).toBeNull();
  });

  it("pickDirectory／saveFile は既定のパスを渡す", async () => {
    const { d, calls } = dialog(() => ok({ path: "D:\\out" }));
    expect(await d.pickDirectory({ defaultPath: "D:\\" })).toBe("D:\\out");
    expect(await d.saveFile({ defaultPath: "D:\\out\\a.mp4", filters: [{ name: "MP4", extensions: ["mp4"] }] })).toBe(
      "D:\\out",
    );
    expect(calls.map((c) => c.url)).toEqual(["/v1/local/dialog/open-dir", "/v1/local/dialog/save"]);
    expect(calls[0]!.body).toEqual({ defaultPath: "D:\\" });
    expect(calls[1]!.body).toMatchObject({ defaultPath: "D:\\out\\a.mp4" });
  });

  it("サーバーのエラーコードが AppError になる（多重表示・ローカル専用・使えない環境）", async () => {
    for (const [status, code] of [
      [409, "DIALOG_BUSY"],
      [403, "DIALOG_LOCAL_ONLY"],
      [501, "DIALOG_UNAVAILABLE"],
    ] as const) {
      const { d } = dialog(() => new Response(JSON.stringify({ error: { code, message: "m" } }), { status }));
      await expect(d.pickFiles({})).rejects.toMatchObject({ code, status });
    }
  });
});
