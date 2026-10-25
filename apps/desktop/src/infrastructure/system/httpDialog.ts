// DialogPort のフルモード実装。ローカルアプリサーバーが Windows のネイティブダイアログを出し、実パスを返す（#46、#28 の決定）。
// 選んだファイルとフォルダは、サーバーが以後の /v1/local/fs の許可対象にする

import type { DialogPort } from "@/application/ports/system";
import type { LocalApi } from "../local/localApi";

export class HttpDialog implements DialogPort {
  constructor(private readonly api: LocalApi) {}

  async pickFiles({ filters, multiple }: Parameters<DialogPort["pickFiles"]>[0]) {
    const r = await this.api.json<{ paths: string[] }>("POST", "/dialog/open-files", {
      json: { filters, multiple: !!multiple },
    });
    return r.paths;
  }

  async pickDirectory(opts?: { defaultPath?: string }) {
    const r = await this.api.json<{ path: string | null }>("POST", "/dialog/open-dir", {
      json: { defaultPath: opts?.defaultPath },
    });
    return r.path;
  }

  async saveFile({ defaultPath, filters }: Parameters<DialogPort["saveFile"]>[0]) {
    const r = await this.api.json<{ path: string | null }>("POST", "/dialog/save", { json: { defaultPath, filters } });
    return r.path;
  }
}
