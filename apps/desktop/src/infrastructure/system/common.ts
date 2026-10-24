// 時計と ID

import type { ClockPort, IdPort } from "@/application/ports/system";

export const systemClock: ClockPort = {
  now: () => new Date(),
  sleep: (ms, signal) =>
    new Promise<void>((resolve, reject) => {
      if (signal?.aborted) return reject(signal.reason);
      const t = setTimeout(resolve, ms);
      signal?.addEventListener(
        "abort",
        () => {
          clearTimeout(t);
          reject(signal.reason);
        },
        { once: true },
      );
    }),
};

export const randomIds: IdPort = {
  newId(prefix: string) {
    const bytes = crypto.getRandomValues(new Uint8Array(8));
    return `${prefix}_${Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;
  },
};
