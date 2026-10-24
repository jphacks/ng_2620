# ADR（Architecture Decision Records）

決定・理由・却下した案・影響を 1 件 1 ファイルで残す。状態は `採択` / `提案` / `置換済み`。
変更するときは既存 ADR を書き換えず、新しい ADR を足して旧 ADR の状態を `置換済み（ADR-xxxx）` にする。

| ADR | 決定 | 状態 |
|---|---|---|
| [0001](0001-desktop-framework-tauri.md) | デスクトップ基盤は Tauri 2 + React + TypeScript | 採択 |
| [0002](0002-gpu-server-and-compute-backend-port.md) | 推論は GPU サーバーに分離し、アプリからは `ComputeBackend` ポートで抽象化（ローカル Windows / リモート） | 採択 |
| [0003](0003-clean-architecture-and-contract-first.md) | クリーンアーキテクチャと契約先行（OpenAPI＋成果物形式）で 3 レーン以上を並列化 | 採択 |
| [0004](0004-job-stages-and-progressive-results.md) | ジョブは `analyze` / `render` / `export` の 3 種、ステージ単位で成果物を公開し SSE で通知 | 採択 |
| [0005](0005-preview-composite-client-export-server.md) | プレビュー合成はアプリ内 WebGL、書き出し合成はサーバー。合成規則は契約として共有 | 採択 |
| [0006](0006-occlusion-overrides-model.md) | 前後関係の手修正は「遮蔽物ユニット × 区間 × 前/後ろ」のオーバーライドで表し、アプリが正本を持つ | 採択 |
| [0007](0007-project-file-format.md) | プロジェクトはローカルのフォルダ形式 `*.vvlog/`（JSON＋キャッシュ）、スキーマ版付き | 採択 |
| [0008](0008-implementation-repo-and-model-placement.md) | 実装はハッカソン指定リポジトリの monorepo。パイプライン・モデルのコードはそこへ置き、重みは無料の外部（Hugging Face 等）から取得する | 採択 |
| [0009](0009-demo-resilience.md) | デモは処理済みサンプルプロジェクトを同梱し、サーバー無しでも編集体験を見せる | 採択 |
| [0010](0010-out-of-scope-enterprise-and-later.md) | エンタープライズ機能・多形式書き出し等は今回の 2 日間ではスコープ外（拡張点のみ） | 採択 |
| [0011](0011-input-limits-and-resolutions.md) | 入力は 180 秒・長辺 1920px まで。推論はプロキシ解像度、書き出しはフル解像度 | 採択（2026-10-25 改訂：30 秒 → 180 秒） |
| [0012](0012-transport-and-auth.md) | 通信は HTTP(S)＋SSE、認証は Bearer トークン。リモートはトンネル経由の HTTPS | 採択 |
| [0013](0013-frontend-stack.md) | フロントエンドのライブラリ構成（状態管理・パネル・描画・型生成） | 採択 |
| [0014](0014-server-stack.md) | サーバーは FastAPI＋GPU 1 枚につき 1 ワーカー、ステージはサブプロセスで実行 | 採択 |
