# 自分の GPU サーバーを用意する（BYO 導入ガイド）

GitHub Pages 版（ブラウザ）で動画を解析するには、**自分で用意した GPU サーバー**に接続します（[ADR-0015](../adr/0015-web-first-windows-local-server.md)・#35）。
運営側のホストはありません。動画は**あなたのサーバーにだけ**送られ、費用もあなたの負担です。

> **この文書の状態**：Docker イメージ（`deploy/docker/`）と各クラウドの手順は、GPU のある環境でまだ**実際には通していません**（静的な確認のみ）。
> 詳細は末尾の「確認状況」。detectron2 の入れ方は #30 の結論と #51（ビルド済み wheel）待ちで、Docker の既定は暫定のソースビルドです。
> 手順で詰まったら、サーバーの細かい環境変数と公開方法の正は [`services/gpu_server/DEPLOY.md`](../../services/gpu_server/DEPLOY.md)（§5 が BYO の公開手順）です。ここでは重複させず参照します。

## 1. 全体像

```
[ブラウザ: Pages の画面] ──HTTPS──▶ [入口: トンネル / リバースプロキシ] ──▶ 127.0.0.1:8765 [GPU サーバー(トークン必須)]
```

必要なもの：

| 要るもの | 備考 |
|---|---|
| NVIDIA GPU（24GB 級を推奨） | 必要 VRAM と所要時間は #30 の実測待ち。サーバーの動作確認の実績があるのは RTX 5060 Ti（Windows）と L4（Linux VM）[DEPLOY.md] |
| Hugging Face のアカウントとトークン | SAM3・SAM-3D-Body は**ゲート付き**。先に各モデルのページでアクセス許諾を済ませ、そのアカウントで読み取りトークンを作る（[weights/README.md](../../weights/README.md)） |
| ディスク 約 30GB 以上 | 重み約 7GB ＋ イメージ ＋ ジョブの作業領域 |
| HTTPS の入口 | Pages（https）から http の URL には繋げません（混在コンテンツ）。必ず HTTPS にする |

## 2. 選び方

費用は**目安であり、変動します**（為替・時間帯・在庫・地域・ストレージ・転送量で変わる）。この表の数字は事前知識に基づく**概算で、価格ページでは確認していません**（確認日：なし）。使う前に必ず各社の料金ページを見てください。

| 方法 | 手間 | HTTPS | 費用の目安（概算・未確認） | 向いている人 |
|---|---|---|---|---|
| 自宅の Windows + RTX 機（§6） | 中 | Tailscale / Cloudflare Tunnel | 電気代のみ | すでに GPU 機がある |
| RunPod（§4-1） | 小 | プロキシの HTTPS URL が出る | 時間課金。24GB 級で 1 時間あたり 1 ドル未満〜数ドルのことが多い | とにかく早く試したい |
| Vast.ai（§4-2） | 中 | 直接ポートは http。トンネルを併用 | 時間課金。RunPod と同程度かやや安い傾向。貸し手により品質差 | 安さ優先 |
| GCP L4 VM（§4-3） | 大 | Caddy または Cloudflare Tunnel | 時間課金（g2 系）。1 時間あたり 1 ドル前後のオーダー。ディスク・外向き転送は別 | 既に GCP を使っている |

- **止め忘れに注意**：停止中も課金されるもの（ストレージ・予約 IP）があります。使わないときは「停止」ではなく「削除」が確実です。重みはボリュームに残せば次回の再取得（約 7GB）を省けます。
- 1 本あたりの解析時間は #30 の計測待ちで、ここでは書けません。まず短い動画で試してから、費用の見積もりを立ててください。

## 3. Docker イメージ（共通）

`deploy/docker/` に CUDA 対応のイメージがあります（環境 A=推論パイプライン、環境 B=サーバー本体、起動スクリプト）。重みはイメージに**入れず**、初回起動時に `/data/weights`（ボリューム）へ取得します。

### 3-1. ビルド（リポジトリのルートで）

```sh
# 暫定: detectron2 をソースからビルド（時間がかかる。#30 / #51 の結論待ち）
docker build -f deploy/docker/Dockerfile -t vvlog-gpu-server .

# #51 のビルド済み wheel ができたら（URL は #51 の成果物）
docker build -f deploy/docker/Dockerfile -t vvlog-gpu-server \
  --build-arg DETECTRON2_MODE=wheel --build-arg DETECTRON2_WHEEL_URL=<wheel の URL> .
```

RunPod など「レジストリのイメージを指定する」サービスでは、ビルドしたイメージを**自分のレジストリ**に置きます（運営はイメージを配布しません）。

```sh
docker tag vvlog-gpu-server ghcr.io/<あなた>/vvlog-gpu-server:latest
docker push ghcr.io/<あなた>/vvlog-gpu-server:latest     # 事前に docker login ghcr.io。公開設定にするかは自分で決める
```

> 重みは入っていないので公開しても重みは漏れませんが、イメージ内のコードの公開範囲には注意してください。

### 3-2. 環境変数

| 変数 | 意味 |
|---|---|
| `HF_TOKEN` | **初回の重み取得だけ**に使う。ディスクには書かず、取得後はサーバーとパイプラインの環境から消す。取得後は外してよい |
| `VVLOG_API_TOKEN` | 接続トークン。**空なら起動時に `openssl rand -hex 24` で生成してログに 1 回だけ出す**。弱い値（短い・推測されやすい）は拒否して生成し直す（判定はサーバーの `weak_token_reason`） |
| `VVLOG_ALLOWED_ORIGINS` | 画面を開くページのオリジン（§5）。書いていないオリジンは CORS で拒否される |
| `VVLOG_MAX_UPLOAD_BYTES` / `VVLOG_MAX_QUEUED_JOBS` / `VVLOG_ARTIFACT_TTL_HOURS` | 自分のサーバーを守る上限と自動削除（compose の既定は 1GiB / 8 / 72 時間）。他の変数は DEPLOY.md §3 |

コンテナの中でサーバーは `127.0.0.1:8765` だけで待ち受け、外へは `socat` で `:8080` に中継します。`deploy/docker/docker-compose.yml` は**ホストの `127.0.0.1` にしか公開しません**。

### 3-3. 手元の GPU 機で動かす（docker compose）

NVIDIA ドライバと [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/) が要ります。

```sh
export HF_TOKEN=hf_...                                  # 初回だけ
export VVLOG_ALLOWED_ORIGINS=https://<Pages のオリジン>   # §5
docker compose -f deploy/docker/docker-compose.yml up --build -d
docker compose -f deploy/docker/docker-compose.yml logs vvlog | grep -A3 "接続トークン"   # トークン(自動生成時)
curl http://127.0.0.1:8765/v1/health                    # {"status":"ok",...}
```

トークンは**起動のたびに変わります**（自動生成の場合）。再起動後も同じ値で使うなら `VVLOG_API_TOKEN` を自分で決めて渡します。ログに出したトークンは、確認したらログの置き場（クラウドのコンソールなど）に他人が入れないことを確かめてください。

## 4. クラウド GPU の手順

どれも「起動 → URL とトークンを手に入れる → §5 で接続設定に貼る」です。コマンドと画面名は執筆時点の想定で、**実機では未確認**です。サービス側の画面が変わっていたら公式ドキュメントを優先してください。

### 4-1. RunPod

1. §3-1 のイメージを自分のレジストリに push（公開しない場合はレジストリの認証情報を RunPod 側に登録）。
2. Pods → Deploy → GPU（24GB 級）を選ぶ → テンプレートで **Container Image** に `ghcr.io/<あなた>/vvlog-gpu-server:latest`。
3. **Expose HTTP Ports** に `8080`。**Volume Mount Path** は `/data`（永続ボリュームを付けると重みを再取得せずに済む）。
4. 環境変数：`HF_TOKEN`、`VVLOG_ALLOWED_ORIGINS`、（任意）`VVLOG_API_TOKEN`、`VVLOG_ARTIFACT_TTL_HOURS` など。
5. 起動後、Pod のログ（Logs）で「接続トークン」の行を探して控える。重みの取得（約 7GB）が終わると応答する。
6. URL は `https://<pod-id>-8080.proxy.runpod.net` の形になる想定（Pod の Connect 画面に出る）。`curl https://<URL>/v1/health` で確認。
7. 使い終わったら Pod を **Terminate**。

注意（未確認）：プロキシ越しの SSE（進捗）と、長いリクエストの時間制限。進捗が止まる場合は §8。

### 4-2. Vast.ai

1. インスタンスを借りる際に、イメージに §3-1 のイメージを指定（Docker の起動オプションで `-p 8080:8080` と環境変数を渡す。ディスク容量は 40GB 以上）。
2. Vast の「直接ポート」は **http** のことが多く、そのまま Pages からは繋げません。インスタンス内でトンネルを併用します：
   ```sh
   cloudflared tunnel --url http://127.0.0.1:8080     # https://<ランダム>.trycloudflare.com が出る（DEPLOY.md §5-2）
   ```
   （`cloudflared` はイメージに入っていません。インスタンス内にインストールするか、`vvlog` と別の入口を用意してください）
3. ログからトークンを控え、`https://…trycloudflare.com` を URL として使う。再起動で URL が変わる。
4. 使い終わったらインスタンスを **Destroy**。

### 4-3. GCP の L4（Compute Engine, g2 系）

1. VM を作る（想定。GPU クォータの申請が要る場合がある）：
   ```sh
   gcloud compute instances create vvlog-gpu --zone=us-central1-a --machine-type=g2-standard-4 \
     --maintenance-policy=TERMINATE --boot-disk-size=200GB \
     --image-family=ubuntu-2204-lts --image-project=ubuntu-os-cloud
   ```
2. SSH で入り、NVIDIA ドライバ・Docker・NVIDIA Container Toolkit を入れる（GCP の GPU ドライバ手順に従う）。
3. リポジトリを clone して §3-3 を実行（`docker compose … up --build -d`。detectron2 のソースビルドに時間がかかる）。
4. HTTPS の入口を付ける。次のどちらか：
   - ドメインがある → Caddy（DEPLOY.md §5-3）。ファイアウォールは **443 と SSH だけ**開ける（8765 / 8080 は開けない）。
   - ドメインが無い → `cloudflared tunnel --url http://127.0.0.1:8765`（DEPLOY.md §5-2）。
5. 使い終わったら VM を**削除**（停止だけだとディスク代が続く）。

## 5. 接続設定に貼る（共通）

1. **URL**：`https://…`（末尾に `/v1` は付けない）。`curl https://<URL>/v1/health` が `{"status":"ok",...}` を返すこと。
2. **トークン**：起動ログ、または自分で決めた `VVLOG_API_TOKEN`。
3. **`VVLOG_ALLOWED_ORIGINS`** には、**画面を開くページのオリジンだけ**を書く。例：Pages の画面が `https://jphacks.github.io/ng_2620/` なら `https://jphacks.github.io`（パス `/ng_2620/` は含めない）。複数ならカンマ区切り。スキーム（`https://`）まで一致が必要。
4. アプリの「接続設定」を開き、プロファイルに URL とトークンを登録して接続を確認する。失敗理由は画面の診断に出る。
5. 動画はこのサーバーにだけ送られる。他人と共有しない。

## 6. 自分の Windows 機を「外部公開用 GPU サーバー」にする

RTX 付きの Windows 機を自分の GPU サーバーにして、別の端末や Pages の画面から使えます。手順の正は DEPLOY.md の §3（起動）と §5（公開）です。

1. パイプライン環境を作り（DEPLOY.md §1）、重みを取得（`HF_TOKEN=… python weights/fetch.py --dest <ckpt> --write_config`）。
2. 起動（PowerShell）：
   ```powershell
   $env:VVLOG_API_TOKEN = (-join ((1..24) | % { '{0:x2}' -f (Get-Random -Max 256) }))   # 48 桁の 16 進。控える
   $env:VVLOG_ALLOWED_ORIGINS = "https://<Pages のオリジン>"
   $env:VVLOG_MAX_UPLOAD_BYTES = 1073741824; $env:VVLOG_MAX_QUEUED_JOBS = 8; $env:VVLOG_ARTIFACT_TTL_HOURS = 72
   $env:VVLOG_PYTHON = "C:\...\pipeline\.venv\Scripts\python.exe"
   .\services\gpu_server\run_local.ps1       # 127.0.0.1:8765 だけで待ち受ける
   ```
   （`Get-Random` は暗号用の乱数ではありません。気になる場合は WSL / Git Bash の `openssl rand -hex 24` を使ってください。）
3. 公開：
   - 自分の端末だけから使う → **Tailscale**（`tailscale serve --bg --https=443 http://127.0.0.1:8765`。**Funnel は使わない**）。tailnet の外の Pages 画面からは使えません。
   - Pages など外のページから使う → **Cloudflare Tunnel**（`cloudflared tunnel --url http://127.0.0.1:8765`、常用は名前付きトンネル）。DEPLOY.md §5-1 / §5-2。

**安全設定**
- 待ち受けは `127.0.0.1` のみ。Windows ファイアウォールで 8765 を開けない。外から届くのはトンネル経由だけ。
- トークンは必須で 16 文字以上の推測されにくい値。`VVLOG_ALLOW_WEAK_TOKEN=1` は公開時に使わない。
- `VVLOG_ALLOWED_ORIGINS` は自分のページのオリジンだけ。
- 上限と自動削除（`VVLOG_MAX_UPLOAD_BYTES` / `VVLOG_MAX_QUEUED_JOBS` / `VVLOG_ARTIFACT_TTL_HOURS`）を必ず設定する。
- 使わないときはトンネルとサーバーを止める。quick tunnel の URL は漏れたら作り直す（再起動で別 URL になる）。
- アプリ本体に内蔵のローカルサーバー（`VVLOG_LOCAL=1`）は手元の画面専用です。トンネルで外に出す用途は想定せず、公開には上の `run_local.ps1`（トークン方式）を使ってください。

## 7. 安全のまとめ（全方法共通）

- **ポートを直接インターネットに開けない。** 開けるのは HTTPS の入口（443）だけ。クラウドのファイアウォール／セキュリティグループで `8765` / `8080` を許可しない。
- トークンは必須。人に渡さない。漏れたら再起動して作り直す。
- `docker-compose.yml` のポートは `127.0.0.1` に限る。`0.0.0.0` にしない。
- `HF_TOKEN` は初回の取得だけ。取得後は環境変数から外す。
- 保持期間は `VVLOG_ARTIFACT_TTL_HOURS` で短くする。借りたマシンを返す前に、ボリュームごと消す。

## 8. トラブルシュート

| 症状 | 原因と対処 |
|---|---|
| ブラウザで「CORS」「Failed to fetch」 | `VVLOG_ALLOWED_ORIGINS` にページのオリジン（スキーム込み・パス無し）が無い。末尾の `/` を付けない。DEPLOY.md の `curl -X OPTIONS …` で `access-control-allow-origin` が返るか確認 |
| 「混在コンテンツ」でブロックされる | URL が `http://`。Pages は `https://` にしか繋げない。トンネル／Caddy で HTTPS にする |
| 証明書エラー | Caddy はドメインの DNS と 443 の到達が要る。IP 直指定の自己署名は不可。Tailscale は `tailscale serve` の URL（`*.ts.net`）を使う |
| 401（トークンが違う） | 自動生成のトークンは**再起動で変わる**。ログの最新の値を貼る。前後の空白に注意 |
| 進捗（SSE）が止まる | nginx の `proxy_buffering off` / `proxy_read_timeout` 不足、またはプロキシの時間制限。Caddy は `flush_interval -1`。DEPLOY.md §5-3 |
| アップロードが途中で切れる | プロキシのリクエスト上限。サーバーは 8 MB ずつ送る（nginx は `client_max_body_size 16m`） |
| 起動直後に応答がない | 初回の重み取得中（約 7GB）。`docker compose logs -f vvlog` で確認 |
| 起動が「重みがありません」で止まる | `HF_TOKEN` 未指定。SAM3 / SAM-3D-Body のアクセス許諾が済んだアカウントのトークンを渡す |
| 起動が「トークンが弱い」で止まる | イメージでは自動で生成し直す。イメージ外（run_linux.sh など）なら `openssl rand -hex 24` |
| `docker: could not select device driver "nvidia"` | NVIDIA Container Toolkit が未導入。入れて Docker を再起動 |
| detectron2 のビルドが長い／失敗 | 暫定のソースビルド。`TORCH_CUDA_ARCH_LIST` を自分の GPU に絞ると短くなる。#51 の wheel 方式に移る予定 |

## 9. 確認状況

- 確認したこと：`deploy/docker/Dockerfile` の `docker build --check`（構文・規約。警告なし）、`entrypoint.sh` の `bash -n`、`docker-compose.yml` の構文（`docker compose config`）、トークン決定ロジックの単体テスト（`deploy/docker/tests/test_entrypoint.sh`）。
- **未確認**：イメージの実ビルド、コンテナの起動、GPU での解析、重みの初回取得、RunPod / Vast.ai / GCP の各手順（画面名・コマンド・URL 形式）、プロキシ越しの SSE、費用の数字。
- 暫定：detectron2 は #30 / #51 待ち（Docker 既定はソースビルド）。torch は 2.7.1 + CUDA 12.8 で仮置き。
