# 契約の変更履歴

## 1.0.0（M0）

- `/v1` の全エンドポイント（contracts §2.1）。予約（`units:prompt`、`/me`）は 501。
- ジョブ `analyze` / `render` / `export` とステージ、SSE イベント（`x-sse-events`）。
- 成果物の名前と形式（contracts §4）。
- `project.schema.json`（schemaVersion 1）、`overrides.schema.json`（schemaVersion 1）。
- 補足（contracts.md に追記済みの内容の具体化）：
  - `GET /v1/capabilities` は `{gpu: {name, vramMB} | null, limits: {maxDurationSec, maxLongSide, maxFps, maxTargets, formats}, queueLength, backend}`。`backend` は `pipeline`（実サーバー）か `replay`（モック）。
  - `GET /v1/queue` の `position` は実行中のジョブが `-1`、待ちは 0 始まり。
  - `GET /v1/projects/{id}` の `media[]` は `{mediaId, filename, size, sha256, path, meta, createdAt}`。
  - SSE の `snapshot` には `id` が付かない（`Last-Event-ID` は実イベントだけを指す）。
