import { describe, expect, it } from "vitest";
import type { ComputeProfile } from "@/domain/connection";
import { LOCAL_PROFILE_ID } from "@/domain/connection";
import type { AppSettings, SecretStorePort, SettingsPort } from "../ports/settings";
import { defaultSettings } from "../ports/settings";
import { EnsureLocalProfile } from "./connection";

function world(initial: Partial<AppSettings> = {}) {
  let saved: AppSettings = { ...defaultSettings(), ...initial };
  const secrets = new Map<string, string>();
  const settings: SettingsPort = {
    load: async () => structuredClone(saved),
    save: async (s) => void (saved = structuredClone(s)),
  };
  const store: SecretStorePort = {
    get: async (k) => secrets.get(k) ?? null,
    set: async (k, v) => void secrets.set(k, v),
    delete: async (k) => void secrets.delete(k),
  };
  return { uc: new EnsureLocalProfile({ settings, secrets: store }), secrets, get: () => saved };
}

const input = (token = "t1", baseUrl = "http://127.0.0.1:8765") => ({ baseUrl, token, name: "この PC" });
const remote: ComputeProfile = {
  id: "gpu1",
  name: "自前 GPU",
  kind: "remote",
  baseUrl: "https://gpu.example",
  tokenRef: "gpu1",
};

describe("EnsureLocalProfile", () => {
  it("初回：『この PC』を登録して既定にし、トークンを保存する", async () => {
    const w = world();
    const s = await w.uc.execute(input("tok-a"));
    expect(s.profiles).toEqual([
      {
        id: LOCAL_PROFILE_ID,
        name: "この PC",
        kind: "local",
        baseUrl: "http://127.0.0.1:8765",
        tokenRef: LOCAL_PROFILE_ID,
      },
    ]);
    expect(s.activeProfileId).toBe(LOCAL_PROFILE_ID);
    expect(w.secrets.get(LOCAL_PROFILE_ID)).toBe("tok-a");
    expect(w.get().activeProfileId).toBe(LOCAL_PROFILE_ID);
  });

  it("起動のたびにトークンと宛先を更新する（トークンは起動ごとに変わる）。プロファイルは増えない", async () => {
    const w = world();
    await w.uc.execute(input("tok-a", "http://127.0.0.1:8765"));
    const s = await w.uc.execute(input("tok-b", "http://127.0.0.1:50123"));
    expect(s.profiles).toHaveLength(1);
    expect(s.profiles[0]!.baseUrl).toBe("http://127.0.0.1:50123");
    expect(w.secrets.get(LOCAL_PROFILE_ID)).toBe("tok-b");
  });

  it("利用者が選んだ別の接続（自前の GPU）が既定なら、そのまま尊重する", async () => {
    const w = world({ profiles: [remote], activeProfileId: "gpu1" });
    const s = await w.uc.execute(input());
    expect(s.activeProfileId).toBe("gpu1");
    expect(s.profiles.map((p) => p.id)).toEqual([LOCAL_PROFILE_ID, "gpu1"]);
  });

  it("既定の接続が無い・消えている・開発用だけのときは『この PC』を既定にする", async () => {
    for (const init of [
      { profiles: [remote], activeProfileId: null },
      { profiles: [remote], activeProfileId: "gone" },
      { profiles: [{ ...remote, id: "dev", devOnly: true }], activeProfileId: "dev" },
    ] as const) {
      const w = world({ ...init, profiles: [...init.profiles] });
      expect((await w.uc.execute(input())).activeProfileId).toBe(LOCAL_PROFILE_ID);
    }
  });

  it("利用者が『この PC』の名前を変えていても、宛先だけを直す", async () => {
    const w = world({
      profiles: [
        {
          id: LOCAL_PROFILE_ID,
          name: "自宅の PC",
          kind: "local",
          baseUrl: "http://127.0.0.1:1",
          tokenRef: LOCAL_PROFILE_ID,
        },
      ],
      activeProfileId: LOCAL_PROFILE_ID,
    });
    const s = await w.uc.execute(input("t", "http://127.0.0.1:9"));
    expect(s.profiles[0]).toMatchObject({ name: "自宅の PC", baseUrl: "http://127.0.0.1:9" });
  });
});
