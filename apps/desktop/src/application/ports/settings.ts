// アプリ設定とトークン（F03 / F15）

import type { ComputeProfile } from "@/domain/connection";
import type { LocalServerConfig } from "@/domain/localServer";

export interface AppSettings {
  schemaVersion: 1;
  profiles: ComputeProfile[];
  activeProfileId: string | null;
  /** この PC の GPU サーバー（未設定なら undefined） */
  localServer?: LocalServerConfig;
  /** エディタのパネル寸法（react-resizable-panels が保存する値、グループ ID ごと） */
  panelLayouts: Record<string, unknown>;
  /** 新規プロジェクトの保存先（未設定なら ~/Movies/vvlog） */
  projectsDir?: string;
}

export function defaultSettings(): AppSettings {
  return { schemaVersion: 1, profiles: [], activeProfileId: null, panelLayouts: {} };
}

export interface SettingsPort {
  load(): Promise<AppSettings>;
  save(settings: AppSettings): Promise<void>;
}

/** P0 は設定ファイル（権限 600）、P1 で OS のキーチェーン */
export interface SecretStorePort {
  get(ref: string): Promise<string | null>;
  set(ref: string, value: string): Promise<void>;
  delete(ref: string): Promise<void>;
}
