# F05 job-progress-queue — ジョブ進捗・キュー・通知・段階的プレビュー

| 項目 | 内容 |
|---|---|
| レーン | A（SSE・キュー API は C） |
| 優先度 | P0 |
| 関連 | FR-07, FR-08 / [ADR-0004](../../adr/0004-job-stages-and-progressive-results.md) / [contracts §3](../../architecture/contracts.md#3-ジョブとイベント) / [ux/screens S2-2, S5](../../ux/screens.md) |
| 依存 | F03（ComputeBackend、SSE）、F01（プロジェクトへの記録） |

## ユーザーストーリー
- ユーザーとして、処理がどこまで進み、あとどれくらいかかるかを常に知りたい。
- 処理を放置して他のことをしても、終わったら知らせてほしい。複数の動画を続けて投入したい。
- 結果が届いた順に見たい（追跡マスク → メッシュ → アバター）。

## 受け入れ条件
- [ ] ジョブの投入後、SSE でステージと進捗を受け、ウィザード（S2-2 / S2-4）とステータスバーとジョブパネルに同じ状態を表示する。
- [ ] `artifact_ready` を受けたら、その成果物を `cache/<jobId>/` にダウンロード（zip は展開）し、プレビュー領域に反映：`proxy_frames` → フレームのスライドショー、`track_masks` → 色付きマスクの重ね表示、`mesh_preview` → 動画再生、`layers` → エディタが使用可能に。
- [ ] 残り時間：`etaSec` があれば表示、無ければ「計算中」。
- [ ] ジョブパネル：サーバーのキュー（`GET /v1/queue`、5 秒ごと）と自分のジョブ。取り消し（確認あり）、失敗時の再試行（同じパラメータで再投入）、ログ表示（`logs?tail=200`、コピー）。
- [ ] 完了・失敗の通知（前面はトースト、背面は OS 通知）。
- [ ] アプリの再起動・SSE 切断後に状態を復元（`project.json` の jobId → `GET /v1/jobs/{id}` → 未完了なら SSE 再購読、取りこぼした成果物は `listArtifacts` で補完）。
- [ ] `render` は `analyze` の完了前でも投入でき、「解析の完了を待っています」と表示される（`dependsOn`）。

## 設計
- domain/job.ts：`Job`、`StageState`、`applyEvent(job, event): Job`（純粋関数。イベントから状態を作る。`snapshot` は全置換）、`overallProgress(job)`（ステージの重み：ingest 0.05 / track 0.35 / mesh 0.35 / occlusion 0.25 など。重みは `domain/jobWeights.ts`）。
- application/usecases：
  - `SubmitJob`（投入し `project.jobs` に記録）
  - `SubscribeJob`（SSE を購読し、`applyEvent` でストア更新、`artifact_ready` で `FetchArtifact` を呼ぶ、終了で通知）
  - `FetchArtifact`（`LayerCachePort` 経由でダウンロード・展開。済みならスキップ。sha256 照合）
  - `CancelJob`、`RetryJob`、`GetQueue`、`GetLogs`、`RestoreJobs`（プロジェクトを開いたとき）
- ports：`LayerCachePort { has(jobId, name): Promise<boolean>; fetch(jobId, artifact, onProgress): Promise<string /*dir*/>; path(jobId, name): string }`、`NotifierPort { toast(...); osNotify(...) }`。
- infrastructure：`TauriLayerCache`（`download_to` / `unzip_to`、プロジェクトの `cache/`）、`TauriNotifier`。
- presentation/features/jobs：`JobProgressList`（ステージ行）、`JobDrawer`、`ProgressivePreview`（成果物ごとの表示部品）、ステータスバーのジョブ表示。
- ストア：`useJobsStore`（jobId → Job、購読中のハンドル）。

## タスク分解
1. domain：`applyEvent`・`overallProgress` ＋テスト（イベント列の再生、snapshot、順不同の再送）。
2. `SubscribeJob`・`FetchArtifact`・`RestoreJobs` ＋テスト（InMemoryComputeBackend でイベント列を流す）。
3. `TauriLayerCache`、`TauriNotifier`。
4. 進捗表示部品（ウィザード用、ステータスバー用、ドロワー用）。
5. `ProgressivePreview`（フレーム、マスク重ね、動画）。
6. キュー表示、取り消し・再試行・ログ。

## テスト
- モックサーバーの 10 倍速再生で、全成果物が届き、プレビューが順に切り替わる。
- SSE を途中で切断 → 再接続後に重複なし・欠落なし。

## P1 / スコープ外
- ジョブの優先度変更（P2）、他ユーザーのジョブ名の秘匿（スコープ外）。
