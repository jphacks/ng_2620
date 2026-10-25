// アプリ設定とトークン（F03 / F15）

import type { ComputeProfile } from "@/domain/connection";

export interface AppSettings {
  schemaVersion: 1;
  profiles: ComputeProfile[];
  activeProfileId: string | null;
  /** 動画を送ってよいと確認した外部サーバー（プロファイル ID → 確認したときの URL）。URL が変わると確認し直す */
  uploadConsents?: Record<string, string>;
  /** エディタのパネル寸法（react-resizable-panels が保存する値、グループ ID ごと） */
  panelLayouts: Record<string, unknown>;
  /** 新規プロジェクトの保存先（未設定なら ~/Movies/vvlog） */
  projectsDir?: string;
  /** 取り込み画面の「既定のアバターで自動仕上げ」（解析 → 描画 → エディタで再生）。前回の選択を覚えておく */
  autoFinish?: AutoFinishSetting;
}

export interface AutoFinishSetting {
  enabled: boolean;
  avatarId: string | null;
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
