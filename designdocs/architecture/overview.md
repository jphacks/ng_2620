# アーキテクチャ概要

関連 ADR：[0001](../adr/0001-desktop-framework-tauri.md) / [0002](../adr/0002-gpu-server-and-compute-backend-port.md) / [0003](../adr/0003-clean-architecture-and-contract-first.md) / [0005](../adr/0005-preview-composite-client-export-server.md) / [0014](../adr/0014-server-stack.md)

## 1. システム構成

```
┌───────────────────────── Mac / Windows（デスクトップアプリ: Tauri） ─────────────────────────┐
│  presentation (React)  ──▶ application (use cases) ──▶ ports ◀── infrastructure (adapters)     │
│   ウィザード / エディタ / 書き出し        │                         ├ HttpComputeBackend ──────────┼──┐ HTTPS + SSE
│   WebGL 合成（プレビュー）              domain（合成規則・エンティティ）  ├ TauriProjectRepository（*.vvlog）│  │ (Bearer)
│                                                                    ├ TauriLayerCache            │  │
│  src-tauri (Rust): fs / 子プロセス(ローカルサーバー起動) / keychain  └ OfflineComputeBackend      │  │
└────────────────────────────────────────────────────────────────────────────────────────────┘  │
                                                                                                │
        ┌──────────────── ローカル: http://127.0.0.1:8765（Windows + NVIDIA）──────────────────────┤
        └──────────────── リモート: https://<tunnel>（Windows 機 / クラウド GPU VM）────────────────┘
                                                                                                ▼
┌──────────────────────────────── GPU サーバー（services/gpu_server: FastAPI） ─────────────────────────┐
│ interface (routers, SSE) ─▶ application (use cases, ports) ─▶ domain (Job 状態機械, Stage, Artifact)   │
│                                   ▲                                                                │
│ adapters: SubprocessPipelineRunner ─▶ pipeline/（offline_app_lite.py, run_fusion_v2.py, avatar_app.py）│
│           FsArtifactStore（VVLOG_DATA_ROOT）, InProcessJobQueue（GPU 1 枚 = 1 ワーカー）, FfmpegMedia      │
└───────────────────────────────────────────────────────────────────────────────────────────────────┘

開発時: services/mock_server（同じ API、fixtures/ の処理済みデータを段階的に返す）
```

## 2. 主要なデータの流れ

| 段 | アプリ | サーバー | データ |
|---|---|---|---|
| 1 取り込み | 動画を検証（ffprobe 相当は Rust 側 / サーバーの `ingest` で再検証）→ プロジェクト作成 → 分割アップロード | `POST /v1/projects`、`/v1/uploads` | ソース動画 |
| 2 解析 | `analyze` ジョブ投入、SSE 購読、届いた成果物を `cache/` に保存してプレビュー | `ingest`→`track`→`mesh`→`occlusion` | プロキシフレーム、人物一覧、マスク、メッシュオーバーレイ、遮蔽物ユニット |
| 3 キャスト | `track` 完了後に人物を選び VRM を割当（`mesh` / `occlusion` は裏で進行）→ VRM をアップロード | `POST /v1/avatars` | VRM |
| 4 描画 | `render` ジョブ投入（キャスト・仕上げパラメータ） | `removal`→`avatar`→`layers` | プレビュー用レイヤー（zip） |
| 5 編集 | WebGL で合成、手修正は `overrides.json` に保存（サーバー往復なし） | — | — |
| 6 書き出し | `export` ジョブ投入（手修正・設定）→ 完成した MP4 をダウンロード | `composite`→`encode` | MP4 |

## 3. クリーンアーキテクチャの規則（両側共通）

- 依存は **presentation/interface → application → domain** の一方向。infrastructure/adapters は application のポートを実装し、組み立て（DI）は composition root（アプリ：`src/main.tsx` の `container.ts`、サーバー：`main.py`）だけで行う。
- **domain** は外部ライブラリ・I/O に依存しない（React・fetch・Tauri・FastAPI・torch を import しない）。
- **ポート**（interface）は application に置く。名前は `XxxPort`、実装は `<技術名>Xxx`（例：`HttpComputeBackend implements ComputeBackendPort`）。
- ユースケースは 1 クラス（または 1 関数）1 ユースケース、`execute(input): Promise<output>`。UI はユースケースだけを呼ぶ。
- feature 間の直接 import は禁止。共有するものは `domain/`・`application/ports/`・`presentation/shared/` に置く。
- 契約（API 型・スキーマ）は `packages/contracts` から生成・取り込みし、手書きしない。

## 4. デスクトップアプリの層とディレクトリ

```
apps/desktop/
  src-tauri/                     Rust（薄く）
    src/commands/fs.rs           プロジェクトフォルダの原子的書き込み、zip 展開、ファイルコピー
    src/commands/media.rs        動画のメタ情報（ffprobe サイドカー or サーバーに任せる、F02 参照）
    src/commands/server.rs       ローカル GPU サーバーの起動・停止（Windows、P1）
    src/commands/secrets.rs      トークンの保存（keychain、P1）
  src/
    domain/                      外部依存なし・純粋
      project.ts                 Project, SourceMedia, JobRef, schemaVersion, マイグレーション
      cast.ts                    Person, CastEntry(action='replace'), Avatar
      occlusion.ts               OccluderUnit, OcclusionOverride, 区間演算, applyOverrides()（unit×frame→mode）
      composite.ts               合成規則（CPU 版の参照実装＝テスト用、シェーダーと同じ式）
      job.ts                     Job, Stage, JobEvent, 状態遷移
      validation.ts              入力制約（30 秒、1920px、形式）
    application/
      ports/                     ComputeBackendPort, ProjectRepositoryPort, LayerCachePort, AvatarLibraryPort,
                                 MediaProbePort, SettingsPort, ClockPort, NotifierPort
      usecases/                  CreateProject, OpenProject, ImportMedia, StartAnalysis, SubscribeJob, SelectCast,
                                 ImportAvatar, StartRender, FetchLayers, SetOverride, UndoOverride, StartExport, ...
    infrastructure/
      compute/HttpComputeBackend.ts, compute/OfflineComputeBackend.ts, compute/sse.ts
      project/TauriProjectRepository.ts, project/InMemoryProjectRepository.ts
      cache/TauriLayerCache.ts
      avatar/TauriAvatarLibrary.ts, avatar/vrmMeta.ts（glTF の拡張 VRM/VRMC_vrm からメタ情報を読む）
      settings/TauriSettings.ts
    presentation/
      app/                       ルーティング、ワークスペース切替、container.ts（DI）
      shared/                    UI 部品、デザイントークン、i18n/ja.ts、hooks
      features/
        home/                    F01
        import/                  F02
        connection/              F03
        jobs/                    F05（ジョブパネル・通知・トースト）
        cast/                    F06
        avatar-library/          F07
        editor/                  F09（viewer/, timeline/, compositor/（WebGL）, inspector/）
        occlusion-edit/          F10
        export/                  F11
        settings/                F03 / F15
```

## 5. GPU サーバーの層とディレクトリ

```
services/gpu_server/
  vvlog_server/
    domain/                      Job（状態機械 queued→running→succeeded|failed|canceled）, Stage, Artifact, Project, 例外
      composite.py               合成規則（アプリの domain/composite.ts と同じテストケースを通す）
      overrides.py               手修正の適用（unit×区間→フレームごとの前/後ろ）
    application/
      ports.py                   PipelineRunnerPort, ArtifactStorePort, JobQueuePort, EventBusPort, MediaPort, ClockPort
      usecases/                  create_project, upload_chunk, finalize_upload, submit_job, cancel_job, get_job,
                                 stream_events, list_artifacts, upload_avatar, delete_project
      stages/                    ステージ定義（入力→出力→進捗の解釈）：ingest, track, mesh, occlusion, removal,
                                 avatar, layers, composite, encode
    adapters/
      pipeline_subprocess.py     pipeline/ のスクリプトをサブプロセス実行、標準出力→進捗
      artifact_fs.py             VVLOG_DATA_ROOT 配下
      queue_inprocess.py         GPU 1 枚につき 1 ワーカー
      events_memory.py           SSE 用のイベントバッファ（Last-Event-ID 対応）
      media_ffmpeg.py            ffprobe / ffmpeg（CFR 化、回転、音声の分離と再多重化）
    interface/
      api.py                     FastAPI ルーター（/v1/...）、認証ミドルウェア
      schemas.py                 pydantic（packages/contracts/openapi.yaml と契約テストで照合）
    main.py                      composition root、設定（環境変数）
  tests/                         domain / usecase / 契約テスト
  requirements.lock
pipeline/                        既存パイプライン（ADR-0008）。サーバーからはサブプロセスでのみ呼ぶ
```

## 6. 設定（環境変数、サーバー）

| 変数 | 既定 | 意味 |
|---|---|---|
| `VVLOG_API_TOKEN` | （必須） | Bearer トークン |
| `VVLOG_DATA_ROOT` | `./data` | プロジェクト・ジョブ・成果物の保存先 |
| `VVLOG_CKPT_ROOT` | `./weights` | 重みの場所（`configs/body4d.yaml` の `ckpt_root` を上書き） |
| `VVLOG_PORT` | `8765` | 待受ポート |
| `VVLOG_GPU_IDS` | `0` | ワーカーを割り当てる GPU（カンマ区切り。P0 は 1 枚） |
| `VVLOG_MAX_TARGETS` | `4` | 追跡する人数の上限 |
| `VVLOG_FUSION_DEPTH` | `da2_indoor_s` | fusion v2 の深度モデル（`FUSION_DEPTH`） |
| `VVLOG_TRACKER_SIZE` | `672` | 追跡の入力解像度（`--tracker_size`） |
| `VVLOG_HMR_STRIDE` | `3` | SAM 3D Body を回すフレーム間隔（`--hmr_stride`。1 で毎フレーム） |

## 7. エラーの扱い（共通）

- サーバーのエラーは `{"error": {"code": "...", "message": "...", "detail": {...}}}`（[contracts §2.3](contracts.md#23-エラー)）。`code` は列挙で、アプリは `code` ごとに文言と対処を出す（[ux/interaction](../ux/interaction.md#5-エラー文言)）。
- ジョブの失敗は `job_failed` イベント（`code`、失敗したステージ、ログの末尾）。アプリは「再試行」「ログを見る」を出す。
- 通信断：SSE は自動再接続（指数バックオフ、最大 30 秒）。その間はステータスバーに「再接続中」。ジョブ自体はサーバーで続く。
