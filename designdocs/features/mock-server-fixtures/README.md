# F14 mock-server-fixtures — モックサーバーと固定データ（並列開発の土台）

| 項目 | 内容 |
|---|---|
| レーン | C（**最初の 2〜3 時間**）。合成フィクスチャ v0 は B |
| 優先度 | P0（他レーンの前提） |
| 関連 | [ADR-0003](../../adr/0003-clean-architecture-and-contract-first.md) / [parallel-plan §4](../../architecture/parallel-plan.md#4-依存関係とモックによる切り離し) / [contracts](../../architecture/contracts.md) |
| 依存 | 契約（M0） |

## ユーザーストーリー
- アプリの開発者として、GPU が無くても、本物と同じ API・同じ成果物・同じイベントの流れで開発したい。

## 受け入れ条件
- [ ] `services/mock_server`：`openapi.yaml` の全エンドポイントに、スキーマに合う応答を返す（契約テストで確認）。認証はトークン `mock` を受け付ける。
- [ ] ジョブ投入で、フィクスチャに記録したイベント列（`events.jsonl`、実時間の時刻つき）を再生する。`VVLOG_MOCK_SPEED`（既定 10）倍速。成果物はフィクスチャのファイルを返す。
- [ ] アップロードは受け取って捨てる（sha256 は照合）。どの動画でも同じフィクスチャの結果を返す。
- [ ] 失敗の再現：`VVLOG_MOCK_FAIL=track:NO_PERSON_FOUND` のように、指定ステージで指定エラーを起こせる。SSE を N 秒後に切る `VVLOG_MOCK_DROP_SSE=5`。
- [ ] 起動：`pnpm mock`（ルートのスクリプト）または `python -m mock_server --port 8766`。
- [ ] **フィクスチャ v0（人工）**：B が D1 +2h までに作る。`fixtures/synth/`：96 フレーム、540x960、動く円のアバター 2 人、四角の遮蔽物ユニット 2 つ（片方は自動で「前」、片方は「後ろ」）、`units.json`、`layers/`、正解合成画像（手修正なし・ユニット #2 を「前」にした場合）。
- [ ] **フィクスチャ v1（実データ）**：C が D1 終了までに作る。実サーバーの analyze・render・export の全成果物＋`events.jsonl`。チームで撮影した 10 秒の素材（F12 のサンプルと共用）。VRM ファイルは含めない。

## 設計
- モックサーバーは FastAPI で、**本サーバーの `interface/schemas.py` を import して使う**（型の二重定義を避ける）。ジョブの実行だけ `ReplayRunner` に差し替える（本サーバーのユースケースに `PipelineRunnerPort` のモック実装を注入する形でもよい：その場合モックサーバー＝本サーバー＋`ReplayRunner`。こちらを推奨）。
- フィクスチャの形式：
  ```
  fixtures/<name>/
    manifest.json        { name, source: { durationSec, width, height, fps }, license, jobs: { analyze, render, export } }
    jobs/<type>/events.jsonl   記録されたイベント（時刻つき）
    jobs/<type>/artifacts/...  成果物（contracts §4 と同じ名前・形式）
  ```
- 記録：本サーバーに `VVLOG_RECORD_FIXTURE=<dir>` を付けて実行すると、イベントと成果物をこの形式で書き出す（F13 の小さな追加）。

## タスク分解
1. （C）`ReplayRunner` とモック起動（本サーバーのユースケースを再利用）。最初はフィクスチャ無しで「固定の短いイベント列＋空の成果物」でもよい（D1 +2h）。
2. （B）フィクスチャ v0 の生成スクリプト（`fixtures/synth/make_synth.py`）と正解画像。
3. （C）失敗・切断の再現オプション。
4. （C）記録モード `VVLOG_RECORD_FIXTURE` とフィクスチャ v1。
5. 契約テスト（モックが `openapi.yaml` に合う）。

## P1 / スコープ外
- 複数フィクスチャの切替（動画の sha256 で選ぶ）（P1）。
