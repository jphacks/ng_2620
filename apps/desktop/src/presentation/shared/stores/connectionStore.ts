// 接続プロファイルと現在の接続状態（F03）。ステータスバー・ホーム・取り込み・設定が使う

import { create } from "zustand";
import type { Capabilities, ComputeBackendPort } from "@/application/ports/compute";
import type { ConnectionWatch } from "@/application/usecases";
import type { ComputeProfile, ConnectionState } from "@/domain/connection";
import { initialConnection } from "@/domain/connection";
import { ja } from "../i18n/ja";
import { getServices } from "../services";

export const DEV_MOCK_PROFILE: ComputeProfile = {
  id: "dev-mock",
  name: ja.connection.devMock,
  kind: "local",
  baseUrl: "http://127.0.0.1:8766",
  tokenRef: "dev-mock",
  devOnly: true,
};

export const DEV_SIM_PROFILE: ComputeProfile = {
  id: "dev-memory",
  name: ja.connection.devSim,
  kind: "local",
  baseUrl: "memory://sim",
  tokenRef: "dev-memory",
  devOnly: true,
};

interface ConnectionStore {
  ready: boolean;
  profiles: ComputeProfile[];
  activeProfileId: string | null;
  state: ConnectionState;
  capabilities: Capabilities | null;
  backend: ComputeBackendPort | null;
  init(): Promise<void>;
  select(profileId: string | null): Promise<void>;
  save(profile: ComputeProfile, token?: string): Promise<void>;
  remove(profileId: string): Promise<void>;
  checkNow(): void;
}

let watch: ConnectionWatch | null = null;

export function activeProfile(s: Pick<ConnectionStore, "profiles" | "activeProfileId">): ComputeProfile | null {
  return s.profiles.find((p) => p.id === s.activeProfileId) ?? null;
}

/** 現在の接続の実装（無ければオフライン） */
export function currentBackend(): ComputeBackendPort {
  return useConnectionStore.getState().backend ?? getServices().compute.offline();
}

export const useConnectionStore = create<ConnectionStore>((set, get) => {
  const restartWatch = () => {
    watch?.stop();
    watch = null;
    const { uc, compute } = getServices();
    const profile = activeProfile(get());
    if (!profile) {
      set({ backend: null, state: initialConnection(null), capabilities: null });
      return;
    }
    const backend = compute.forProfile(profile);
    set({ backend, capabilities: null });
    watch = uc.watchConnection.start({
      backend,
      onState: (state) => {
        const wasConnected = get().state.status === "connected";
        set({ state });
        if (state.status === "connected" && (!wasConnected || !get().capabilities)) {
          backend.capabilities().then(
            (capabilities) => get().backend === backend && set({ capabilities }),
            () => {},
          );
        }
      },
    });
  };

  return {
    ready: false,
    profiles: [],
    activeProfileId: null,
    state: initialConnection(null),
    capabilities: null,
    backend: null,

    async init() {
      const { uc, env } = getServices();
      const listed = await uc.listProfiles.execute();
      let { profiles, activeProfileId } = listed;
      // フルモード：この画面を配信しているサーバー自身を接続「この PC」として用意する（トークンは起動ごとに変わる）
      if (uc.ensureLocalProfile) ({ profiles, activeProfileId } = await uc.ensureLocalProfile());
      if (env.isDev) {
        // 開発ビルドだけ：モックサーバー（F14）とシミュレーターを初期登録
        for (const [p, token] of [
          [DEV_MOCK_PROFILE, "mock"],
          [DEV_SIM_PROFILE, ""],
        ] as const) {
          if (!profiles.some((x) => x.id === p.id))
            ({ profiles, activeProfileId } = await uc.saveProfile.execute({ profile: p, token }));
        }
        const preferred =
          env.backendMode === "memory" ? DEV_SIM_PROFILE.id : env.backendMode === "mock" ? DEV_MOCK_PROFILE.id : null;
        const current = profiles.find((p) => p.id === activeProfileId);
        if (preferred && (!current || current.devOnly) && activeProfileId !== preferred) {
          ({ activeProfileId } = await uc.selectActiveProfile.execute(preferred));
        }
      } else {
        profiles = profiles.filter((p) => !p.devOnly);
        if (!profiles.some((p) => p.id === activeProfileId)) activeProfileId = profiles[0]?.id ?? null;
      }
      set({ profiles, activeProfileId, ready: true });
      restartWatch();
    },

    async select(profileId) {
      const s = await getServices().uc.selectActiveProfile.execute(profileId);
      set({ activeProfileId: s.activeProfileId });
      restartWatch();
    },

    async save(profile, token) {
      const s = await getServices().uc.saveProfile.execute({ profile, token });
      const wasActive = get().activeProfileId === profile.id;
      set({ profiles: s.profiles, activeProfileId: s.activeProfileId });
      if (wasActive || s.activeProfileId === profile.id) restartWatch();
    },

    async remove(profileId) {
      const s = await getServices().uc.deleteProfile.execute(profileId);
      set({ profiles: s.profiles, activeProfileId: s.activeProfileId });
      restartWatch();
    },

    checkNow() {
      watch?.checkNow();
    },
  };
});
