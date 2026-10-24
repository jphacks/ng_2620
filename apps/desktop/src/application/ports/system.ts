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
  | { type: "drop"; x: number; y: number; paths: string[] }
  | { type: "leave" };

export interface FileDropPort {
  subscribe(listener: (ev: FileDropEvent) => void): () => void;
}

export interface NotifierPort {
  /** OS 通知（アプリが背面のとき）。許可が無ければ何もしない */
  osNotify(title: string, body?: string): Promise<void>;
}

export interface ClockPort {
  now(): Date;
  sleep(ms: number, signal?: AbortSignal): Promise<void>;
}

export interface IdPort {
  /** 接頭辞付きの ID（例 loc_6f1c…） */
  newId(prefix: string): string;
}
