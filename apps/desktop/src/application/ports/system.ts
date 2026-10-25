// OS 機能：ファイル選択、Finder で表示、ドロップ、通知、時計、ID

export interface FileFilter {
  name: string;
  extensions: string[];
}

export interface DialogPort {
  pickFiles(opts: { filters?: FileFilter[]; multiple?: boolean }): Promise<string[]>;
  pickDirectory(opts?: { defaultPath?: string }): Promise<string | null>;
  saveFile(opts: { defaultPath?: string; filters?: FileFilter[] }): Promise<string | null>;
}

export interface ShellPort {
  /** Finder / エクスプローラで表示 */
  reveal(path: string): Promise<void>;
  /** 既定のアプリで開く */
  open(path: string): Promise<void>;
  /** ブラウザで URL を開く（VRM の利用条件など） */
  openUrl(url: string): Promise<void>;
}

/** ウィンドウへのファイルのドラッグ＆ドロップ。座標は CSS ピクセル（ビューポート基準） */
export type FileDropEvent =
  | { type: "over"; x: number; y: number; paths: string[] }
  | {
      type: "drop";
      x: number;
      y: number;
      /** 受け付けるかの判定に使う（拡張子など）。prepare があるときは、まだ存在しない取り込み先のパス */
      paths: string[];
      /** ブラウザのフルモード：File をサーバーへ取り込む。受け付けた先だけが呼び、できたパスを返す（受け付けない先では呼ばない＝無駄に送らない） */
      prepare?: () => Promise<string[]>;
    }
  | { type: "leave" };

/** ブラウザのフルモードで、ドロップされた File をサーバーへ取り込んでいる様子（進捗表示用） */
export interface DropUploadEvent {
  id: string;
  name: string;
  state: "uploading" | "done" | "error" | "canceled";
  sent: number;
  total: number;
  error?: string;
  /** uploading の間だけ：転送をやめる */
  cancel?: () => void;
}

export interface FileDropPort {
  subscribe(listener: (ev: FileDropEvent) => void): () => void;
}

export type NotifyPermission = "granted" | "denied" | "default" | "unsupported";

export interface NotifierPort {
  /**
   * アプリが背面のときの通知。許可があれば OS の通知、なければ何も出さない（画面内のトーストは呼び出し側が出す）。
   * ブラウザではタブのタイトルとアイコンにバッジも付ける（前面に戻ると消える）
   */
  osNotify(title: string, body?: string): Promise<void>;
  permission(): NotifyPermission;
  /** 許可を求める。ブラウザはユーザー操作の中でしか許可されない（Safari は必須）ので、ジョブを始めるボタンの中で呼ぶ */
  requestPermission(): Promise<NotifyPermission>;
}

export interface ClockPort {
  now(): Date;
  sleep(ms: number, signal?: AbortSignal): Promise<void>;
}

export interface IdPort {
  /** 接頭辞付きの ID（例 loc_6f1c…） */
  newId(prefix: string): string;
}

export type LocalAppProbe = { found: true; url: string } | { found: false };

/** 制限モード（Pages など）から、この PC のローカルアプリ（フルモード）を探す・案内する（#38） */
export interface LocalAppPort {
  /** ローカルアプリのアドレス（既定ポート） */
  readonly url: string;
  /** このブラウザで探せるか（Safari・Firefox は止められるので false＝案内リンクだけ） */
  readonly canProbe: boolean;
  /** 利用者の操作のときだけ呼ぶ（ブラウザが許可を求めることがある） */
  probe(): Promise<LocalAppProbe>;
}
