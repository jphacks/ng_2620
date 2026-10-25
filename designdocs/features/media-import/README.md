# F02 media-import — 動画の取り込み・検証・アップロード

> **注（ADR-0015、2026-10-25）**：この文書の Tauri・Rust・`src-tauri` に関する記述は、Tauri 廃止（ブラウザ ＋ ローカルアプリサーバー）より前のものです。現在の構成は [ADR-0015](../../adr/0015-web-first-windows-local-server.md)・[tauri-migration.md](../../architecture/tauri-migration.md)・[mode-matrix.md](../mode-matrix.md) を正とします。


| 項目 | 内容 |
|---|---|
| レーン | A（サーバー側の受け口と `ingest` は C） |
| 優先度 | P0 |
| 関連 | FR-03, FR-04 / [ADR-0011](../../adr/0011-input-limits-and-resolutions.md) / [ADR-0012](../../adr/0012-transport-and-auth.md) / [contracts §2.1](../../architecture/contracts.md#21-一覧) |
| 依存 | F01（プロジェクト作成）、F03（接続）、F13（アップロード API） |

## ユーザーストーリー
- 配信者として、動画をドロップするだけで、使えるかどうかがすぐ分かり、使えないなら理由と直し方が知りたい。
- 回線が不安定でも、アップロードが途中から再開してほしい。

## 受け入れ条件
- [ ] ドロップまたはファイル選択で mp4 / mov を受け付ける。他の拡張子はドロップ時に拒否。
- [ ] **アプリ側の事前検証**（1 秒以内）：長さ・解像度・コーデック・音声の有無を表示。30 秒超・長辺 1920px 超は理由付きで拒否（[interaction §5](../../ux/interaction.md#5-エラー文言)）。
- [ ] 取り込み確認画面（S2-1）にプレビュー（`<video>` で元ファイルを再生）と送信先の接続名。
- [ ] 「解析を始める」で：サーバー側プロジェクト作成 → 分割アップロード（8MB、並列 3）→ `complete` → `analyze` ジョブ投入（F04）まで自動で進む。
- [ ] アップロード進捗（% と速度）。中断（アプリ終了・通信断）後に再開すると、`GET /v1/uploads/{id}` で受信済みの断片を確認し、残りだけ送る。
- [ ] サーバー側検証（`ingest`）の拒否（`MEDIA_*`）もアプリ側と同じ文言で出す。
- [ ] sha256 はアップロード前に計算（Rust 側、ストリーム）。同じ動画の再取り込みでは計算結果を再利用。

## 設計
- domain/validation.ts：`validateMedia(meta): Violation[]`（30 秒、1920px、形式）。上限値はサーバーの `capabilities.limits` で上書き可能。
- application/ports：
  ```ts
  interface MediaProbePort { probe(path: string): Promise<LocalMediaInfo> }   // duration, width, height, rotation, codec, fps, hasAudio
  interface FileHasherPort { sha256(path: string, onProgress?: (r: number) => void): Promise<string> }
  // アップロードは ComputeBackendPort（F03）に含める：
  //   createUpload, uploadChunk(uploadId, index, bytes), getUpload, completeUpload
  ```
- application/usecases：`ProbeMedia`、`ImportMedia`（プロジェクト作成＋sha256＋アップロード＋complete、進捗をコールバック）、`ResumeUpload`。
- infrastructure：
  - `TauriMediaProbe`：Rust で動画のメタ情報を読む。実装は **同梱の `ffprobe` サイドカー**（Tauri `externalBin`）を第一候補。間に合わなければ `<video>` 要素の `loadedmetadata`（duration・videoWidth/Height）で代用し、コーデック・音声はサーバーの `ingest` に任せる。
  - `TauriFileHasher`：Rust（`sha2`）でストリーム計算。
  - 断片の読み出しは Rust コマンド `read_chunk(path, offset, len) -> Vec<u8>`（WebView に動画全体を載せない）。
- presentation/features/import：ドロップ領域、S2-1 画面、アップロード進捗。

## サーバー側（レーン C、F13 / F04 と共有）
- `POST /v1/uploads` → `VVLOG_DATA_ROOT/uploads/<uploadId>/` に断片を保存、`complete` で結合・sha256 照合、`projects/<id>/source.<ext>` に移動。
- `ingest` ステージ（F04）が ffprobe で再検証、回転適用・CFR 化・フル / プロキシのフレーム書き出し・音声分離。

## タスク分解
1. domain：`validateMedia` ＋テスト（境界値：30.0 秒、1920px、縦動画の回転）。
2. Rust：`read_chunk`、`sha256_file`（進捗イベント）、`ffprobe` サイドカー同梱（macOS arm64 / Windows x64 の静的ビルド）。
3. `TauriMediaProbe`（ffprobe → 失敗時 `<video>` フォールバック）。
4. `ImportMedia` / `ResumeUpload` ユースケース＋テスト（ComputeBackend はモック）。
5. 画面（ドロップ領域、S2-1、進捗、エラー）。

## テスト
- アップロードの途中でモックサーバーを落とし、再起動後に残りの断片だけが送られる。

## P1 / スコープ外
- 取り込み時のトリミング（区間指定）（P1）、30 秒超の自動分割（スコープ外）。
