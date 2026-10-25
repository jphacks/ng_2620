// BootstrapPort のフルモード実装（/v1/local/bootstrap、#32）

import type { BootstrapPort } from "@/application/ports/bootstrap";
import type { BootstrapJob, BootstrapStatus } from "@/domain/bootstrap";
import { readSse } from "../compute/sse";
import type { LocalApi } from "../local/localApi";

export class HttpBootstrap implements BootstrapPort {
  constructor(private readonly api: LocalApi) {}

  status() {
    return this.api.json<BootstrapStatus>("GET", "/bootstrap");
  }

  start() {
    return this.api.json<BootstrapJob>("POST", "/bootstrap/start", { json: {} });
  }

  async cancel() {
    await this.api.request("POST", "/bootstrap/cancel");
  }

  async watch(onJob: (job: BootstrapJob) => void, signal?: AbortSignal) {
    const res = await this.api.request("GET", "/bootstrap/events", {
      headers: { Accept: "text/event-stream" },
      signal,
    });
    if (!res.body) return null;
    let last: BootstrapJob | null = null;
    for await (const m of readSse(res.body, { idleTimeoutMs: 60_000 })) {
      if (m.event !== "progress" && m.event !== "done") continue;
      last = JSON.parse(m.data) as BootstrapJob;
      onJob(last);
      if (m.event === "done") break;
    }
    return last;
  }
}
