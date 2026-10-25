// composition root（DI）。ポートの実装を組み立てる唯一の場所（architecture/overview §3）。
//   full       ：ローカルアプリサーバー（/v1/local/*）越しのファイル・ハッシュ・probe・ダイアログ・ドロップ取り込み＋ブラウザの fetch（ADR-0015）
//   restricted ：IndexedDB（無ければメモリ）上のファイル＋ fetch。Pages 単体、`pnpm dev` を単体で開いたとき
//   VITE_BACKEND ：mock = モックサーバー（F14、:8766）を既定に、memory = シミュレーターを既定に、http = 登録した接続だけ

import * as UC from "@/application/usecases";
import { FsAvatarLibrary } from "@/infrastructure/avatar/FsAvatarLibrary";
import { FsLayerCache } from "@/infrastructure/cache/FsLayerCache";
import { DefaultComputeBackendFactory } from "@/infrastructure/compute/factory";
import { InMemoryComputeBackend } from "@/infrastructure/compute/InMemoryComputeBackend";
import { canvasSynth } from "@/infrastructure/compute/simArtifacts";
import { fetchTransport } from "@/infrastructure/compute/fetchTransport";
import type { FileSystem } from "@/infrastructure/fs/FileSystem";
import { BrowserEditorMedia } from "@/infrastructure/editor/editorMedia";
import { LocalFiles } from "@/infrastructure/fs/LocalFiles";
import { MEM_ROOT, MemoryFs } from "@/infrastructure/fs/memoryFs";
import { RoutingFs } from "@/infrastructure/fs/routingFs";
import { joinPath } from "@/infrastructure/fs/path";
import { HttpFileSystem } from "@/infrastructure/fs/httpFs";
import { HttpFileHasher, WebCryptoFileHasher } from "@/infrastructure/media/hashers";
import { HttpMediaProbe, VideoElementMediaProbe } from "@/infrastructure/media/probes";
import type { Runtime } from "@/infrastructure/platform";
import { LocalApi } from "@/infrastructure/local/localApi";
import { createLocalApp } from "@/infrastructure/local/probeLocalApp";
import { FsProjectArchive } from "@/infrastructure/project/FsProjectArchive";
import { FsProjectRepository } from "@/infrastructure/project/FsProjectRepository";
import {
  FileSecretStore,
  FileSettings,
  LocalStorageSecretStore,
  LocalStorageSettings,
} from "@/infrastructure/settings/settingsStores";
import { HttpDialog } from "@/infrastructure/system/httpDialog";
import { serverFileDrop, sweepImports } from "@/infrastructure/system/serverDrop";
import { useDropUploadStore } from "@/presentation/shared/stores/dropUploadStore";
import {
  browserDialog,
  browserFileDrop,
  browserFileExchange,
  browserNotifier,
  browserShell,
} from "@/infrastructure/system/browserSystem";
import { randomIds, systemClock } from "@/infrastructure/system/common";
import { ja } from "@/presentation/shared/i18n/ja";
import type { BackendMode, Services } from "@/presentation/shared/services";

function backendMode(): BackendMode {
  const v = import.meta.env.VITE_BACKEND;
  return v === "mock" || v === "memory" ? v : "http";
}

/** detectRuntime() の結果を渡す。省略時は restricted（テストと単体の開発用） */
export function createServices(runtime: Runtime = { mode: "restricted" }): Services {
  const local = runtime.mode === "full" && runtime.local ? runtime.local : null;
  const api = local ? new LocalApi(local) : null;
  const diskBacked = !!api; // 実ディスクに置く（設定・シークレットもファイル）
  const isDev = import.meta.env.DEV;
  // restricted：ドロップ・選択した端末のファイルは memFs（File への参照。閉じると消える）、それ以外は IndexedDB（storage があれば）
  const memFs = diskBacked ? null : new MemoryFs();
  const fs: FileSystem = memFs
    ? new RoutingFs(runtime.storage ?? new MemoryFs(), memFs, (p) => p.startsWith(`${MEM_ROOT}/drops/`))
    : new HttpFileSystem(api!);

  const dirs = local
    ? {
        projects: async () => local.dirs.projects,
        appData: async () => local.dirs.appData,
        config: async () => local.dirs.config,
      }
    : {
        projects: async () => `${MEM_ROOT}/Movies/vvlog`,
        appData: async () => `${MEM_ROOT}/AppData`,
        config: async () => `${MEM_ROOT}/Config`,
      };

  const settings = diskBacked
    ? new FileSettings(fs, async () => joinPath(await dirs.config(), "settings.json"))
    : new LocalStorageSettings();
  const secrets = diskBacked
    ? new FileSecretStore(fs, async () => joinPath(await dirs.config(), "secrets.json"))
    : new LocalStorageSecretStore();
  const projects = new FsProjectRepository(fs, {
    projectsDir: async () => (await settings.load()).projectsDir ?? (await dirs.projects()),
    appDataDir: dirs.appData,
  });
  const files = new LocalFiles(fs);
  const archive = new FsProjectArchive(fs);
  const hasher = api ? new HttpFileHasher(api) : new WebCryptoFileHasher(fs);
  const probe = api ? new HttpMediaProbe(api, fs) : new VideoElementMediaProbe(fs);
  const cache = new FsLayerCache(fs, hasher);
  const avatars = new FsAvatarLibrary(fs, async () => joinPath(await dirs.appData(), "avatars"));
  const transport = fetchTransport(fs);

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

  const importsDir = async () => joinPath(await dirs.appData(), "imports");
  const clock = systemClock;
  const submit = new UC.SubmitJob();

  return {
    env: { mode: runtime.mode, isDev, backendMode: backendMode(), version: __APP_VERSION__ },
    platform: {
      files,
      // full：ネイティブダイアログはサーバー側（#46）、ドロップはサーバーの取り込みフォルダへ転送（#47）
      dialog: memFs ? browserDialog(memFs) : new HttpDialog(api!),
      shell: browserShell(fs),
      drops: memFs
        ? browserFileDrop(memFs)
        : serverFileDrop({
            fs,
            importsDir,
            newId: () => randomIds.newId("imp"),
            onUpload: (ev) => useDropUploadStore.getState().update(ev),
          }),
      notifier: browserNotifier,
      localApp: memFs && window.location.protocol === "https:" ? createLocalApp() : undefined,
      clock,
    },
    maintenance: api
      ? async () => {
          // 取り込みフォルダの掃除：最近のプロジェクトが元の動画として使っているものは残す
          const referenced: string[] = [];
          for (const r of await projects.listRecent()) {
            try {
              const raw = JSON.parse(await fs.readText(joinPath(r.path, "project.json"))) as {
                source?: { path?: string };
              };
              if (raw.source?.path) referenced.push(raw.source.path);
            } catch {
              // 読めないプロジェクトは参照なしとして扱わず、安全側に倒す（掃除しすぎない）
              referenced.push(r.path);
            }
          }
          await sweepImports(fs, await importsDir(), referenced);
        }
      : undefined,
    compute,
    paths: projects,
    settings,
    editorMedia: new BrowserEditorMedia(files),
    uc: {
      createProject: new UC.CreateProject({ projects, clock, ids: randomIds }),
      openProject: new UC.OpenProject({ projects }),
      saveProject: new UC.SaveProject({ projects, clock }),
      listRecent: new UC.ListRecentProjects({ projects }),
      loadOverrides: new UC.LoadOverrides({ projects }),
      deleteProject: new UC.DeleteProject({ projects }),
      ...(memFs
        ? {
            exportProjectZip: new UC.ExportProjectZip({ archive, exchange: browserFileExchange }),
            importProjectZip: new UC.ImportProjectZip({ archive, exchange: browserFileExchange, projects }),
          }
        : {}),
      clearCache: (project) => projects.clearCache(project),
      probeMedia: new UC.ProbeMedia({ probe }),
      importMedia: new UC.ImportMedia({ files, hasher, clock }),
      listProfiles: new UC.ListProfiles({ settings }),
      saveProfile: new UC.SaveProfile({ settings, secrets }),
      deleteProfile: new UC.DeleteProfile({ settings, secrets }),
      selectActiveProfile: new UC.SelectActiveProfile({ settings }),
      testConnection: new UC.TestConnection({ compute }),
      watchConnection: new UC.WatchConnection({ clock }),
      ...(local
        ? {
            // この画面を配信しているサーバー自身につなぐ（開発プロキシ越しでも、見えている origin がそのまま宛先）
            ensureLocalProfile: () =>
              new UC.EnsureLocalProfile({ settings, secrets }).execute({
                baseUrl: window.location.origin,
                token: local.token,
                name: ja.connection.local.profileName,
              }),
          }
        : {}),
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
      loadEditorData: new UC.LoadEditorData({ projects, files }),
      saveOverrides: new UC.SaveOverrides({ projects }),
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
