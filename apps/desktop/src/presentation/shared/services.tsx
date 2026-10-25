// 画面から使うサービス（ユースケースと、表示に要る最小限の OS 機能）。組み立ては presentation/app/container.ts

import { createContext, useContext } from "react";
import type { ReactNode } from "react";
import type { ComputeBackendFactory } from "@/application/ports/compute";
import type { EditorMediaPort } from "@/application/ports/editor";
import type { LocalFilePort } from "@/application/ports/media";
import type { ProjectRepositoryPort } from "@/application/ports/project";
import type { SettingsPort } from "@/application/ports/settings";
import type { ClockPort, DialogPort, FileDropPort, NotifierPort, ShellPort } from "@/application/ports/system";
import type * as UC from "@/application/usecases";
import type { ComputeProfile } from "@/domain/connection";
import type { Project } from "@/domain/project";

export type BackendMode = "mock" | "http" | "memory";

/** full：ローカルアプリサーバーが画面を配信している／restricted：Pages 単体（features/mode-matrix.md） */
export type RuntimeMode = "full" | "restricted";

export interface Diagnostics {
  webgl2: boolean;
  stream: boolean | null; // fetch のストリームが読めるか（接続先が無ければ null）
}

export interface Services {
  env: { mode: RuntimeMode; isDev: boolean; backendMode: BackendMode; version: string };
  platform: {
    files: LocalFilePort;
    dialog: DialogPort;
    shell: ShellPort;
    drops: FileDropPort;
    notifier: NotifierPort;
    clock: ClockPort;
  };
  /** 起動後に 1 回、裏で行う掃除など（失敗しても無視してよい）。無ければ何もしない */
  maintenance?: () => Promise<void>;
  compute: ComputeBackendFactory;
  /** プロジェクト内のパスの解決だけ（読み書きはユースケース経由） */
  paths: Pick<ProjectRepositoryPort, "resolve" | "cacheDir" | "pathOf" | "exportsDir" | "defaultDir">;
  /** パネル配置などの UI 設定 */
  settings: SettingsPort;
  uc: {
    createProject: UC.CreateProject;
    openProject: UC.OpenProject;
    saveProject: UC.SaveProject;
    listRecent: UC.ListRecentProjects;
    loadOverrides: UC.LoadOverrides;
    deleteProject: UC.DeleteProject;
    /** 制限モードだけ：zip の書き出し・読み込み（#27） */
    exportProjectZip?: UC.ExportProjectZip;
    importProjectZip?: UC.ImportProjectZip;
    clearCache: (project: Project) => Promise<void>;
    probeMedia: UC.ProbeMedia;
    importMedia: UC.ImportMedia;
    listProfiles: UC.ListProfiles;
    saveProfile: UC.SaveProfile;
    deleteProfile: UC.DeleteProfile;
    selectActiveProfile: UC.SelectActiveProfile;
    testConnection: UC.TestConnection;
    watchConnection: UC.WatchConnection;
    /** フルモードだけ：画面を配信しているローカルサーバーを接続「この PC」として用意する */
    ensureLocalProfile?: () => Promise<{ profiles: ComputeProfile[]; activeProfileId: string | null }>;
    submitJob: UC.SubmitJob;
    subscribeJob: UC.SubscribeJob;
    fetchArtifact: UC.FetchArtifact;
    cancelJob: UC.CancelJob;
    retryJob: UC.RetryJob;
    getQueue: UC.GetQueue;
    getLogs: UC.GetLogs;
    restoreJobs: UC.RestoreJobs;
    loadAnalysisResult: UC.LoadAnalysisResult;
    updateCast: UC.UpdateCast;
    renamePerson: UC.RenamePerson;
    startRender: UC.StartRender;
    listAvatars: UC.ListAvatars;
    importAvatar: UC.ImportAvatar;
    removeAvatar: UC.RemoveAvatar;
    startExport: UC.StartExport;
    saveExportResult: UC.SaveExportResult;
    loadEditorData: UC.LoadEditorData;
    saveOverrides: UC.SaveOverrides;
  };
  /** エディタ（F09）が読む画像のデコード */
  editorMedia: EditorMediaPort;
  /** F12（レーン B）が差し込む。同梱サンプルをコピーして、そのパスを返す */
  openSample?: () => Promise<{ path: string }>;
  diagnose(): Promise<Diagnostics>;
}

let current: Services | null = null;

/** ストア（React の外）から使う */
export function getServices(): Services {
  if (!current) throw new Error("services が初期化されていません（container.ts）");
  return current;
}

const Ctx = createContext<Services | null>(null);

export function ServicesProvider({ services, children }: { services: Services; children: ReactNode }) {
  current = services;
  return <Ctx.Provider value={services}>{children}</Ctx.Provider>;
}

export function useServices(): Services {
  const s = useContext(Ctx);
  if (!s) throw new Error("ServicesProvider がありません");
  return s;
}
