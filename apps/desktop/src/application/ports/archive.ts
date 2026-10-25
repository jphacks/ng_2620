// プロジェクトの zip 書き出し／読み込み（制限モードの保存手段、#27）。ブラウザの保存領域は消えることがあるので、zip が『本当の保存』

export interface ProjectArchivePort {
  /** プロジェクトフォルダ → zip。cache/ は入れない（サーバーから取り直せる） */
  pack(projectPath: string): Promise<Uint8Array>;
  /** zip → destPath のプロジェクトフォルダ。project.json が無い zip や、外へ出る項目のある zip は PROJECT_INVALID */
  unpack(zip: Uint8Array, destPath: string): Promise<void>;
}

/** ブラウザとのファイルのやり取り（ダウンロードさせる／ファイルを選ばせる）。フルモードでは提供しない */
export interface FileExchangePort {
  download(filename: string, bytes: Uint8Array, mime?: string): Promise<void>;
  /** 選ばれなければ null */
  pickFile(opts: { accept: string[] }): Promise<{ name: string; bytes: Uint8Array } | null>;
}
