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

| `VVLOG_ALLOWED_ORIGINS` | `https://me.github.io` | ブラウザ（Pages 版など）からの接続を許すオリジン。カンマ区切り。**画面を開くページのオリジンだけ**を書く（パスは含めない） |
| `VVLOG_MAX_UPLOAD_BYTES` | `1073741824` | 1 回のアップロードの上限（既定 4 GiB。動画は 30 秒・長辺 1920px までなので 1 GiB でも十分） |
| `VVLOG_MAX_QUEUED_JOBS` | `8` | 待ち＋実行中のジョブの上限（既定 16、0 で無制限）。超えると `429 QUEUE_FULL` |
| `VVLOG_ARTIFACT_TTL_HOURS` | `72` | 終わったプロジェクトと放置されたアップロードを、最後の動きからこの時間で自動削除（既定 0 = 残す）。起動時と 1 時間ごと。実行中・待ちのジョブがあるプロジェクトは消さない |
| `VVLOG_ALLOW_WEAK_TOKEN` | `1` | 手元だけで試すとき、短い・推測されやすいトークンの拒否を解除する（公開するサーバーでは使わない） |

- トークンは 16 文字以上で、推測されにくい値にする（`openssl rand -hex 24`）。短い・`password`／`test-token` のような値だと起動を拒否する。
- Windows：`services\gpu_server\run_local.ps1`
- Linux：`services/gpu_server/run_linux.sh`

確認：`curl http://127.0.0.1:8765/v1/health` → `{"status":"ok",...}`、
`curl -H "Authorization: Bearer $VVLOG_API_TOKEN" http://127.0.0.1:8765/v1/capabilities` → GPU 名と VRAM。

## 4. デモ（Mac から Tailscale 越し）

1. Windows 機とデモ用 Mac を同じ tailnet に参加させる。
2. Windows 機で `tailscale serve --bg --https=443 http://127.0.0.1:8765`（tailnet の中だけ。**Funnel は使わない**）。
3. Mac で `curl https://<機名>.<tailnet>.ts.net/v1/health` が通ることを確認。
4. アプリの接続設定に `https://<機名>.<tailnet>.ts.net` とトークンを登録。

## 5. 自分の GPU サーバーを公開する（BYO、ADR-0015・#35）

GitHub Pages などの HTTPS のページから、**自分で用意した** GPU サーバーを使うときの手順。運営側のホストは無く、動画は自分のサーバーにだけ送られる。

Docker イメージ（`deploy/docker/`）とクラウド GPU（RunPod / Vast.ai / GCP L4）の手順は [designdocs/guides/byo-gpu-server.md](../../designdocs/guides/byo-gpu-server.md)。

**守ること**
- サーバーは常に `127.0.0.1` にだけ待ち受ける。**ポートを直接インターネットに開けない**。外から届くのは下のトンネル／リバースプロキシ（HTTPS）だけにする。
- トークンを必ず設定する（上の表）。HTTPS でない URL に Pages から接続することはできない（mixed content）。
- `VVLOG_ALLOWED_ORIGINS` に、画面を開くページのオリジンを書く。書いていないオリジンからは CORS で拒否される。

**どの方法でも共通の起動**
```sh
export VVLOG_API_TOKEN=$(openssl rand -hex 24)       # 控えておく（接続設定に貼る）
export VVLOG_ALLOWED_ORIGINS=https://me.github.io
export VVLOG_MAX_UPLOAD_BYTES=1073741824 VVLOG_MAX_QUEUED_JOBS=8 VVLOG_ARTIFACT_TTL_HOURS=72
./run_linux.sh        # Windows は run_local.ps1。127.0.0.1:8765 で待ち受ける
```

### 5-1. Tailscale（自分の端末だけで使う）
§4 のとおり `tailscale serve --bg --https=443 http://127.0.0.1:8765`。**Funnel は使わない**（誰でも届くようになる）。同じ tailnet の端末だけが接続でき、証明書は Tailscale が用意する。Pages のように tailnet の外のページから使うことはできない。

### 5-2. Cloudflare Tunnel（Pages など外のページから使う）
```sh
cloudflared tunnel --url http://127.0.0.1:8765          # 試すだけ: https://<ランダム>.trycloudflare.com が出る
# 常用するなら: cloudflared tunnel login → create → route dns → config.yml の ingress に http://127.0.0.1:8765
```
- 出た `https://…` とトークンを、アプリの接続設定に登録する。URL は再起動で変わる（quick tunnel）ので、常用には名前付きトンネルを使う。
- SSE（進捗）はそのまま通る。1 回のリクエスト本体の上限（無料プランで 100 MB）より小さい 8 MB ずつ送る作りなので、アップロードも通る。

### 5-3. クラウドの GPU VM ＋ リバースプロキシ
VM のファイアウォールは **443（と SSH）だけ**開ける。Caddy なら証明書も自動：
```
gpu.example.com {
    reverse_proxy 127.0.0.1:8765 {
        flush_interval -1          # SSE をためずに流す
    }
}
```
nginx の場合は次を入れる（既定のままだと進捗が止まり、大きなアップロードが切られる）：
```
client_max_body_size 16m;           # 断片は 8 MB
proxy_buffering off;                # SSE
proxy_request_buffering off;
proxy_read_timeout 3600s;           # 長いジョブの SSE
proxy_set_header Host $host;
proxy_pass http://127.0.0.1:8765;
```

### 確認
```sh
curl https://<URL>/v1/health                                              # {"status":"ok",...}
curl -H "Authorization: Bearer $VVLOG_API_TOKEN" https://<URL>/v1/capabilities
curl -i -X OPTIONS https://<URL>/v1/projects -H "Origin: https://me.github.io" \
     -H "Access-Control-Request-Method: POST" -H "Access-Control-Request-Headers: authorization,content-type"
# → 200 と access-control-allow-origin: https://me.github.io（書いていないオリジンだと 400）
```

## 6. 片付け・障害時

- サーバーを止めると実行中のジョブは次の起動時に `INTERRUPTED` で失敗になる（再実行はアプリから）。
- ジョブのログ：`GET /v1/jobs/{id}/logs` か `data/projects/<prj>/work/<job>/*.log`。
