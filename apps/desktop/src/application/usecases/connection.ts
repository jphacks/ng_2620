// F03 接続プロファイルと疎通確認

import type { ComputeProfile, ConnectionState } from "@/domain/connection";
import {
  HEALTH_INTERVAL_MS,
  initialConnection,
  nextConnectionState,
  normalizeBaseUrl,
  SUPPORTED_API_VERSION,
} from "@/domain/connection";
import { AppError, toAppError } from "@/domain/errors";
import type { Capabilities, ComputeBackendFactory, ComputeBackendPort, Health } from "../ports/compute";
import type { AppSettings, SecretStorePort, SettingsPort } from "../ports/settings";
import type { ClockPort } from "../ports/system";

export class ListProfiles {
  constructor(private readonly d: { settings: SettingsPort }) {}
  async execute(): Promise<Pick<AppSettings, "profiles" | "activeProfileId" | "localServer">> {
    const s = await this.d.settings.load();
    return { profiles: s.profiles, activeProfileId: s.activeProfileId, localServer: s.localServer };
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
  | { ok: false; failedStep: TestStep; error: AppError; health?: Health };

export class TestConnection {
  constructor(private readonly d: { compute: ComputeBackendFactory }) {}

  /** health（認証なし）→ API 版の確認 → capabilities（認証あり）の順。失敗は段階別に返す */
  async execute(profile: ComputeProfile, opts: { token?: string | null } = {}): Promise<TestResult> {
    const backend = this.d.compute.forProfile(profile, opts.token !== undefined ? { token: opts.token } : undefined);
    let health: Health;
    try {
      health = await backend.health();
    } catch (e) {
      return { ok: false, failedStep: "reach", error: toAppError(e) };
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
