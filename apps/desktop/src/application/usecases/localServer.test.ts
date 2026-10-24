import { describe, expect, it } from "vitest";
import { AppError } from "@/domain/errors";
import type { LocalServerConfig } from "@/domain/localServer";
import { LOCAL_PROFILE_ID } from "@/domain/localServer";
import type { LocalServerPort, LocalServerStatus } from "../ports/localServer";
import type { AppSettings, SecretStorePort, SettingsPort } from "../ports/settings";
import { defaultSettings } from "../ports/settings";
import { FakeBackend, FakeClock } from "../testing/fakes";
import { StartLocalServer } from "./localServer";

const CONFIG: LocalServerConfig = {
  serverDir: "C:/repo/services/gpu_server",
  pythonPath: "C:/py/python.exe",
  port: 8765,
  autoStart: false,
};

class MemSettings implements SettingsPort {
  constructor(public s: AppSettings = defaultSettings()) {}
  async load() {
    return this.s;
  }
  async save(s: AppSettings) {
    this.s = s;
  }
}

class MemSecrets implements SecretStorePort {
  map = new Map<string, string>();
  async get(ref: string) {
    return this.map.get(ref) ?? null;
  }
  async set(ref: string, v: string) {
    this.map.set(ref, v);
  }
  async delete(ref: string) {
    this.map.delete(ref);
  }
}

/** start してから readyAfter 回目の health で応答する。exitAfterStart なら起動直後に終了する */
class FakeServer implements LocalServerPort {
  started: { token: string } | null = null;
  running = false;
  readyAfter = 2;
  exitAfterStart = false;
  /** 起動前から同じポートに別のサーバーがいる */
  foreign = false;
  async start(input: { token: string }) {
    this.started = { token: input.token };
    this.running = !this.exitAfterStart;
    return 42;
  }
  async stop() {
    this.running = false;
  }
  async status(): Promise<LocalServerStatus> {
    return {
      running: this.running,
      pid: this.running ? 42 : null,
      exitCode: this.exitAfterStart && this.started ? 1 : null,
      log: [],
    };
  }
  health() {
    if (this.foreign || (this.running && --this.readyAfter < 0))
      return Promise.resolve({ status: "ok" as const, version: "t", apiVersion: 1 });
    return Promise.reject(new AppError("NETWORK", "refused"));
  }
}

function setup(opts: { config?: LocalServerConfig } = {}) {
  const settings = new MemSettings({ ...defaultSettings(), localServer: opts.config ?? CONFIG });
  const secrets = new MemSecrets();
  const server = new FakeServer();
  const compute = {
    forProfile: () => Object.assign(new FakeBackend(), { health: () => server.health() }),
    offline: () => new FakeBackend(),
  };
  const uc = new StartLocalServer({
    settings,
    secrets,
    server,
    compute,
    clock: new FakeClock(),
    newToken: () => "tok_new",
  });
  return { settings, secrets, server, uc };
}

describe("StartLocalServer", () => {
  it("起動して応答を待ち、「この PC」の接続を既定にする。トークンは作って保存する", async () => {
    const { settings, secrets, server, uc } = setup();
    const s = await uc.execute({ profileName: "この PC" });
    expect(server.started).toEqual({ token: "tok_new" });
    expect(await secrets.get(LOCAL_PROFILE_ID)).toBe("tok_new");
    expect(s.activeProfileId).toBe(LOCAL_PROFILE_ID);
    expect(settings.s.profiles).toEqual([
      expect.objectContaining({ id: LOCAL_PROFILE_ID, kind: "local", baseUrl: "http://127.0.0.1:8765" }),
    ]);
  });

  it("保存済みのトークンを使い回す", async () => {
    const { secrets, server, uc } = setup();
    await secrets.set(LOCAL_PROFILE_ID, "tok_old");
    await uc.execute({ profileName: "この PC" });
    expect(server.started).toEqual({ token: "tok_old" });
  });

  it("設定が足りなければ起動しない", async () => {
    const { server, uc } = setup({ config: { ...CONFIG, pythonPath: "" } });
    await expect(uc.execute({ profileName: "この PC" })).rejects.toMatchObject({ code: "LOCAL_SERVER_FAILED" });
    expect(server.started).toBeNull();
  });

  it("同じポートに別のサーバーがいれば起動しない", async () => {
    const { server, uc } = setup();
    server.foreign = true;
    await expect(uc.execute({ profileName: "この PC" })).rejects.toMatchObject({ code: "LOCAL_SERVER_FAILED" });
    expect(server.started).toBeNull();
  });

  it("応答する前に終了したら失敗にして、接続は登録しない", async () => {
    const { settings, server, uc } = setup();
    server.exitAfterStart = true;
    await expect(uc.execute({ profileName: "この PC" })).rejects.toMatchObject({
      code: "LOCAL_SERVER_FAILED",
      message: expect.stringContaining("終了コード 1"),
    });
    expect(settings.s.profiles).toEqual([]);
  });

  it("時間内に応答しなければ失敗にする", async () => {
    const { server, uc } = setup();
    server.readyAfter = Infinity;
    await expect(uc.execute({ profileName: "この PC", timeoutMs: 5000 })).rejects.toMatchObject({
      code: "LOCAL_SERVER_FAILED",
    });
  });
});
