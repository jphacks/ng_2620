// F03 P1：この PC の GPU サーバーを起動・停止し、接続プロファイル「この PC」を用意する

import { AppError, toAppError } from "@/domain/errors";
import type { LocalServerConfig } from "@/domain/localServer";
import { LOCAL_START_TIMEOUT_MS, localProfile, validateLocalServer } from "@/domain/localServer";
import type { ComputeBackendFactory } from "../ports/compute";
import type { LocalServerPort } from "../ports/localServer";
import type { AppSettings, SecretStorePort, SettingsPort } from "../ports/settings";
import type { ClockPort } from "../ports/system";

const POLL_MS = 1000;

export class SaveLocalServerConfig {
  constructor(private readonly d: { settings: SettingsPort }) {}
  async execute(config: LocalServerConfig): Promise<AppSettings> {
    const s = await this.d.settings.load();
    const next: AppSettings = { ...s, localServer: { ...config, serverDir: config.serverDir.trim(), pythonPath: config.pythonPath.trim() } };
    await this.d.settings.save(next);
    return next;
  }
}

export class StartLocalServer {
  constructor(
    private readonly d: {
      settings: SettingsPort;
      secrets: SecretStorePort;
      server: LocalServerPort;
      compute: ComputeBackendFactory;
      clock: ClockPort;
      newToken: () => string;
    },
  ) {}

  /** 起動 → health が返るまで待つ → 「この PC」のプロファイルを登録して既定にする。トークンは初回に作って保存する */
  async execute(input: { profileName: string; timeoutMs?: number }): Promise<AppSettings> {
    const { settings, secrets, server, compute, clock } = this.d;
    const config = (await settings.load()).localServer;
    if (!config || validateLocalServer(config).length) throw new AppError("LOCAL_SERVER_FAILED", "サーバーのフォルダと推論用の Python を設定してください");

    const profile = localProfile(config.port, input.profileName);
    const token = (await secrets.get(profile.tokenRef)) || this.d.newToken();
    await secrets.set(profile.tokenRef, token);
    const backend = compute.forProfile(profile, { token });
    const alive = () =>
      backend.health().then(
        () => true,
        () => false,
      );

    if (!(await server.status()).running) {
      // 手で起動したサーバーが同じポートにいると、こちらのトークンでは通らない
      if (await alive()) throw new AppError("LOCAL_SERVER_FAILED", `ポート ${config.port} は別のサーバーが使っています。そのサーバーを止めるか、ポートを変えてください`);
      try {
        await server.start({ serverDir: config.serverDir, pythonPath: config.pythonPath, port: config.port, token });
      } catch (e) {
        throw new AppError("LOCAL_SERVER_FAILED", toAppError(e).message);
      }
    }

    const deadline = clock.now().getTime() + (input.timeoutMs ?? LOCAL_START_TIMEOUT_MS);
    while (!(await alive())) {
      const st = await server.status();
      if (!st.running) throw new AppError("LOCAL_SERVER_FAILED", `サーバーが終了しました（終了コード ${st.exitCode ?? "—"}）。ログを確認してください`);
      if (clock.now().getTime() >= deadline) throw new AppError("LOCAL_SERVER_FAILED", "サーバーが応答しません。ログを確認してください");
      await clock.sleep(POLL_MS);
    }

    // 待っている間に他の設定が変わっているかもしれないので読み直す
    const s = await settings.load();
    const profiles = s.profiles.some((p) => p.id === profile.id) ? s.profiles.map((p) => (p.id === profile.id ? profile : p)) : [...s.profiles, profile];
    const next: AppSettings = { ...s, profiles, activeProfileId: profile.id };
    await settings.save(next);
    return next;
  }
}

export class StopLocalServer {
  constructor(private readonly d: { server: LocalServerPort }) {}
  execute(): Promise<void> {
    return this.d.server.stop();
  }
}
