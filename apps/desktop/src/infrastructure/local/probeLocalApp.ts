// Pages などの https のページから、この PC のローカルアプリが動いているかを探す（#38、#28 の決定）。
// 自動では切り替えない：許可（Chrome のローカルネットワークアクセス）の確認や、Safari の混在コンテンツの制限があるので、
// 利用者が押したときだけ探し、見つかったらローカル版の URL を開いてもらう。CORS には頼らない（no-cors で『つながったか』だけ見る）

import type { LocalAppPort, LocalAppProbe } from "@/application/ports/system";

export const DEFAULT_LOCAL_PORT = 8765;

export function localAppUrl(port = DEFAULT_LOCAL_PORT): string {
  return `http://127.0.0.1:${port}/`;
}

/** 先に答えた（つながった）ポートを返す。HTTP の応答が何であれ（403 でも）サーバーは居る。ブラウザが止めたときも false */
export async function probeLocalApp(
  opts: { ports?: number[]; timeoutMs?: number; fetchImpl?: typeof fetch } = {},
): Promise<LocalAppProbe> {
  const { ports = [DEFAULT_LOCAL_PORT], timeoutMs = 1500, fetchImpl = (i, n) => globalThis.fetch(i, n) } = opts;
  for (const port of ports) {
    try {
      await fetchImpl(`http://127.0.0.1:${port}/v1/health`, {
        mode: "no-cors",
        cache: "no-store",
        signal: AbortSignal.timeout(timeoutMs),
      });
      return { found: true, url: localAppUrl(port) };
    } catch {
      // つながらない（動いていない／許可されていない／ブラウザが混在コンテンツとして止めた）
    }
  }
  return { found: false };
}

/** https のページから http://127.0.0.1 を探せるブラウザか。Chromium 系（Chrome・Edge）だけ。Safari と Firefox は混在コンテンツとして止めるので、案内リンクだけにする */
export function canProbeFromHttps(ua: string): boolean {
  return /Chrome\//.test(ua);
}

/** 制限モードを https で開いているときの、ローカルアプリの探索と案内 */
export function createLocalApp(ua = navigator.userAgent): LocalAppPort {
  return { url: localAppUrl(), canProbe: canProbeFromHttps(ua), probe: () => probeLocalApp() };
}
