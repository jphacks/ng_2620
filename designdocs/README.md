# designdocs — vvlog（virtual vlog）設計ドキュメント

実写動画の人物を VRM アバターに置き換えるデスクトップアプリ（Adobe 系の制作ツールを将来像とする）の、
ハッカソン 2 日間（Day 1 / Day 2）で作り切るための**決定版**設計。

- このリポジトリ（`vlog_JPHack2026`）は推論パイプラインの研究・検証用。**アプリの実装はハッカソン指定の別リポジトリ**で行い、
  パイプラインとモデルもそちらへ移す（[ADR-0008](adr/0008-implementation-repo-and-model-placement.md)）。
- 開発は Claude Code 3 アカウント（3 レーン）以上の並列を前提に、契約（API・成果物形式・プロジェクト形式）を先に固定してから各レーンが独立に進む。

## 読む順番

| 誰が | 読むもの |
|---|---|
| 全員（最初の 30 分） | [PRD](PRD.md) → [architecture/overview](architecture/overview.md) → [architecture/parallel-plan](architecture/parallel-plan.md) → 自分のレーンの feature |
| 実装中に参照 | [architecture/contracts](architecture/contracts.md)（API・成果物・プロジェクト形式の正）、[ux/](ux/README.md)（画面・操作の正） |
| 判断に迷ったら | [adr/](adr/README.md)（なぜそう決めたか・何をやらないか） |

## 構成

```
designdocs/
  README.md                 この索引
  PRD.md                    プロダクト要求（誰の・何の課題を・何で解くか、機能要件一覧、非機能要件、スコープ外）
  glossary.md               用語集（人物/ターゲット/遮蔽物ユニット/レイヤー/プロキシ…）
  adr/                      Architecture Decision Records（決定とその理由、却下した案）
  architecture/
    overview.md             システム構成、クリーンアーキテクチャの層、実装リポジトリのディレクトリ構成
    contracts.md            GPU サーバー API（OpenAPI の元）、イベント、成果物（レイヤー）形式、プロジェクトファイル形式、合成規則
    parallel-plan.md        レーン分割、2 日間のマイルストーン、依存関係、ブランチ運用、Definition of Done
    pipeline-integration.md 既存パイプライン（scripts/）をサーバーのステージに載せる方法と必要な改修
  ux/
    README.md               UX 原則、情報設計、画面遷移
    screens.md              画面ごとのワイヤーフレームと状態（空・処理中・エラー）
    interaction.md          ショートカット、操作仕様、文言
    demo-script.md          デモ台本と障害時の切り替え手順
  features/<機能名>/README.md  機能要件ごと（ユーザーストーリー、受け入れ条件、層ごとの設計、API、タスク分解、担当レーン）
```

## 機能一覧（features/）

| ID | 機能 | 担当レーン | 優先度 |
|---|---|---|---|
| F01 | [project-management](features/project-management/README.md) プロジェクトの作成・保存・再開 | A | P0 |
| F02 | [media-import](features/media-import/README.md) 動画の取り込み・検証・アップロード | A（+C） | P0 |
| F03 | [compute-connection](features/compute-connection/README.md) GPU サーバー接続（ローカル Windows / リモート） | A（+C） | P0 |
| F04 | [analysis-pipeline](features/analysis-pipeline/README.md) 解析ジョブ（追跡・メッシュ・遮蔽判定） | C | P0 |
| F05 | [job-progress-queue](features/job-progress-queue/README.md) ジョブ進捗・キュー・通知・段階的プレビュー | A（+C） | P0 |
| F06 | [cast-selection](features/cast-selection/README.md) 人物一覧から置換対象を選ぶ | A | P0 |
| F07 | [avatar-library](features/avatar-library/README.md) VRM の取り込み・一覧・ライセンス表示 | A | P0 |
| F08 | [avatar-render](features/avatar-render/README.md) アバター描画ジョブとレイヤー生成 | C | P0 |
| F09 | [editor-viewer](features/editor-viewer/README.md) エディタ（ビューア・タイムライン・プレビュー合成） | B | P0 |
| F10 | [occlusion-editing](features/occlusion-editing/README.md) 前後関係の手修正 | B（+C） | P0 |
| F11 | [export](features/export/README.md) MP4（H.264＋音声）書き出し | C（+A） | P0 |
| F12 | [demo-mode](features/demo-mode/README.md) 処理済みサンプルプロジェクトとオフライン動作 | B | P0 |
| F13 | [gpu-server-runtime](features/gpu-server-runtime/README.md) サーバー基盤（ジョブ実行・成果物保存・認証・配備） | C | P0 |
| F14 | [mock-server-fixtures](features/mock-server-fixtures/README.md) モックサーバーと固定データ（並列開発の土台） | C（最初の 2h） | P0 |
| F15 | [app-shell](features/app-shell/README.md) アプリ枠（ワークスペース・パネル・設定・テーマ） | A | P0 |

レーン: **A = デスクトップ（ワークフロー）**、**B = エディタ（ビューア・合成・手修正）**、**C = GPU サーバー（推論・書き出し）**。
詳細は [parallel-plan](architecture/parallel-plan.md)。
