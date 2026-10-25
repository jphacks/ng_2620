// ローカルアプリサーバー（services/gpu_server を VVLOG_LOCAL=1 で起動したもの、ADR-0015）への入口。
// 画面がこのサーバーから配信されているとき（同一オリジン）だけ、GET /v1/local/info がトークンを渡してくれる＝フルモード

import { AppError } from "@/domain/errors";
import { errorFromBody } from "../compute/HttpComputeBackend";

export interface LocalInfo {
  mode: "local";
  version: string;
  apiVersion: number;
  port: number;
  /** 画面のビルド成果物を配信しているか */
  spa: boolean;
  token: string;
  /** 許可されたフォルダ（projects は既定の保存先） */
  dirs: { projects: string; appData: string; config: string };
}

type Query = Record<string, string | number | boolean | undefined>;

interface RequestOpts {
  /** ページが閉じられても送り切る（保存用。本体が 64KiB までのとき） */
  keepalive?: boolean;
  query?: Query;
  json?: unknown;
  body?: BodyInit;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

/** ローカルサーバーが居ればその情報、居なければ（Pages など）null。トークンが無い応答は『別オリジンから見た』ものなので null */
export async function fetchLocalInfo(
  opts: { base?: string; fetchImpl?: typeof fetch; timeoutMs?: number } = {},
): Promise<LocalInfo | null> {
  const { base = "", fetchImpl = (i, n) => globalThis.fetch(i, n), timeoutMs = 1500 } = opts;
  try {
    const res = await fetchImpl(`${base}/v1/local/info`, { cache: "no-store", signal: AbortSignal.timeout(timeoutMs) });
    if (!res.ok) return null;
    const j = (await res.json()) as Partial<LocalInfo>;
    if (j?.mode !== "local" || typeof j.token !== "string" || !j.dirs) return null;
    return j as LocalInfo;
  } catch {
    return null;
  }
}

export class LocalApi {
  constructor(
    readonly info: LocalInfo,
    private readonly base = "",
    private readonly fetchImpl: typeof fetch = (i, n) => globalThis.fetch(i, n),
  ) {}

  url(path: string, query: Query = {}): string {
    const q = Object.entries(query)
      .filter(([, v]) => v !== undefined)
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
      .join("&");
    return `${this.base}/v1/local${path}${q ? `?${q}` : ""}`;
  }

  /** <img>・<video> に渡せる URL（ヘッダーを付けられないのでトークンをクエリに載せる） */
  rawUrl(fsPath: string): string {
    return this.url("/fs/raw", { path: fsPath, t: this.info.token });
  }

  async request(method: string, path: string, opts: RequestOpts = {}): Promise<Response> {
    const headers: Record<string, string> = { Authorization: `Bearer ${this.info.token}`, ...opts.headers };
    let body = opts.body;
    if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.json);
    }
    let res: Response;
    try {
      res = await this.fetchImpl(this.url(path, opts.query), {
        method,
        headers,
        body,
        signal: opts.signal,
        keepalive: opts.keepalive,
      });
    } catch (e) {
      if (opts.signal?.aborted) throw e;
      throw new AppError("NETWORK", `ローカルサーバーに届きません: ${e instanceof Error ? e.message : String(e)}`);
    }
    // 416 は『範囲が末尾の外』で、呼び出し側が空として扱う
    if (!res.ok && res.status !== 416) throw errorFromBody(res.status, await res.text().catch(() => ""));
    return res;
  }

  async json<T>(method: string, path: string, opts?: RequestOpts): Promise<T> {
    return (await (await this.request(method, path, opts)).json()) as T;
  }
}
