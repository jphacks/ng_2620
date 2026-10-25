// 接続プロファイルと接続状態（F03、ADR-0002 / 0012）

export type ProfileKind = "local" | "remote";

export interface ComputeProfile {
  id: string;
  name: string;
  kind: ProfileKind;
  baseUrl: string; // /v1 を含まない
  tokenRef: string; // SecretStorePort のキー
  /** 開発ビルドだけで出すもの（開発用モックなど） */
  devOnly?: boolean;
}

export type ConnectionStatus = "checking" | "connected" | "reconnecting" | "offline";

export interface ConnectionState {
  status: ConnectionStatus;
  profileId: string | null;
  failures: number; // 連続失敗回数
  everConnected: boolean;
  lastCheckedAt?: string;
}

/** 失敗何回で「再接続中」にするか（F03：30 秒ごと、失敗 2 回で再接続中） */
export const RECONNECT_AFTER_FAILURES = 2;
export const HEALTH_INTERVAL_MS = 30_000;
export const SUPPORTED_API_VERSION = 1;

export function initialConnection(profileId: string | null): ConnectionState {
  return { status: profileId ? "checking" : "offline", profileId, failures: 0, everConnected: false };
}

export function nextConnectionState(prev: ConnectionState, ok: boolean, at?: string): ConnectionState {
  if (ok) return { ...prev, status: "connected", failures: 0, everConnected: true, lastCheckedAt: at };
  const failures = prev.failures + 1;
  let status: ConnectionStatus;
  if (!prev.everConnected) status = "offline";
  else status = failures >= RECONNECT_AFTER_FAILURES ? "reconnecting" : "connected";
  return { ...prev, status, failures, lastCheckedAt: at };
}

export function normalizeBaseUrl(url: string): string {
  let u = url.trim();
  while (u.endsWith("/")) u = u.slice(0, -1);
  if (u.endsWith("/v1")) u = u.slice(0, -3);
  return u;
}

export function isLoopbackUrl(url: string): boolean {
  const m = /^[a-z]+:\/\/(\[[^\]]+\]|[^/:?#]+)/i.exec(url.trim());
  const host = (m?.[1] ?? "").toLowerCase();
  return host === "localhost" || host === "[::1]" || /^127\.\d+\.\d+\.\d+$/.test(host);
}

/** リモートで http://（非 TLS）を使おうとしている（127.0.0.1 / localhost は除く） */
export function isInsecureRemote(p: Pick<ComputeProfile, "kind" | "baseUrl">): boolean {
  return p.kind === "remote" && /^http:\/\//i.test(p.baseUrl.trim()) && !isLoopbackUrl(p.baseUrl);
}

export type ProfileProblem = "name_required" | "url_invalid";

export function validateProfile(p: Pick<ComputeProfile, "name" | "baseUrl">): ProfileProblem[] {
  const out: ProfileProblem[] = [];
  if (!p.name.trim()) out.push("name_required");
  if (!/^(https?|memory):\/\/[^\s]+$/i.test(p.baseUrl.trim())) out.push("url_invalid");
  return out;
}

/** フルモードで、この画面を配信しているローカルサーバー自身につなぐプロファイルの ID（トークンも同じキーで保存） */
export const LOCAL_PROFILE_ID = "local-gpu";

export function localProfile(baseUrl: string, name: string): ComputeProfile {
  return { id: LOCAL_PROFILE_ID, name, kind: "local", baseUrl, tokenRef: LOCAL_PROFILE_ID };
}
