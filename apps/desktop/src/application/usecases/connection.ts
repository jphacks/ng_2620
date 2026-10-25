// F03 接続プロファイルと疎通確認

import type { ComputeProfile, ConnectionState, ReachReason } from "@/domain/connection";
import {
  HEALTH_INTERVAL_MS,
  initialConnection,
  isMixedContent,
  localProfile,
  nextConnectionState,
  normalizeBaseUrl,
  SUPPORTED_API_VERSION,
} from "@/domain/connection";
import { AppError, toAppError } from "@/domain/errors";
import type { Capabilities, ComputeBackendFactory, ComputeBackendPort, Health } from "../ports/compute";
import type { AppSettings, SecretStorePort, SettingsPort } from "../ports/settings";
import type { ClockPort, NetworkProbePort } from "../ports/system";

export class ListProfiles {
  constructor(private readonly d: { settings: SettingsPort }) {}
  async execute(): Promise<Pick<AppSettings, "profiles" | "activeProfileId" | "uploadConsents">> {
    const s = await this.d.settings.load();
    return { profiles: s.profiles, activeProfileId: s.activeProfileId, uploadConsents: s.uploadConsents };
  }
}

export class SaveProfile {
  constructor(private readonly d: { settings: SettingsPort; secrets: SecretStorePort }) {}

  /** 追加・更新。token を渡したときだけトークンを書き換える。既定が無ければこれを既定にする */
  async execute(input: { profile: ComputeProfile; token?: string }): Promise<AppSettings> {
    const { settings, secrets } = this.d;
    const profile = {
      ...input.profile,
      name: input.profile.name.trim(),
      baseUrl: normalizeBaseUrl(input.profile.baseUrl),
    };
    if (input.token !== undefined) await secrets.set(profile.tokenRef, input.token);
    const s = await settings.load();
    const i = s.profiles.findIndex((p) => p.id === profile.id);
    const profiles = i >= 0 ? s.profiles.map((p) => (p.id === profile.id ? profile : p)) : [...s.profiles, profile];
    const next: AppSettings = { ...s, profiles, activeProfileId: s.activeProfileId ?? profile.id };
    await settings.save(next);
    return next;
  }
}

export class DeleteProfile {
  constructor(private readonly d: { settings: SettingsPort; secrets: SecretStorePort }) {}
  async execute(profileId: string): Promise<AppSettings> {
    const { settings, secrets } = this.d;
    const s = await settings.load();
    const target = s.profiles.find((p) => p.id === profileId);
    if (target) await secrets.delete(target.tokenRef);
    const profiles = s.profiles.filter((p) => p.id !== profileId);
    const activeProfileId = s.activeProfileId === profileId ? (profiles[0]?.id ?? null) : s.activeProfileId;
    const next = { ...s, profiles, activeProfileId };
    await settings.save(next);
    return next;
  }
}

export class SelectActiveProfile {
  constructor(private readonly d: { settings: SettingsPort }) {}
  async execute(profileId: string | null): Promise<AppSettings> {
    const s = await this.d.settings.load();
    const next = { ...s, activeProfileId: profileId };
    await this.d.settings.save(next);
    return next;
  }
}

export type TestStep = "reach" | "api_version" | "auth" | "other";

export type TestResult =
  | { ok: true; health: Health; capabilities: Capabilities }
  | {
      ok: false;
      failedStep: TestStep;
      error: AppError;
      health?: Health;
      /** failedStep が reach のとき：届かなかった理由の推定。pageOrigin は CORS の許可に加えるオリジン */
      reason?: ReachReason;
      pageOrigin?: string;
    };

export class TestConnection {
  constructor(private readonly d: { compute: ComputeBackendFactory; net?: NetworkProbePort }) {}

  /** health（認証なし）→ API 版の確認 → capabilities（認証あり）の順。失敗は段階別に返す。届かないときは理由も推定する */
  async execute(profile: ComputeProfile, opts: { token?: string | null } = {}): Promise<TestResult> {
    const backend = this.d.compute.forProfile(profile, opts.token !== undefined ? { token: opts.token } : undefined);
    let health: Health;
    try {
      health = await backend.health();
    } catch (e) {
      return { ok: false, failedStep: "reach", error: toAppError(e), ...(await this.whyUnreachable(profile)) };
    }
    if (health.apiVersion !== SUPPORTED_API_VERSION) {
      return {
        ok: false,
        failedStep: "api_version",
        health,
        error: new AppError(
          "API_VERSION_MISMATCH",
          `API 版が合いません（サーバー ${health.apiVersion} / アプリ ${SUPPORTED_API_VERSION}）`,
        ),
      };
    }
    try {
      const capabilities = await backend.capabilities();
      return { ok: true, health, capabilities };
    } catch (e) {
      const err = toAppError(e);
      return { ok: false, failedStep: err.code === "UNAUTHORIZED" ? "auth" : "other", error: err, health };
    }
  }

  /** http:// を https のページから → 混在コンテンツ／no-cors でつながる → CORS で拒否／それ以外 → 届かない */
  private async whyUnreachable(profile: ComputeProfile): Promise<{ reason?: ReachReason; pageOrigin?: string }> {
    const net = this.d.net;
    if (!net) return {};
    if (isMixedContent(net.pageOrigin, profile.baseUrl)) return { reason: "mixed_content", pageOrigin: net.pageOrigin };
    const reached = await net.reachable(`${normalizeBaseUrl(profile.baseUrl)}/v1/health`).catch(() => false);
    return { reason: reached ? "cors" : "unreachable", pageOrigin: net.pageOrigin };
  }
}

/** 動画を送ってよいという確認を記録する（宛先の URL と一緒に） */
export class RecordUploadConsent {
  constructor(private readonly d: { settings: SettingsPort }) {}
  async execute(profile: Pick<ComputeProfile, "id" | "baseUrl">): Promise<AppSettings> {
    const s = await this.d.settings.load();
    const next: AppSettings = {
      ...s,
      uploadConsents: { ...s.uploadConsents, [profile.id]: normalizeBaseUrl(profile.baseUrl) },
    };
    await this.d.settings.save(next);
    return next;
  }
}

export interface ConnectionWatch {
  stop(): void;
  /** 待たずにすぐ確認する */
  checkNow(): void;
}

export class WatchConnection {
  constructor(private readonly d: { clock: ClockPort }) {}

  /** 定期的に health を確認する（30 秒ごと、失敗 2 回で「再接続中」） */
  start(input: {
    backend: ComputeBackendPort;
    onState: (s: ConnectionState) => void;
    intervalMs?: number;
  }): ConnectionWatch {
    const { clock } = this.d;
    const interval = input.intervalMs ?? HEALTH_INTERVAL_MS;
    const stopper = new AbortController();
    let waker = new AbortController();
    let state = initialConnection(input.backend.profileId);
    input.onState(state);

    const loop = async () => {
      while (!stopper.signal.aborted) {
        const ok = await input.backend.health().then(
          () => true,
          () => false,
        );
        if (stopper.signal.aborted) return;
        state = nextConnectionState(state, ok, clock.now().toISOString());
        input.onState(state);
        waker = new AbortController();
        await clock.sleep(interval, waker.signal).catch(() => {});
      }
    };
    void loop();
    return {
      stop: () => {
        stopper.abort();
        waker.abort();
      },
      checkNow: () => waker.abort(),
    };
  }
}

/**
 * フルモード：この画面を配信しているローカルサーバーを接続「この PC」として用意する。
 * トークンは起動のたびに変わるので毎回書き直す。既定の接続が無い（または消えている）ときだけ「この PC」を既定にする
 */
export class EnsureLocalProfile {
  constructor(private readonly d: { settings: SettingsPort; secrets: SecretStorePort }) {}

  async execute(input: { baseUrl: string; token: string; name: string }): Promise<AppSettings> {
    const { settings, secrets } = this.d;
    const profile = localProfile(input.baseUrl, input.name);
    await secrets.set(profile.tokenRef, input.token);
    const s = await settings.load();
    const profiles = s.profiles.some((p) => p.id === profile.id)
      ? s.profiles.map((p) => (p.id === profile.id ? { ...p, baseUrl: profile.baseUrl, kind: "local" as const } : p))
      : [profile, ...s.profiles];
    const active = profiles.some((p) => p.id === s.activeProfileId && !p.devOnly) ? s.activeProfileId : profile.id;
    const next: AppSettings = { ...s, profiles, activeProfileId: active };
    await settings.save(next);
    return next;
  }
}
