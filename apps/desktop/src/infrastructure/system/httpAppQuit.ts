// アプリの終了：ローカルサーバーに POST /v1/local/shutdown（同一オリジン＋トークン。ランチャーがサーバーの終了を見て片付ける、#31）

import type { AppQuitPort } from "@/application/ports/system";
import type { LocalApi } from "../local/localApi";

export class HttpAppQuit implements AppQuitPort {
  constructor(private readonly api: LocalApi) {}

  async quit() {
    await this.api.request("POST", "/shutdown");
  }
}
