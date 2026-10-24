# 並列開発計画（3 アカウント＝3 レーン以上）

前提：Claude Code 3 アカウント（A / B / C）。各アカウントはさらにサブエージェントで feature 内の層（domain / application / infrastructure / presentation）を並列に進めてよい。
時刻は各日の作業開始からの経過時間（`D1 +0h` = Day 1 の開始）。

## 1. レーン

| レーン | 担当 feature | 成果物 | 主に触るディレクトリ |
|---|---|---|---|
| **A デスクトップ（ワークフロー）** | F15 app-shell, F01 project-management, F02 media-import, F03 compute-connection, F05 job-progress-queue（UI）, F06 cast-selection, F07 avatar-library, F11 export（UI） | アプリ枠とウィザード、ホーム、設定、キャスト、書き出しダイアログ | `apps/desktop/src/presentation/{app,shared,features/{home,import,connection,jobs,cast,avatar-library,export,settings}}`, `apps/desktop/src/infrastructure/{project,avatar,settings,compute}`, `apps/desktop/src-tauri` |
| **B エディタ** | F09 editor-viewer, F10 occlusion-editing, F12 demo-mode | ビューア、タイムライン、WebGL 合成、前後関係の手修正、サンプルプロジェクト | `apps/desktop/src/presentation/features/{editor,occlusion-edit}`, `apps/desktop/src/domain/{composite,occlusion}.ts`, `apps/desktop/src/infrastructure/cache` |
| **C GPU サーバー** | F14 mock-server-fixtures（最初）, F13 gpu-server-runtime, F04 analysis-pipeline, F08 avatar-render, F11 export（サーバー） | モックサーバー、フィクスチャ、FastAPI、ステージ、パイプライン改修、配備 | `services/`, `pipeline/`, `models/`, `weights/`, `fixtures/`, `packages/contracts`（オーナー） |

`apps/desktop/src/domain/`・`application/ports/` は A と B が共有する。**変更は小さな PR で即 main へ**（他レーンは都度 rebase）。所有者：`project.ts`・`job.ts`・`cast.ts` = A、`composite.ts`・`occlusion.ts` = B。

## 2. マイルストーン

| 時点 | マイルストーン | 完了条件 |
|---|---|---|
| **D1 +2h** | **M0 契約凍結** | `packages/contracts/`（openapi.yaml、project/overrides の JSON Schema、CHANGELOG）が main に入る。TS 型が生成できる。合成フィクスチャ v0（合成用の人工データ）が入る |
| D1 +3h | M0.5 骨組み | A：Tauri アプリが起動し、空のワークスペースとホームが出る。B：v0 フィクスチャをビューアで再生。C：モックサーバーが全エンドポイントに応答、`pipeline/` が GPU 機で e2e を 1 本通す |
| **D1 終了** | **M1 モックで端から端まで** | モックサーバー相手に「新規 → 取り込み → 解析（段階的プレビュー）→ キャスト → 描画 → エディタ → 手修正 → 書き出し（モックの mp4 をダウンロード）」が通る。C：実サーバーで `analyze` が通り、実データのフィクスチャ v1 がモックに入る |
| D2 +4h | **M2 実サーバー結合** | 実 GPU サーバー（リモート経由）で全フローが通る。手修正が書き出しに反映される |
| D2 +7h | **M3 デモ凍結** | サンプルプロジェクト同梱、デモ台本で通し 2 回、既知の不具合リスト。以後はバグ修正のみ |
| D2 終了 | 発表 | [demo-script](../ux/demo-script.md) |

## 3. 時間割（各レーン）

### Day 1

| 時間 | A | B | C |
|---|---|---|---|
| +0〜2h | 契約レビュー（UI 側の要求を出す）、Tauri＋React 雛形、デザイントークン、DI コンテナ、`ComputeBackendPort` 定義 | 契約レビュー（合成規則・レイヤー形式）、合成フィクスチャ v0 の生成スクリプト、`domain/composite.ts`＋テスト | **契約の起草と凍結（オーナー）**、モックサーバー（固定応答）、`pipeline/` 移植開始 |
| +2〜5h | F15 アプリ枠、F01 ホーム・プロジェクト保存、F03 接続設定（モック相手） | F09 ビューア（WebGL 合成、プロキシフレームのキャッシュ）、タイムライン | モックサーバーの段階的イベント再生、F13 サーバー骨格（ジョブ・キュー・SSE・成果物）、GPU 機で e2e |
| +5〜9h | F02 取り込み＋アップロード、F05 ジョブパネル・段階的プレビュー、F06 キャスト、F07 アバターライブラリ | F10 手修正（ユニットのクリック選択、区間、前/後ろ、元に戻す）、インスペクタ | F04 `analyze`（ingest・track・mesh・occlusion）、§3.1 人物一覧、§3.2 ユニット、フィクスチャ v1 |

### Day 2

| 時間 | A | B | C |
|---|---|---|---|
| +0〜4h | F11 書き出しダイアログ、実サーバー結合（接続・アップロード・SSE の実地試験）、エラー表示 | 実フィクスチャ v1 で合成の一致確認（`preview_video` と比較）、F12 サンプルプロジェクト、オフライン動作 | F08 `render`（キャスト・VRM・レイヤー書き出し）、F11 `export`（手修正の適用・音声）、リモート配備（トンネル） |
| +4〜7h | 結合バグ修正、文言・空状態・ショートカットの仕上げ | 操作感の仕上げ（スクラブ・再生の 30fps 維持）、P1（ブラシ）は余力があれば | デモ用ジョブの事前実行、サンプルプロジェクトの書き出し、P1（`render` のみ再実行） |
| +7h〜 | デモ通し、凍結 | デモ通し、凍結 | サーバー監視、障害時の切替手順 |

## 4. 依存関係とモックによる切り離し

```
契約(M0) ─┬─▶ A（モックサーバーで開発）────────────────┐
          ├─▶ B（フィクスチャ v0 → v1 で開発）──────────┼─▶ M2 結合
          └─▶ C モックサーバー ─▶ 実サーバー ─▶ フィクスチャ v1 ┘
```

| 依存 | 切り離し方 |
|---|---|
| A の全画面 → サーバー API | モックサーバー（F14）。`VVLOG_MOCK_SPEED=10` でイベントを 10 倍速で再生 |
| B のエディタ → 実際のレイヤー | 合成フィクスチャ v0（人工の図形：動く円＝アバター、四角＝遮蔽物ユニット、既知の正解合成画像付き）。v1 で実データに差し替え |
| B のエディタ → A のプロジェクト読み込み | B は `InMemoryProjectRepository`＋フィクスチャの `project.json` で単独起動できるルート `/#/dev/editor?fixture=v0` を持つ |
| C の `render` → VRM | `pipeline` 既存のサンプル VRM（ローカルのみ、コミットしない） |
| A の書き出し → C の `export` | モックは `fixtures/.../export.mp4` を返す |

## 5. ブランチと統合

- `main` は常に起動できる状態。直接 push しない。PR はレーン内でレビュー（作成者以外のサブエージェントか、手が空いた人）してマージ。
- ブランチ：`lane-a/<feature>`、`lane-b/<feature>`、`lane-c/<feature>`、契約は `contracts/<内容>`。
- **契約 PR** は全レーンの承認（チャンネルで OK をもらう）が要る。M0 以降の契約変更は追加のみ（フィールド追加・エンドポイント追加）にし、既存の意味を変えない。
- 統合の機会：毎 2 時間に 1 回、各レーンが main に rebase して起動確認（`pnpm tauri dev` とモックサーバー）。

## 6. Definition of Done（feature 共通）

- [ ] feature 文書の受け入れ条件をすべて満たす（手で確認した手順をPRに書く）。
- [ ] domain・application にユニットテストがある（ポートはモックで差し替え）。
- [ ] 層の依存規則を守っている（presentation から infrastructure を直接 import しない、domain は外部依存なし）。
- [ ] 文言は `i18n/ja.ts`、色・余白はデザイントークン。
- [ ] エラー・空・処理中の状態を [ux/screens](../ux/screens.md) どおりに出す。
- [ ] モックサーバー相手と（M2 以降は）実サーバー相手で動く。

## 7. レーン内の並列化（サブエージェント向けの切り方）

各 feature 文書の「タスク分解」は、**層ごとに独立して着手できる**ように書いてある。典型：

1. domain（エンティティ・純粋関数・テスト）— 依存なし
2. application（ユースケース・ポート・テスト）— 1 のインターフェースだけに依存
3. infrastructure（アダプタ）— ポートと契約に依存
4. presentation（画面）— ユースケースの入出力型に依存（中身はスタブで開始できる）

サブエージェントに渡すときは「feature 文書のパス＋担当するタスク番号＋触ってよいディレクトリ」を指定する。
