# packages/contracts

アプリ（レーン A・B）と GPU サーバー（レーン C）の契約。正本は [designdocs/architecture/contracts.md](../../designdocs/architecture/contracts.md)。

| ファイル | 中身 | 作り方 |
|---|---|---|
| `openapi.yaml` | `/v1` の全エンドポイント・型・エラー。`x-sse-events` に SSE の各イベントの data の型 | **生成物**。`services/gpu_server` で `python scripts/export_openapi.py`（手で編集しない） |
| `project.schema.json` | `*.vvlog/project.json`（contracts §6.1） | 手書き |
| `overrides.schema.json` | `*.vvlog/overrides.json`（contracts §6.2） | 手書き |
| `CHANGELOG.md` | 契約の変更履歴。M0（凍結）以降は追加だけ | 手書き |

## TypeScript の型

```sh
pnpm dlx openapi-typescript packages/contracts/openapi.yaml -o apps/desktop/src/infrastructure/compute/api.gen.ts
```

## 変更の手順

1. contracts.md を直す。
2. サーバーのスキーマ（`services/gpu_server/vvlog_server/interface/schemas.py`）を直し、`openapi.yaml` を生成し直す。
3. `CHANGELOG.md` に追記し、全レーンに告知（契約 PR は全レーンの承認が要る。designdocs/architecture/parallel-plan.md §5）。

`services/gpu_server/tests/test_contract.py` が、コミット済みの `openapi.yaml` とサーバーの出す OpenAPI の一致を確認する。
