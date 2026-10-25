# Tauri 依存の棚卸しとポート／アダプタの再設計

[ADR-0015](../adr/0015-web-first-windows-local-server.md) で Tauri を廃止し、Windows 専用ローカルサーバー＋ブラウザ UI にした（親 #18、本書は #21）。
本書は、(1) 何が Tauri に依存しているか、(2) それぞれをどこへ移すか、(3) ポートとアダプタをどう組み替えるか、(4) フル／制限モードの判定をどう持つか、を定める。モードごとの機能差は [features/mode-matrix.md](../features/mode-matrix.md)。

## 1. 方針
- **ポート（`application/ports`）は変えない**。ADR-0003 のクリーンアーキテクチャを守り、差し替えるのは `infrastructure` のアダプタと `container.ts` の組み立てだけにする。
- アダプタは 3 系統にする。
  | 系統 | 使う場面 | 実装の置き場所 |
  |---|---|---|
  | **ローカルHTTP版** | フルモード。ローカルサーバーの API を `fetch` で呼ぶ | `infrastructure/*/http*.ts`（新設） |
  | **ブラウザ単体版** | 制限モード。IndexedDB・Web Crypto・`<video>` で完結 | `infrastructure/*/browser*.ts`（新設） |
  | **メモリ版** | テストと `pnpm dev:memory`（現行の `MemoryFs` など。残す） | 既存 |
- Tauri 版アダプタ（`tauri*.ts`、`Tauri*`）は、置き換えが済んだ時点で削除する（#29、#40）。
- Rust のコマンドは Python（FastAPI）に移植する。実装先は `services/gpu_server` を拡張したローカルアプリサーバー（#22）。

## 2. 依存の棚卸し

### 2.1 Rust コマンド（`src-tauri/src/commands`、約 800 行）

| コマンド | 責務 | 現行の呼び出し元 | 移植先 | チケット |
|---|---|---|---|---|
| `fs_exists` / `fs_stat` | 存在確認、サイズ・更新時刻・種別 | `tauriFs` | ローカル FS API（`HEAD`/`GET /v1/local/fs/stat`） | #23 |
| `fs_read_text` | UTF-8 テキストを読む | 同上 | ローカル FS API | #23 |
| `fs_write_atomic` | 一時ファイル→rename で書く（`private` は権限 600） | 同上 | ローカル FS API。Windows の rename 上書きに注意 | #23 |
| `fs_read_bytes` / `fs_read_chunk` | 全体／範囲の読み出し | 同上 | `GET` ＋ `Range`（巨大ファイルを全読みしない） | #23 |
| `fs_write_bytes` | バイト列を書く | 同上 | `PUT` | #23 |
| `fs_copy_file` / `fs_ensure_dir` / `fs_list_dir` / `fs_remove` | コピー・フォルダ作成・一覧・再帰削除 | 同上 | ローカル FS API | #23 |
| `fs_move_to_trash` | ゴミ箱へ移動 | 同上 | ローカル FS API（Windows は `send2trash` 相当） | #23 |
| `fs_unzip` | zip（store／deflate）を展開 | `FsLayerCache` | ローカル FS API（zip-slip 対策を入れる） | #23 |
| `media_probe` | ffprobe で長さ・解像度・回転・コーデック・fps・音声の有無 | `TauriMediaProbe` | ローカル media API（`POST /v1/local/media/probe`） | #24 |
| `hash_sha256` | ストリームで sha256、進捗を通知 | `TauriFileHasher` | ローカル hash API（進捗は SSE） | #24 |
| `net_download` | 成果物のダウンロード（`.part` と Range で再開、進捗通知） | `tauriTransport.download` | ローカルサーバーが外部 GPU へ中継するか、ブラウザが直接取得してローカル FS API へ書く（§3.3） | #25 |
| `net_upload_chunk` | ファイルの範囲を PUT（8MB 断片） | `tauriTransport.uploadFile` | 同上 | #25 |
| `server_start` / `server_stop` / `server_status` | GPU サーバーを子プロセスとして起動・停止・状態取得 | `tauriLocalServer`、`LocalServerPanel` | **不要になる**。ローカルサーバー自身がアプリ本体なので、起動は Windows ランチャー（#31）が担う | #31 |

### 2.2 フロントエンドの依存

| 場所 | Tauri 依存 | 置き換え | チケット |
|---|---|---|---|
| `infrastructure/fs/tauriFs.ts` | `invoke`（`fs_*`）、`convertFileSrc` | フル：`httpFs`（ローカル FS API）。`toUrl` はローカルサーバーの URL を返す。制限：`browserFs`（IndexedDB） | #26、#27 |
| `infrastructure/cache/TauriLayerCache.ts` | 名前だけ（実体は `FsLayerCache`＋`FileSystem`） | ファイル名を `FsLayerCache.ts` に変更。`FileSystem` を差し替えれば両モードで動く | #26 |
| `infrastructure/compute/transports.ts` | `tauri-plugin-http` の `fetch`、`net_download`、`net_upload_chunk` | フル：ブラウザの `fetch`（CORS は外部 GPU 側で許可、#36）。ダウンロード・アップロードは §3.3 | #25、#26 |
| `infrastructure/media/hashers.ts` | `hash_sha256` | フル：`HttpFileHasher`。制限：`WebCryptoFileHasher`（既存） | #24、#26 |
| `infrastructure/media/probes.ts` | `media_probe` | フル：`HttpMediaProbe`。制限：`VideoElementMediaProbe`（既存） | #24、#26 |
| `infrastructure/system/tauriSystem.ts` | `plugin-dialog`、`plugin-opener`、`plugin-notification`、`getCurrentWebview().onDragDropEvent` | §3.2・§3.4（#28 で決定済み） | #46、#47、#48 |
| `infrastructure/system/localServer.ts` | `server_*` | `LocalServerPort` ごと廃止（フルモードではサーバー＝自分自身）。`LocalServerPanel` は「接続先の状態表示」に作り替える | #29、#31 |
| `infrastructure/platform.ts` | `isTauri()`（`__TAURI_INTERNALS__`） | `detectMode()` に置き換え（§4） | #26 |
| `presentation/app/container.ts` | `@tauri-apps/api/path`（`appConfigDir`・`appDataDir`・`videoDir`）、`isTauri` 分岐 | パスはローカルサーバーが返す（`GET /v1/local/info` の `dirs`）。分岐は `mode` で行う | #26 |
| `presentation/*`（`AppRoot`・`TitleBar`・`LocalServerPanel`・`ConnectionSettings`・`GeneralSettings`・`connectionStore`）の `env.isTauri` | 7 か所 | `env.mode` と `env.capabilities`（§4）に置換。`TitleBar` の信号機（macOS のオーバーレイ）は廃止 | #29 |
| `package.json` | `@tauri-apps/api`・`plugin-*`・`@tauri-apps/cli`、`tauri` スクリプト | 削除 | #29 |
| `vite.config.ts` | 固定ポート 1420、`src-tauri` の watch 除外、`TAURI_ENV_` 接頭辞、`target: "safari16"` | ローカルサーバーが配信する `dist` と Pages 用 base（#37）に合わせる | #29、#37 |
| `src-tauri/**`（Cargo・`tauri.conf.json`・`capabilities`・icons・resources/samples） | — | 削除。サンプルプロジェクト（F12）は Pages／ローカルサーバーが静的配信する場所へ移す | #40 |
| ルート `package.json`・`lefthook.yml`・`.github/workflows/ci.yml`・`Makefile` | `tauri`、`lint:rust`、`format:rust`、Rust CI | 削除または Windows ビルドへ置換 | #40、#41 |

## 3. 再設計

### 3.1 `FileSystem` と `LocalFilePort`
- `FileSystem` インターフェース（`infrastructure/fs/FileSystem.ts`）は**そのまま維持**する。実装を 3 つ持つ。
  | 実装 | モード | 備考 |
  |---|---|---|
  | `HttpFileSystem` | フル | ローカル FS API を呼ぶ。`readChunk` は `Range`、`toUrl` は API の URL |
  | `BrowserFileSystem` | 制限 | IndexedDB 上の仮想ファイルシステム。パスは `MEM_ROOT` のような仮想ルート配下 |
  | `MemoryFs` | テスト・dev:memory | 既存 |
- **`toUrl` の置き換え**：`convertFileSrc`（asset プロトコル）の代わりに `http://127.0.0.1:<port>/v1/local/fs/raw?path=…&t=<トークン>` を返す。`<img>`・`<video>`・`createImageBitmap` はヘッダーを付けられないため、トークンはクエリで渡す（短命のセッショントークン。API のトークンとは別にする）。
- **パスの安全**：ローカル FS API はサーバーが許可したルート（プロジェクトの保存先・アプリデータ・設定）の配下にだけ作用させる（`..` と symlink を解決してから判定）。任意パスの読み書きは認めない。ルートの外にあるユーザーの動画は、読み取り専用の導出値（probe・sha256）だけをパスで受け付ける（#24）。ドロップされた動画はサーバーの作業フォルダへ取り込んでから扱う（§3.2）。

### 3.2 パス文字列を前提にしている箇所（#28 で決定済み）
現行ポートは**ファイルのパス文字列**を通す設計である（`DialogPort.pickFiles` は `string[]`、`FileDropEvent` は `paths`、`FileHasherPort.sha256(path)`・`MediaProbePort.probe(path)`・`LocalFilePort.readChunk(path, …)` もパスを受ける）。ブラウザは選ばれたファイルの実パスを教えない。

**決定（#28）：ポートは変えず、パスを保つ。** 実パスが得られない部分は、モードごとに次のとおり埋める。`FileRef` のような新しい型は導入しない（初版の本書は導入を提案したが、#28 の決定で不要になった）。

| 操作 | フル | 制限 |
|---|---|---|
| ファイル／フォルダ選択・保存先 | ローカルサーバーが Windows のネイティブダイアログを出し、**サーバー上の実パス**を返す（#46）。ローカル接続のときだけ有効 | ブラウザのファイル選択。`File` を `BrowserFileSystem` に登録し、**仮想パス**（`MEM_ROOT` 配下）を返す |
| ドロップ | `File` をローカルサーバーの作業フォルダへストリームで書き込み、以後は**サーバー上のパス**で扱う（#47） | `File` を仮想パスで登録し、メモリ／IndexedDB／GPU 直送に渡す（#27、#47） |
| probe／sha256／readChunk | パス（サーバー側で実行） | 仮想パス（ブラウザ側で実行） |

- どちらのモードでも `string` のパスを返すので、`application` 層とユースケースは変わらない（現行の `browserDialog(memFs)`・`browserFileDrop(memFs)` と同じ考え方）。
- `DialogPort` のフル版は `POST /v1/local/dialog/{open-files|open-dir|save}` を呼ぶ `HttpDialog`（#46）。`FileDropPort` のフル版は、ドロップ中の座標と `over`／`leave` をブラウザの `dragover`／`dragleave` から作り、`drop` ではアップロードを待ってからパスを渡す（#47）。座標は CSS ピクセルのまま。
- ネイティブダイアログはサーバー端末の画面に出るため、**ローカル接続（127.0.0.1＋起動トークン）以外では無効**にする。リモート接続や Pages 経由では出さない。
- ドロップで作業フォルダにコピーしたファイルは、プロジェクトの取り込みで不要になった時点で消す（掃除の方針は #47）。

### 3.3 大容量の転送（#25）
- **外部 GPU との通信はブラウザから直接 `fetch` する**（CORS・HTTPS・トークンを外部サーバー側で整える、#36）。ローカルサーバーを中継しない。
  - アップロード：`File.slice()` で 8MB 断片を取り出して `PUT`（再開可能、ADR-0012）。パスで指定された大容量ファイルは、ローカルサーバーの Range 読み出し（`GET`）から断片を取る。
  - ダウンロード：`fetch` のストリームを読み、フルモードでは断片をローカル FS API（`PUT` の追記）へ流して `<dest>.part` に書く。制限モードでは IndexedDB に書く。`Range` での再開は `<dest>.part` のサイズを見て行う。
  - 進捗は `fetch` のストリームから直接得られる。
- ローカル GPU（フルモードで自分自身に投入する場合）は同一サーバーなので、ファイルはサーバーが直接読めるようにし、HTTP を経由しない経路を `HttpTransport` の別実装（`LocalTransport`）として持たせる。

### 3.4 通知・外部を開く
| ポート | フル | 制限 |
|---|---|---|
| `NotifierPort.osNotify` | Web Notifications（許可されたときだけ）。未許可・拒否のときは画面内トーストとタブタイトル／ファビコンのバッジで代替する。許可の要求はジョブ開始などのユーザー操作から出す（Safari は必須）（#48） | 同左 |
| `ShellPort.reveal`／`ShellPort.open` | **今回は提供しない**（#28 で見送り。要望があれば `POST /v1/local/shell/*` を許可ルート配下に限って後から足す） | 提供しない |
| `ShellPort.openUrl` | `window.open(url, "_blank", "noopener")` | 同左 |

### 3.5 シークレットと設定
- フル：`FileSettings`／`FileSecretStore`（`FileSystem` 経由）はそのまま使える。保存先はローカルサーバーの設定フォルダ。ブラウザの `localStorage` にはトークンを置かない。
- 制限：`LocalStorageSettings`／`LocalStorageSecretStore`（既存）。
- 変更が要るのは、保存先のパス取得だけ（`@tauri-apps/api/path` の代わりに `GET /v1/local/info`）。

### 3.6 ローカルGPUサーバーの起動・停止
- `LocalServerPort`・`StartLocalServer` ユースケース・`LocalServerPanel` は廃止する（フルモードではサーバー＝画面の配信元）。
- 接続プロファイルの「種別：ローカル」は、`http://127.0.0.1:<自分のポート>` を指す固定のプロファイルとして自動登録する。トークンは起動トークン（#22）から得る。
- `connectionStore` の `localServer.autoStart` は廃止する。

## 4. モード判定（`platform.ts`）
`isTauri()` を `detectMode()` に置き換える。

```ts
export type AppMode = "full" | "restricted";

export interface Capabilities {
  localFs: boolean;      // プロジェクトフォルダを直接読み書きできる
  localGpu: boolean;     // この PC の GPU で推論できる
  localShell: boolean;   // エクスプローラで表示などができる
  nativeDialogs: boolean; // サーバー側のネイティブダイアログが使える（ローカル接続のときだけ）
}

export async function detectMode(origin: Location): Promise<{ mode: AppMode; capabilities: Capabilities; local?: LocalInfo }>;
```

- 判定は**起動時に 1 回**、同一オリジンの `GET /v1/local/info` を試す。
  - 成功（`200`）→ フル。`dirs`・`gpu`・`version` を得て、`capabilities` を埋める。`gpu` が無い（CUDA なし）場合は `localGpu = false` のフルモードになりうる。
  - 失敗（接続不可・`404`）→ 制限。GitHub Pages ではここに落ちる。
- Pages 版からローカルサーバーが見つかったとき（`http://127.0.0.1:<既定ポート>` に `info` が返る）は、バナーで昇格を案内する（#38）。混在コンテンツ（HTTPS ページから `http://127.0.0.1` への通信）は、ブラウザが localhost を例外として許可するかを #38 で検証する。
- `container.ts` は `mode` と `capabilities` だけを見てアダプタを選ぶ（`createServices(mode)`）。`presentation` は `env.mode`／`env.capabilities` だけを見て、`isTauri` を使わない。
- `Services.env` を `{ mode, capabilities, isDev, backendMode, version }` に改める。`isMac`（`TitleBar`）はウィンドウ装飾の廃止とともに不要になる。

## 5. 後続チケットの粒度の確認
各チケットの完了条件に、本書で追加または確定した事項を反映する。

| チケット | 本書で確定したこと |
|---|---|
| #22 ローカルサーバー | `GET /v1/local/info`（モード判定用・認証なし）、セッショントークン（`raw` 用）、`127.0.0.1` 限定、許可ルートの概念 |
| #23 ローカルFS API | §2.1 の `fs_*` 13 個。Range 読み出し、zip-slip 対策、パスのルート制限、`raw` エンドポイント（`toUrl` 用） |
| #24 media/hash API | `media_probe`、`hash_sha256`（進捗は SSE）。ドロップ分は #47 で作業フォルダへ取り込んでからパスで渡すので、`file` を受ける口は要らない |
| #25 大容量転送 | §3.3。ブラウザから外部 GPU への直接 `fetch`、`.part` と Range の再開 |
| #26 アダプタ差し替え | `HttpFileSystem`／`HttpFileHasher`／`HttpMediaProbe`／`LocalTransport`、`TauriLayerCache.ts` の改名、`container.ts` の `mode` 分岐 |
| #27 制限モード | `BrowserFileSystem`（IndexedDB）、zip の保存／読み込み、`WebCryptoFileHasher`・`VideoElementMediaProbe` の上限 |
| #28 → #46／#47／#48 | §3.2・§3.4 のとおり決定済み。ダイアログ API は #46、ドロップのストリーム取り込みは #47、通知は #48 |
| #29 Tauri 依存除去（フロント） | §2.2 の `package.json`・`vite.config.ts`・`isTauri` 7 か所・`TitleBar`・`LocalServerPanel`・`LocalServerPort` の廃止 |
| #31 ランチャー | `server_start/stop/status` の責務（起動・多重起動防止・終了）を引き継ぐ |
| #40 Tauri 削除 | `src-tauri/**`、`lefthook.yml`・ルート `package.json`・`Makefile` の Rust 関連 |

### 追加で切るべきか（提案）
- **F12 サンプルの配置**：`src-tauri/resources/samples` は Tauri の `bundle.resources` で同梱していた。Pages とローカルサーバーの両方から取れる静的配信の場所（例 `apps/desktop/public/samples/`）へ移す作業が要る。#37 と #40 のどちらにも明記されていないので、#37 に含めるか別チケットにする。

## 6. 未確定
- 外部 GPU の CORS／HTTPS 要件が満たせない環境でのローカルサーバー中継（フォールバック）を持つか。持たない場合、BYO の導入手順（#43）に CORS 設定を必須として書く。
- HTTPS の Pages から `http://127.0.0.1` へ通信できるか。#28 の決定で Safari は昇格を諦め案内リンクのみとし、Chrome／Edge で #38 が実測する。
