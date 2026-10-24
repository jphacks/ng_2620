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

const hex = (bytes: Uint8Array) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");

export const randomIds: IdPort = {
  newId(prefix: string) {
    return `${prefix}_${hex(crypto.getRandomValues(new Uint8Array(8)))}`;
  },
};

/** この PC の GPU サーバーの API トークン（48 桁の 16 進） */
export function randomToken(): string {
  return hex(crypto.getRandomValues(new Uint8Array(24)));
}
