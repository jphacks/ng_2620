// WeightsPort のフルモード実装（/v1/local/weights、#33）

import type { WeightsPort } from "@/application/ports/weights";
import type { WeightsJob, WeightsStatus } from "@/domain/weights";
import { readSse } from "../compute/sse";
import type { LocalApi } from "../local/localApi";

export class HttpWeights implements WeightsPort {
  constructor(private readonly api: LocalApi) {}

  status() {
    return this.api.json<WeightsStatus>("GET", "/weights");
  }

  start(hfToken?: string) {
    const token = hfToken?.trim();
    return this.api.json<WeightsJob>("POST", "/weights/start", { json: token ? { hfToken: token } : {} });
  }

  async cancel() {
    await this.api.request("POST", "/weights/cancel");
  }

  async watch(onJob: (job: WeightsJob) => void, signal?: AbortSignal) {
    const res = await this.api.request("GET", "/weights/events", { headers: { Accept: "text/event-stream" }, signal });
    if (!res.body) return null;
    let last: WeightsJob | null = null;
    for await (const m of readSse(res.body, { idleTimeoutMs: 60_000 })) {
      if (m.event !== "progress" && m.event !== "done") continue;
      last = JSON.parse(m.data) as WeightsJob;
      onJob(last);
      if (m.event === "done") break;
    }
    return last;
  }
}
