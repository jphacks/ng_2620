// composition root（DI）。ポートの実装を組み立てる唯一の場所（architecture/overview §3）。
//   Tauri の中   ：Rust コマンド（fs / media / hash / net）＋ tauri-plugin-http / dialog / opener / notification
//   ブラウザだけ ：メモリ上のファイル＋ fetch（`pnpm dev` を Tauri なしで開いたとき。開発用）
//   VITE_BACKEND ：mock = モックサーバー（F14、:8766）を既定に、memory = シミュレーターを既定に、http = 登録した接続だけ

import { appConfigDir, appDataDir, join, videoDir } from "@tauri-apps/api/path";
import * as UC from "@/application/usecases";
import { FsAvatarLibrary } from "@/infrastructure/avatar/FsAvatarLibrary";
import { FsLayerCache } from "@/infrastructure/cache/TauriLayerCache";
import { DefaultComputeBackendFactory } from "@/infrastructure/compute/factory";
import { InMemoryComputeBackend } from "@/infrastructure/compute/InMemoryComputeBackend";
import { canvasSynth } from "@/infrastructure/compute/simArtifacts";
import { browserTransport, tauriTransport } from "@/infrastructure/compute/transports";
import type { FileSystem } from "@/infrastructure/fs/FileSystem";
import { LocalFiles } from "@/infrastructure/fs/LocalFiles";
import { MEM_ROOT, MemoryFs } from "@/infrastructure/fs/memoryFs";
import { joinPath } from "@/infrastructure/fs/path";
import { tauriFs } from "@/infrastructure/fs/tauriFs";
import { TauriFileHasher, WebCryptoFileHasher } from "@/infrastructure/media/hashers";
import { TauriMediaProbe, VideoElementMediaProbe } from "@/infrastructure/media/probes";
import { isTauri } from "@/infrastructure/platform";
import { FsProjectRepository } from "@/infrastructure/project/FsProjectRepository";
import { FileSecretStore, FileSettings, LocalStorageSecretStore, LocalStorageSettings } from "@/infrastructure/settings/settingsStores";
import { browserDialog, browserFileDrop, browserNotifier, browserShell } from "@/infrastructure/system/browserSystem";
import { randomIds, systemClock } from "@/infrastructure/system/common";
import { tauriDialog, tauriFileDrop, tauriNotifier, tauriShell } from "@/infrastructure/system/tauriSystem";
import type { BackendMode, Services } from "@/presentation/shared/services";

function backendMode(): BackendMode {
  const v = import.meta.env.VITE_BACKEND;
  return v === "mock" || v === "memory" ? v : "http";
}

export function createServices(): Services {
  const inTauri = isTauri();
  const isDev = import.meta.env.DEV;
  const memFs = inTauri ? null : new MemoryFs();
  const fs: FileSystem = memFs ?? tauriFs;

  const dirs = inTauri
    ? {
        projects: async () => join(await videoDir(), "vvlog"),
        appData: () => appDataDir(),
        config: () => appConfigDir(),
      }
    : {
        projects: async () => `${MEM_ROOT}/Movies/vvlog`,
        appData: async () => `${MEM_ROOT}/AppData`,
        config: async () => `${MEM_ROOT}/Config`,
      };

  const settings = inTauri ? new FileSettings(fs, async () => joinPath(await dirs.config(), "settings.json")) : new LocalStorageSettings();
  const secrets = inTauri ? new FileSecretStore(fs, async () => joinPath(await dirs.config(), "secrets.json")) : new LocalStorageSecretStore();
  const projects = new FsProjectRepository(fs, {
    projectsDir: async () => (await settings.load()).projectsDir ?? (await dirs.projects()),
    appDataDir: dirs.appData,
  });
  const files = new LocalFiles(fs);
  const hasher = inTauri ? new TauriFileHasher() : new WebCryptoFileHasher(fs);
  const probe = inTauri ? new TauriMediaProbe(fs) : new VideoElementMediaProbe(fs);
  const cache = new FsLayerCache(fs, hasher);
  const avatars = new FsAvatarLibrary(fs, async () => joinPath(await dirs.appData(), "avatars"));
  const transport = inTauri ? tauriTransport(fs) : browserTransport(fs);

  let sim: InMemoryComputeBackend | null = null;
  const compute = new DefaultComputeBackendFactory({
    secrets,
    transport,
    simulator: isDev
      ? () =>
          (sim ??= new InMemoryComputeBackend({
            fs,
            synth: canvasSynth(),
            speed: Number(import.meta.env.VITE_SIM_SPEED) || 1,
            fail: import.meta.env.VITE_SIM_FAIL || undefined,
          }))
      : undefined,
  });

  const clock = systemClock;
  const submit = new UC.SubmitJob();

  return {
    env: { isTauri: inTauri, isDev, backendMode: backendMode(), version: __APP_VERSION__ },
    platform: {
      files,
      dialog: memFs ? browserDialog(memFs) : tauriDialog,
      shell: memFs ? browserShell(memFs) : tauriShell,
      drops: memFs ? browserFileDrop(memFs) : tauriFileDrop,
      notifier: inTauri ? tauriNotifier : browserNotifier,
      clock,
    },
    compute,
    paths: projects,
    settings,
    uc: {
      createProject: new UC.CreateProject({ projects, clock, ids: randomIds }),
      openProject: new UC.OpenProject({ projects }),
      saveProject: new UC.SaveProject({ projects, clock }),
      listRecent: new UC.ListRecentProjects({ projects }),
      loadOverrides: new UC.LoadOverrides({ projects }),
      deleteProject: new UC.DeleteProject({ projects }),
      clearCache: (project) => projects.clearCache(project),
      probeMedia: new UC.ProbeMedia({ probe }),
      importMedia: new UC.ImportMedia({ files, hasher, clock }),
      listProfiles: new UC.ListProfiles({ settings }),
      saveProfile: new UC.SaveProfile({ settings, secrets }),
      deleteProfile: new UC.DeleteProfile({ settings, secrets }),
      selectActiveProfile: new UC.SelectActiveProfile({ settings }),
      testConnection: new UC.TestConnection({ compute }),
      watchConnection: new UC.WatchConnection({ clock }),
      submitJob: submit,
      subscribeJob: new UC.SubscribeJob({ clock }),
      fetchArtifact: new UC.FetchArtifact({ projects, cache }),
      cancelJob: new UC.CancelJob(),
      retryJob: new UC.RetryJob({ submit }),
      getQueue: new UC.GetQueue(),
      getLogs: new UC.GetLogs(),
      restoreJobs: new UC.RestoreJobs(),
      loadAnalysisResult: new UC.LoadAnalysisResult({ projects, files }),
      updateCast: new UC.UpdateCast(),
      renamePerson: new UC.RenamePerson(),
      startRender: new UC.StartRender({ avatars, submit }),
      listAvatars: new UC.ListAvatars({ avatars }),
      importAvatar: new UC.ImportAvatar({ avatars }),
      removeAvatar: new UC.RemoveAvatar({ avatars }),
      startExport: new UC.StartExport({ projects, submit }),
      saveExportResult: new UC.SaveExportResult({ projects, files }),
    },
    async diagnose() {
      const webgl2 = !!document.createElement("canvas").getContext("webgl2");
      // 既定の接続の /v1/health を fetch のストリームとして読めるか（SSE と同じ読み方）
      let stream: boolean | null = null;
      try {
        const s = await settings.load();
        const profile = s.profiles.find((p) => p.id === s.activeProfileId);
        if (profile && /^https?:\/\//.test(profile.baseUrl)) {
          const res = await transport.fetch(`${profile.baseUrl}/v1/health`, { method: "GET" });
          const reader = res.body?.getReader();
          stream = !!reader && !(await reader.read()).done;
          await reader?.cancel();
        }
      } catch (e) {
        console.warn("[self-check] stream", e);
        stream = false;
      }
      return { webgl2, stream };
    },
  };
}
