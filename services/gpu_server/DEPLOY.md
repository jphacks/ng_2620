# GPU サーバーの配備

designdocs/features/gpu-server-runtime・ADR-0002 / 0012 / 0014。サーバー本体（FastAPI、torch を import しない）と、
推論を行うパイプラインの Python（`VVLOG_PYTHON`）は別の環境にする。

## 1. パイプラインの環境（GPU 機に 1 回）

| 機械 | 手順 |
|---|---|
| Windows（RTX 5060 Ti） | `pipeline/` に既存の `.venv` 構成（`pip install -e pipeline/models/sam3` など、pipeline/README.md）。Triton は `triton-windows` |
| Linux GPU VM（L4 など） | Python 3.11〜3.13 の venv に `pipeline/pyproject.toml` の依存。確認済みのピン：`ai-edge-litert`（表情）、`transformers~=4.57.3`、`diffusers==0.29.1`、`roma~=1.5.4`、`huggingface-hub<1.0`、`eva-decord`、detectron2 `a1ce2f9`、`moderngl`。MoGe は `git+https://github.com/microsoft/MoGe.git` の後に `utils3d_moge` を外して upstream `utils3d` |

## 2. 重み

```sh
HF_TOKEN=hf_... <pipeline の python> weights/fetch.py --dest <ckpt> --write_config
```

`--write_config` が `pipeline/configs/body4d.yaml` の `ckpt_root` を書き換える。

## 3. 起動

| 変数 | 例 | 意味 |
|---|---|---|
| `VVLOG_API_TOKEN` | `openssl rand -hex 24` | アプリの接続設定に入れるトークン（必須） |
| `VVLOG_PYTHON` | `C:\...\pipeline\.venv\Scripts\python.exe` | パイプラインを動かす Python |
| `VVLOG_DATA_ROOT` | `services/gpu_server/data` | ジョブ・成果物の保存先 |
| `VVLOG_GPU_IDS` | `0` | 使う GPU |
| `VVLOG_MAX_TARGETS` / `VVLOG_HMR_STRIDE` / `VVLOG_TRACKER_SIZE` / `VVLOG_FUSION_DEPTH` | `4` / `3` / `672` / `da2_indoor_s` | パイプラインの既定値 |

- Windows：`services\gpu_server\run_local.ps1`
- Linux：`services/gpu_server/run_linux.sh`

確認：`curl http://127.0.0.1:8765/v1/health` → `{"status":"ok",...}`、
`curl -H "Authorization: Bearer $VVLOG_API_TOKEN" http://127.0.0.1:8765/v1/capabilities` → GPU 名と VRAM。

## 4. デモ（Mac から Tailscale 越し）

1. Windows 機とデモ用 Mac を同じ tailnet に参加させる。
2. Windows 機で `tailscale serve --bg --https=443 http://127.0.0.1:8765`（tailnet の中だけ。**Funnel は使わない**）。
3. Mac で `curl https://<機名>.<tailnet>.ts.net/v1/health` が通ることを確認。
4. アプリの接続設定に `https://<機名>.<tailnet>.ts.net` とトークンを登録。

## 5. 片付け・障害時

- サーバーを止めると実行中のジョブは次の起動時に `INTERRUPTED` で失敗になる（再実行はアプリから）。
- ジョブのログ：`GET /v1/jobs/{id}/logs` か `data/projects/<prj>/work/<job>/*.log`。
