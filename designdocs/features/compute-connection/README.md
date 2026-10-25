# F03 compute-connection — GPU サーバー接続（ローカル Windows / リモート）

> **注（ADR-0015、2026-10-25）**：この文書の Tauri・Rust・`src-tauri` に関する記述は、Tauri 廃止（ブラウザ ＋ ローカルアプリサーバー）より前のものです。現在の構成は [ADR-0015](../../adr/0015-web-first-windows-local-server.md)・[tauri-migration.md](../../architecture/tauri-migration.md)・[mode-matrix.md](../mode-matrix.md) を正とします。


| 項目 | 内容 |
|---|---|
| レーン | A（サーバーの health / capabilities / 認証は C） |
| 優先度 | P0 |
| 関連 | FR-05 / [ADR-0002](../../adr/0002-gpu-server-and-compute-backend-port.md) / [ADR-0012](../../adr/0012-transport-and-auth.md) |
| 依存 | F15。モックサーバー（F14）で開発 |

## ユーザーストーリー
- Mac ユーザーとして、チームの GPU サーバー（リモート）の URL とトークンを登録し、つながるか確かめたい。
- Windows ユーザーとして、自分の PC の GPU（ローカル）で処理したい。
- どちらの経路でも、アプリの使い方は同じであってほしい。

## 受け入れ条件
- [ ] 設定 > 接続：プロファイルの追加・編集・削除・既定の選択。項目：名前、種別（ローカル / リモート）、URL、トークン。
- [ ] [接続テスト]：`/v1/health`（認証なし）→ `/v1/capabilities`（認証あり）を順に呼び、GPU 名・VRAM・キュー長・API 版を表示。失敗は段階別に（「サーバーに届かない」「トークンが違う」「API 版が合わない」）。
- [ ] リモートで `http://`（非 TLS）を登録しようとしたら警告（127.0.0.1 / localhost は除く）。
- [ ] ステータスバーに現在の接続（●緑 / 黄 / 灰）。30 秒ごとに health を確認、失敗 2 回で「再接続中」。
- [ ] プロジェクトは作成時のプロファイル ID を記録し、開いたときに別の接続が既定なら「このプロジェクトは remote-win で解析されています。切り替えますか？」。
- [ ] モックサーバーを `http://127.0.0.1:8766` の「開発用モック」プロファイルとして初期登録（開発ビルドのみ）。

## 設計
- domain：`ComputeProfile { id, name, kind: "local" | "remote", baseUrl, tokenRef }`、`ConnectionState`。
- application/ports（**このアプリの中心のポート**）：
  ```ts
  interface ComputeBackendPort {
    health(): Promise<Health>;
    capabilities(): Promise<Capabilities>;
    createProject(name: string): Promise<{ projectId: string }>;
    getProject(projectId: string): Promise<ServerProject>;
    deleteProject(projectId: string): Promise<void>;
    createUpload(req: CreateUploadReq): Promise<{ uploadId: string; chunkSize: number }>;
    uploadChunk(uploadId: string, index: number, bytes: Uint8Array): Promise<void>;
    getUpload(uploadId: string): Promise<{ receivedChunks: number[] }>;
    completeUpload(uploadId: string): Promise<{ mediaId: string; meta: MediaMeta }>;
    registerAvatar(file: { name: string; bytes: Uint8Array }): Promise<{ avatarId: string }>;
    hasAvatar(avatarId: string): Promise<boolean>;
    submitJob(projectId: string, type: JobType, params: JobParams): Promise<Job>;
    getJob(jobId: string): Promise<Job>;
    listJobs(projectId: string): Promise<Job[]>;
    queue(): Promise<QueueItem[]>;
    cancelJob(jobId: string): Promise<void>;
    events(jobId: string, lastEventId?: string): AsyncIterable<JobEvent>;   // SSE
    listArtifacts(jobId: string): Promise<Artifact[]>;
    downloadArtifact(jobId: string, name: string, dest: string, onProgress?): Promise<void>;  // Rust 側でディスクへ
    logs(jobId: string, tail?: number): Promise<string>;
  }
  interface ComputeBackendFactory { forProfile(p: ComputeProfile): ComputeBackendPort }
  interface SecretStorePort { get(ref: string): Promise<string | null>; set(ref: string, v: string): Promise<void> }
  ```
- usecases：`ListProfiles`、`SaveProfile`、`TestConnection`、`SelectActiveProfile`、`WatchConnection`（定期 health）。
- infrastructure：
  - `HttpComputeBackend`：`openapi-fetch`（生成型）。HTTP は `tauri-plugin-http`（Rust 側の fetch。CORS と任意 URL の許可を回避）。SSE は `compute/sse.ts`（`fetch` のストリームを行単位で解釈、`Last-Event-ID`、指数バックオフ再接続、`heartbeat` 監視）。
  - ダウンロードは Rust コマンド `download_to(url, headers, dest)`（Range 再開、進捗イベント）→ zip は `unzip_to(dest)`。
  - `OfflineComputeBackend`：health は失敗、ジョブ系は `OFFLINE` エラー。サンプルプロジェクト（F12）で使用。
  - `TauriSecretStore`：P0 はアプリ設定ファイル（`settings.json`、権限 600）、P1 で keychain（`tauri-plugin-stronghold` or OS keychain）。
- presentation/features/connection：設定の接続タブ、テスト結果、ステータスバーのインジケータ。

## ローカル（Windows）経路
- P0：ユーザーがサーバーを手で起動（`services/gpu_server/run_local.ps1`）し、`http://127.0.0.1:8765` のローカルプロファイルで接続。
- P1：アプリが子プロセスとしてサーバーを起動・停止（Rust `server.rs`、Python の場所は設定で指定）。

## タスク分解
1. ポート定義（`application/ports/compute.ts`）— **D1 +2h までに main へ**（A・B が使う）。
2. `sse.ts`（行パーサ、再接続、Last-Event-ID）＋テスト（切断の再現）。
3. `HttpComputeBackend`（生成型、tauri-plugin-http）、Rust の `download_to` / `unzip_to`。
4. `OfflineComputeBackend`、`InMemoryComputeBackend`（ユニットテスト用：イベント列を流す）。
5. プロファイルの保存（設定ファイル）、`TestConnection` ほかユースケース。
6. 設定画面・ステータスバー表示。

## テスト
- SSE：途中で切断 → `Last-Event-ID` で再開して重複・欠落なくイベントが届く（モックサーバー）。
- トークン違い → `UNAUTHORIZED` の文言。

## P1 / スコープ外
- keychain 保存（P1）、ローカルサーバーの自動起動（P1）、複数サーバーへの負荷分散（スコープ外）、SSO（スコープ外）。
