# ADR-0003 クリーンアーキテクチャと契約先行で 3 レーン以上を並列化する

- 状態：採択
- 日付：2026-10-24

## 文脈
- 2 日間で、Claude Code 3 アカウント（＋各アカウント内のサブエージェント）を並列に走らせて作り切る。
- 並列化の失敗パターンは「互いの API や形式を推測して作り、結合で壊れる」「1 か所（巨大な App.tsx や main.py）に変更が集中して衝突する」。

## 決定
1. **契約先行**：Day 1 の最初の 2 時間で次を固定し、`packages/contracts/` に置く。以後の変更は契約 PR でのみ行う。
   - GPU サーバー API：`openapi.yaml`（TS 型は `openapi-typescript`、Python は pydantic モデルを手書きし、契約テストで照合）。
   - ジョブイベント（SSE）のスキーマ、成果物（レイヤー）のファイル形式と命名、プロジェクトファイル（`project.json`）の JSON Schema、合成規則。
   - 正本は [architecture/contracts.md](../architecture/contracts.md)。`openapi.yaml` はこの文書から起こす。
2. **クリーンアーキテクチャ**（依存は内向きのみ）をアプリとサーバーの両方で採る。
   - `domain`（エンティティ・値オブジェクト・純粋関数。合成規則 `effectiveHide()` もここ）
   - `application`（ユースケース＋ポート＝インターフェース）
   - `infrastructure` / `adapters`（HTTP、ファイル、Tauri、パイプライン呼び出し）
   - `presentation` / `interface`（React 画面、FastAPI ルーター）
3. **feature 単位の縦割り**：presentation と application は `features/<機能名>/` に分け、1 つの feature = 1 つの担当（レーン）にする。共有部（domain、ポート定義、UI 部品）の変更は小さな PR に分けてすぐ main に入れる。
4. **モックを先に**：モックサーバー（F14）と `InMemory` アダプタを Day 1 午前に用意し、UI レーンは GPU 無しで進める。

## 却下した案
| 案 | 却下理由 |
|---|---|
| 実装しながら API を決める | 3 レーンが同時に推測して作ると結合で壊れる。契約の固定に 2 時間かけても回収できる |
| 層を分けない（画面から直接 fetch） | 並列作業時にファイル衝突が増え、モックとの差し替えができない |
| gRPC / tRPC | Python サーバーと Tauri の双方で導入コストがある。HTTP＋OpenAPI で足りる |

## 影響
- ユースケースはポートだけに依存するので、`MockComputeBackend`・`InMemoryProjectRepository` でユニットテストできる。
- 契約の変更は全レーンに影響する。変更時は `packages/contracts/CHANGELOG.md` に追記し、チャンネルで告知する（[parallel-plan](../architecture/parallel-plan.md)）。
