# services/gpu_server

vvlog の GPU サーバー。アプリからのジョブを受けて `pipeline/` の推論をサブプロセスで回し、段階ごとの成果物と完成 MP4 を返す
（designdocs/features/gpu-server-runtime、ADR-0014）。

- **アプリから呼ぶ手順**：[packages/contracts/CLIENT_GUIDE.md](../../packages/contracts/CLIENT_GUIDE.md)（型は `openapi.yaml`）
- **GPU 機での起動・Tailscale**：[DEPLOY.md](DEPLOY.md)
- **GPU なしの開発**：[services/mock_server](../mock_server/README.md)

## 仕組み

| ジョブ | サーバーが実行するもの |
|---|---|
| analyze | ffmpeg（回転・CFR・フル/プロキシのフレーム・音声） → `pipeline/scripts/offline_app_lite.py`（検出・SAM3 追跡・HMR） → `pipeline/scripts/occlusion/run_occlusion_stage.py`（fusion v2・遮蔽物ユニット） |
| render | `pipeline/scripts/avatar_app.py --fusion_state … --targets … --vrm_map … --export_layers …` |
| export | `pipeline/scripts/avatar_app.py --hq <フル解像度> --overrides …` → ffmpeg（H.264 + AAC） |

ジョブは GPU 1 枚につき 1 本ずつ FIFO で実行。状態・イベント・成果物は `VVLOG_DATA_ROOT` 以下のファイル
（`jobs/<jobId>/job.json`・`events.jsonl`、`projects/<projectId>/{media,work,artifacts}/`）。

## 層（designdocs/architecture/overview §5）

```
vvlog_server/
  domain/        Job の状態機械、入力制約とプロキシ寸法、分割アップロード、成果物の一覧、手修正の展開（I/O なし）
  application/   ポート、ユースケース、ワーカー、stages/（ingest, track_mesh, occlusion, render, export）
  adapters/      ファイル保存、events.jsonl、FIFO、サブプロセス、ffmpeg、nvidia-smi、VRM の読み取り、フィクスチャ記録
  interface/     FastAPI（/v1）、pydantic スキーマ（openapi.yaml の元）、SSE、Range
  main.py        組み立て（composition root）
```

## 開発

```sh
uv venv .venv -p 3.11 && uv pip install -p .venv/bin/python -e '.[dev]'
.venv/bin/python -m pytest -q                 # パイプラインは FakePipelineRunner（tests/fake_pipeline.py）で代用
.venv/bin/python scripts/export_openapi.py    # schemas.py を変えたら packages/contracts/openapi.yaml を作り直す
```
