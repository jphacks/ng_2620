# services/gpu_server — vvlog GPU サーバー

アプリからジョブ（解析・描画・書き出し）を受けて、`pipeline/` の推論を**別プロセス**で回し、段階ごとの成果物と完成 MP4 を返す。

```
アプリ ──HTTP/SSE──▶ vvlog_server（FastAPI、torch なし） ──サブプロセス──▶ pipeline/ の Python（torch・CUDA）
                      この README の手順で作る環境 A                    「パイプラインの環境 B」（GPU 機だけ）
```

**環境は 2 つ**ある。サーバー本体（環境 A）は軽く、どの OS でも動く。推論（環境 B）は NVIDIA GPU の機械にだけ作り、サーバーには
`VVLOG_PYTHON` でその Python のパスを教える。GPU が無い機械（Mac など）では環境 B を作らず、**モックサーバー**を使う。

| やりたいこと | 必要なもの | 手順 |
|---|---|---|
| アプリの開発・画面の確認（GPU なし） | 環境 A ＋ ffmpeg | [1](#1-環境-a-を作るどの機械でも) → [モックサーバー](../mock_server/README.md) |
| 本物の推論を回す（GPU 機） | 環境 A ＋ 環境 B ＋ 重み ＋ ffmpeg | [1](#1-環境-a-を作るどの機械でも) → [2](#2-環境-b-を作るgpu-機だけ) → [3](#3-重みgpu-機だけ) → [4](#4-起動) |
| Mac のアプリから GPU 機につなぐ（デモ） | 上に加えて Tailscale | [DEPLOY.md §4](DEPLOY.md#4-デモmac-から-tailscale-越し) |

アプリから呼ぶ API の使い方は [packages/contracts/CLIENT_GUIDE.md](../../packages/contracts/CLIENT_GUIDE.md)。

## 0. 前提

| もの | 版 | 確認 | 入れ方 |
|---|---|---|---|
| Python | 3.10 以上（3.11 で確認） | `python3 --version` | macOS: `brew install python@3.11` / Windows: python.org |
| uv（推奨） | 0.5 以上 | `uv --version` | `curl -LsSf https://astral.sh/uv/install.sh \| sh`（無ければ venv + pip でもよい） |
| ffmpeg / ffprobe | 6 以上 | `ffmpeg -version` | macOS: `brew install ffmpeg` / Windows: `winget install Gyan.FFmpeg` / Ubuntu: `apt install ffmpeg`。**PATH に必要**（アップロードの検証と ingest で使う） |
| NVIDIA ドライバ・CUDA | GPU 機だけ | `nvidia-smi` | 環境 B の torch に合うもの |

## 1. 環境 A を作る（どの機械でも）

```sh
cd services/gpu_server
uv venv .venv -p 3.11
uv pip install -p .venv/bin/python -e '.[dev]'          # Windows: -p .venv\Scripts\python.exe
.venv/bin/python -m pytest -q                            # 43 件通れば OK（GPU 不要。推論は偽物で代用）
```

uv が無い場合：`python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`。
`-e`（editable）で入れるので、コードを変えても入れ直しは要らない。**ビルド手順は無い**（純 Python）。

## 2. 環境 B を作る（GPU 機だけ）

`pipeline/` を動かす Python。サーバーとは別の venv にする（torch・CUDA が重く、版の縛りが強いため）。

### Linux（新しい L4 の VM でこの手順どおりに確認済み：Python 3.13・CUDA 13.0・torch 2.14 cu130）

uv で作る（素の `python3 -m venv` は Ubuntu / Debian では pip が入らず失敗する。使うなら先に `apt install python3-venv`）。
**torch は `nvcc --version` と同じ CUDA 版のものを入れる**：detectron2 はインストール時に CUDA 拡張をビルドし、
版が違うと `The detected CUDA version (13.0) mismatches the version that was used to compile PyTorch (12.4)` で止まる
（nvcc が無い機械は CUDA Toolkit を入れる）。

```sh
cd pipeline
uv venv .venv -p 3.13                     # 3.13 が無ければ uv が取ってくる
P="uv pip install -p .venv/bin/python"
CU=$(nvcc --version | sed -n 's/.*release \([0-9]*\)\.\([0-9]*\).*/cu\1\2/p')   # 例 cu130、cu128、cu126
$P torch torchvision --index-url https://download.pytorch.org/whl/$CU        # nvcc と同じ CUDA 版の torch
$P setuptools wheel                                                           # detectron2 のビルドに要る
$P --no-build-isolation --no-deps 'git+https://github.com/facebookresearch/detectron2.git@a1ce2f9'
$P --no-deps -e models/sam3
$P opencv-python~=4.12.0.88 einops~=0.8.1 pycocotools~=2.0.10 roma~=1.5.4 omegaconf~=2.3.0 pytorch_lightning \
  yacs~=0.1.8 fvcore pyrender~=0.1.45 termcolor~=3.2.0 diffusers==0.29.1 transformers~=4.57.3 accelerate~=1.12.0 \
  'imageio[ffmpeg]' timm ftfy regex iopath trimesh braceexpand~=0.1.7 cloudpickle~=3.1.2 psutil torchmetrics \
  webdataset scipy moderngl ai-edge-litert 'huggingface-hub<1.0' eva-decord
$P 'git+https://github.com/microsoft/MoGe.git' 'huggingface-hub<1.0'        # 固定を渡さないと 2.x に上がり transformers が読めなくなる
uv pip uninstall -p .venv/bin/python utils3d_moge || true                     # MoGe 同梱版は utils3d.pt が無い
$P --no-deps 'git+https://github.com/EasternJournalist/utils3d.git'
```

ヘッドレスの Linux では描画に EGL を使う：サーバーが `PYOPENGL_PLATFORM=egl`・`MODERNGL_BACKEND=egl` を自動で渡す（`libegl1` が要る）。

### Windows（RTX）

`pipeline/README.md` の Installation（conda か venv、torch は CUDA に合わせる、detectron2・`models/sam3`・`pip install -e .`）に加えて
`pip install moderngl ai-edge-litert triton-windows`。チームの RTX 5060 Ti 機には既存の `.venv` がある。

### 確認

```sh
<環境 B の python> -c "import torch; print(torch.cuda.is_available())"     # True
cd pipeline && <環境 B の python> -c "import sam3, detectron2, moge, utils3d, pyrender, moderngl, decord, ai_edge_litert, transformers, diffusers, pytorch_lightning; print('ok')"
```

## 3. 重み（GPU 機だけ）

```sh
HF_TOKEN=hf_... <環境 B の python> weights/fetch.py --dest <重みの置き場> --write_config    # リポジトリのルートで
```

- 合計約 7GB。SAM3・SAM-3D-Body は Hugging Face で**アクセス許諾が要る**（`HF_TOKEN` のアカウントで申請しておく）。
- `--write_config` が `pipeline/configs/body4d.yaml` の `ckpt_root` を書き換える（機械ごとの設定なので、この差分はコミットしない）。
- 一覧は `weights/manifest.json`。

## 4. 起動

| 変数 | 必須 | 例 | 意味 |
|---|---|---|---|
| `VVLOG_API_TOKEN` | ✓ | `openssl rand -hex 24` の出力 | アプリの接続設定に入れるトークン |
| `VVLOG_PYTHON` | GPU 機 | `pipeline/.venv/bin/python`（**絶対パス**で） | 環境 B の Python |
| `VVLOG_DATA_ROOT` | | `services/gpu_server/data` | ジョブ・成果物の保存先（既定 `./data`） |
| `VVLOG_PORT` | | `8765` | 待受ポート（127.0.0.1 のみで待ち受ける） |
| `VVLOG_GPU_IDS` | | `0` | 使う GPU |
| `VVLOG_MAX_TARGETS` / `VVLOG_HMR_STRIDE` / `VVLOG_TRACKER_SIZE` / `VVLOG_FUSION_DEPTH` | | `4` / `3` / `672` / `da2_indoor_s` | パイプラインの既定値 |

```sh
# Linux（環境 A が無ければ作ってから起動する）
VVLOG_API_TOKEN=... VVLOG_PYTHON=$PWD/../../pipeline/.venv/bin/python ./run_linux.sh

# Windows（PowerShell）
$env:VVLOG_API_TOKEN = "..."; .\run_local.ps1        # VVLOG_PYTHON の既定は pipeline\.venv\Scripts\python.exe

# どこでも（手動）
VVLOG_API_TOKEN=... VVLOG_PYTHON=... .venv/bin/python -m vvlog_server
```

確認：

```sh
curl http://127.0.0.1:8765/v1/health                                                      # {"status":"ok",...}
curl -H "Authorization: Bearer $VVLOG_API_TOKEN" http://127.0.0.1:8765/v1/capabilities   # GPU 名と VRAM
```

通しの確認（アップロード → 解析 → 描画 → 書き出し。10 秒の動画で L4 なら約 12 分）：

```sh
.venv/bin/python scripts/smoke_e2e.py --token $VVLOG_API_TOKEN --video <動画.mp4> --vrm <アバター.vrm>
```

## ローカルアプリサーバーとして起動する（ADR-0015）

Windows 版のアプリ本体。画面（SPA）も配信し、ブラウザで `http://127.0.0.1:<ポート>/` を開いて使う。

```sh
# 先に画面をビルドする: pnpm --filter @vvlog/desktop build（apps/desktop/dist）
VVLOG_LOCAL=1 VVLOG_PORT=0 python -m vvlog_server      # PowerShell: $env:VVLOG_LOCAL=1; $env:VVLOG_PORT=0
# → VVLOG_LOCAL_READY http://127.0.0.1:51234/   （この行の URL をランチャーがブラウザで開く）
```

| 環境変数 | 既定 | 意味 |
|---|---|---|
| `VVLOG_LOCAL` | 無し | `1` でローカルアプリサーバーにする |
| `VVLOG_PORT` | `8765` | `0` で空きポートを自動選定 |
| `VVLOG_API_TOKEN` | 起動ごとに自動生成 | 指定すればそれを使う |
| `VVLOG_SPA_DIR` | `apps/desktop/dist` | 配信する画面のビルド成果物 |
| `VVLOG_ALLOWED_ORIGINS` | 空 | 自分以外に許可するオリジン（カンマ区切り。GitHub Pages のオリジンなど） |

- `127.0.0.1` にだけバインドする（外部には出ない）。`Host` が `127.0.0.1`／`localhost`／`[::1]`＋実ポート以外、`Origin` が自分と `VVLOG_ALLOWED_ORIGINS` 以外のリクエストは `403`（DNS リバインディング・クロスサイト対策）。
- `GET /v1/local/info`（認証なし）が `{"mode":"local",…}` を返す。画面はこれでフルモードを判定する。トークンは**同一オリジンの呼び出しにだけ**含まれ、Pages など別オリジンには含まれない。
- `/v1/*` は従来どおり Bearer トークンが要る。
- `POST /v1/local/shutdown`（#31）：サーバーを穏やかに止める。トークン必須＋同一オリジンのみ（別オリジンは `403 SHUTDOWN_LOCAL_ONLY`）。ローカルモード以外では存在しない（`404`）。画面の 設定 > 一般 >『アプリを終了』から呼ぶ。

### ランチャー（`start.bat`／`start.ps1`、#31）
UI なしでサーバーを起動し、既定ブラウザで画面を開く。`python -m vvlog_server.launcher`（`--no-browser` で開かない）の薄いラッパー。

- ポートは `8765`（Pages の案内リンクが探す番号）を優先し、使用中なら空きポートを自動で選ぶ（`VVLOG_LAUNCHER_PORT` で優先ポートを変更）。
- 多重起動防止：`%LOCALAPPDATA%\vvlog\launcher.json`（pid・port・url）。2 つ目の起動は、既存インスタンスが生きていれば（pid が存在し `/v1/health` が答える）その URL をブラウザで開いて終わる。古い状態ファイルは自動で掃除する。`VVLOG_STATE_DIR` で場所を変更。
- 終了：起動した窓で Ctrl+C（子プロセスごと止める）／画面の『アプリを終了』。実行中のジョブは次回起動で `INTERRUPTED` になる（既存仕様）。トレイアイコンは付けていない（依存を増やさないため）。
- ログ：`%LOCALAPPDATA%\vvlog\logs\server.log`（2MB×5 世代でローテーション。`VVLOG_LOG_DIR` で変更）。データは `VVLOG_DATA_ROOT` 未指定なら `%LOCALAPPDATA%\vvlog\data`。

### ローカル FS API（`/v1/local/fs`、#23）
Tauri の `fs_*` コマンドの移植。ブラウザからプロジェクトフォルダを読み書きする。HTTP 経由なので、**許可ルートの外は `403 PATH_NOT_ALLOWED`**。

- 許可ルート：プロジェクト（`~/Videos/vvlog`）・アプリデータ・設定の各フォルダ（`GET /v1/local/info` の `dirs`。`VVLOG_LOCAL_PROJECTS_DIR`／`_APPDATA_DIR`／`_CONFIG_DIR` で変更）と、`VVLOG_LOCAL_ROOTS`（OS のパス区切りで複数）。
- パスは絶対パスだけ。`realpath` で `..`・シンボリックリンクを解いてから判定する。許可フォルダそのものの削除・上書きはできない。
- `PUT /text`・`PUT /raw` は一時ファイル → fsync → rename の原子的書き込み（`raw` は本体をディスクへストリーム）。`GET /raw` は Range 対応（`<img>`／`<video>` 用に `?t=<トークン>` でも認証できる）。`POST /append?expectedSize=` は再開できるダウンロード用。
- `POST /unzip` は zip-slip・絶対パス・シンボリックリンクの項目を、何も書く前に拒否する。

### ローカル media／hash API（`/v1/local/media/probe`・`/v1/local/hash/sha256`、#24）
Tauri の `media_probe`・`hash_sha256` の移植。ユーザーが選んだ動画（許可ルートの外にあることが多い）を扱うので、**絶対パスの実在する通常ファイル**だけを受け付け、返すのは長さ・解像度・sha256 などの導出値だけ（中身は返さない）。

- `POST /media/probe {"path"}` → `{durationSec, width, height, rotation, codec, fps, hasAudio, sizeBytes}`。ffprobe は `VVLOG_FFPROBE` → `VVLOG_BIN_DIR/ffprobe(.exe)`（同梱）→ PATH の順に探す。見つからなければ `503 FFPROBE_UNAVAILABLE`（画面は `<video>` で代用する）。映像トラックが無い・読めない場合は `422 MEDIA_UNSUPPORTED`。
- `POST /hash/sha256 {"path"}` → `{"sha256"}`。`Accept: text/event-stream` を付けると `progress`（`{"ratio"}`、1% ごと）→ `done`（`{"sha256"}`）をストリームする。同じファイル（パス・サイズ・更新時刻）は計算結果を再利用する。

### ネイティブダイアログ API（`/v1/local/dialog/{open-files|open-dir|save}`、#46）
ブラウザは実パスを渡せないので、サーバーが**この PC の画面に** Windows のダイアログを出して実パスを返す（#28 の決定）。Windows のみ実装（PowerShell + System.Windows.Forms。見えない最前面ウィンドウを親にして、ブラウザの背面に隠れないようにする）。他の OS は `501 DIALOG_UNAVAILABLE`。

- 本体は `{filters:[{name,extensions}], multiple, defaultPath, title}`。結果は `{paths:[…]}`（open-files）／`{path|null}`（open-dir・save）。キャンセルとタイムアウト（10 分）は空の結果で、エラーにしない。
- 同時に開けるのは 1 つ（`409 DIALOG_BUSY`）。ローカルアプリ自身（同一オリジン）からだけ開ける。Pages など別オリジンは、トークンを持っていても `403 DIALOG_LOCAL_ONLY`。
- **選んだものは `/v1/local/fs` の許可対象になる**（フォルダ＝ルート、ファイル＝そのファイルだけ）。許可を広げる唯一の経路で、再起動後も `VVLOG_DATA_ROOT/local/grants.json` から復元する（このファイルは許可ルートの外なので、ブラウザからは書き換えられない）。
- 引数は環境変数 `VVLOG_DIALOG_ARGS`（JSON）で渡し、スクリプトの文字列には入れない。

Windows での手動確認（PowerShell）：
```powershell
$env:VVLOG_LOCAL=1; $env:VVLOG_PORT=8765; python -m vvlog_server          # 別のシェルで:
$t = (irm http://127.0.0.1:8765/v1/local/info).token
irm -Method Post -Headers @{Authorization="Bearer $t"} -ContentType 'application/json' `
  -Body '{"filters":[{"name":"動画","extensions":["mp4","mov"]}]}' http://127.0.0.1:8765/v1/local/dialog/open-files
```

### 重みの取得 API（`/v1/local/weights`、#33）
初回に重み（`weights/manifest.json`、約 7GB。インストーラーには入れない）を取得する。`huggingface_hub` / torch には依存せず（環境 A で動く）、`https://huggingface.co/<repo>/resolve/main/<file>` を urllib で Range 再開しながら取る。

- `GET /weights` 保存先・ファイルごとの状態（`ok`/`unverified`/`partial`/`missing`）・空き容量・実行中ジョブ。`POST /weights/start {"hfToken"?}`（202。実行中は `409 WEIGHTS_BUSY`、空き不足は `507 WEIGHTS_DISK_FULL`）。`POST /weights/cancel`（`.part` は残す）。`GET /weights/events`（SSE: `progress`→`done`）。開始・中止は同一オリジンの画面からだけ。
- 保存先は `VVLOG_CKPT_ROOT`。ローカルモードで未指定なら `%LOCALAPPDATA%\vvlog\weights`（`VVLOG_LOCAL_APPDATA_DIR\weights`）。manifest は `VVLOG_WEIGHTS_MANIFEST` で差し替え。
- `<dest>.part` に書いて再開（Range）。切断は自動で数回再試行。完了後に sha256 を検証し、不一致は `.part` を消して 1 回だけ取り直す。固定値の無いファイルは計算結果を `job.unpinned` で返す。検証済みの印は保存先の `.vvlog-weights.json`（サイズ・更新時刻・sha256）。
- **HF トークンは Bearer ヘッダーにだけ載せ、メモリ以外（ログ・エラー・状態・ディスク）に残さない**。リダイレクト先が別ホスト（CDN）や HF 以外の URL には送らない。
- 失敗は `job.error.code`：`HF_TOKEN_REQUIRED`（トークン無しで 401/403）・`HF_TOKEN_INVALID`（401）・`HF_ACCESS_DENIED`（403＝ライセンス未承諾。`detail.url` に同意ページ）・`WEIGHTS_DISK_FULL`・`WEIGHTS_NETWORK`・`WEIGHTS_CHECKSUM_MISMATCH` ほか。
- 対象外：`hf_cache`（Depth Anything V2。transformers が HF キャッシュから読む）は引き続き `weights/fetch.py`。

## つまずきやすい点

| 症状 | 原因と対処 |
|---|---|
| アップロードの `complete` が `MEDIA_UNSUPPORTED`「動画として読めません」 | ffprobe が PATH に無い。§0 |
| `VVLOG_API_TOKEN を設定してください` で起動しない | 必須。空ではない値を入れる |
| ジョブが `PIPELINE_FAILED`、ログに `No module named ...` | `VVLOG_PYTHON` が環境 B を指していない（既定は `python`）。§2・§4 |
| ログに `... does not exist`（`sam3.pt`・`big-lama.pt`・`face_landmarker.task` など） | 重みが無いか `ckpt_root` が違う。§3 を `--write_config` 付きで |
| `401 Unauthorized`（重みの取得） | Hugging Face のアクセス許諾がまだ、または `HF_TOKEN` 未設定 |
| 描画で segfault（Linux） | TF / JAX が読み込まれて EGL が壊れる。サーバーは `USE_TF=0` 等を自動で渡す。手でスクリプトを動かすときも付ける |
| `ValueError: Key backend: 'module://matplotlib_inline...'`（Colab・Jupyter から手で実行したとき） | ノートブックの `MPLBACKEND` を引き継いでいる。`export MPLBACKEND=Agg`（サーバー経由のジョブは自動で Agg） |
| `huggingface-hub>=0.34.0,<1.0 is required ... found huggingface-hub==2.x` | 後から入れたもの（MoGe など）が hub を上げた。`uv pip install -p .venv/bin/python 'huggingface-hub<1.0'` |
| `The detected CUDA version (13.0) mismatches ... PyTorch (12.4)`（detectron2 のビルド） | torch を `nvcc --version` と同じ CUDA 版で入れ直す（§2） |
| `GPU_OOM` | 同じ GPU で他の処理が動いている。ジョブは 1 本ずつ流す設計（`VVLOG_GPU_IDS` は 1 枚） |
| サーバーを再起動したら実行中のジョブが `INTERRUPTED` | 仕様。アプリから再実行する |
| ジョブの詳細を見たい | `GET /v1/jobs/{id}/logs`、または `data/projects/<prj>/work/<job>/*.log` |

## 仕組み

| ジョブ | サーバーが実行するもの |
|---|---|
| analyze | ffmpeg（回転・CFR・フル/プロキシのフレーム・音声）→ `pipeline/scripts/offline_app_lite.py`（検出・SAM3 追跡・HMR）→ `pipeline/scripts/occlusion/run_occlusion_stage.py`（fusion v2・遮蔽物ユニット） |
| render | `pipeline/scripts/avatar_app.py --fusion_state … --targets … --vrm_map … --export_layers …` |
| export | `pipeline/scripts/avatar_app.py --hq <フル解像度> --overrides …` → ffmpeg（H.264 + AAC） |

ジョブは GPU 1 枚につき 1 本ずつ FIFO。状態・イベント・成果物は `VVLOG_DATA_ROOT` 以下のファイル
（`jobs/<jobId>/job.json`・`events.jsonl`、`projects/<projectId>/{media,work,artifacts}/`）。

```
vvlog_server/
  domain/        Job の状態機械、入力制約とプロキシ寸法、分割アップロード、成果物の一覧、手修正の展開（I/O なし）
  application/   ポート、ユースケース、ワーカー、stages/（ingest, track_mesh, occlusion, render, export）
  adapters/      ファイル保存、events.jsonl、FIFO、サブプロセス、ffmpeg、nvidia-smi、VRM の読み取り、フィクスチャ記録
  interface/     FastAPI（/v1）、pydantic スキーマ（openapi.yaml の元）、SSE、Range
  main.py        組み立て（composition root）
```

API のスキーマ（`interface/schemas.py`）を変えたら `.venv/bin/python scripts/export_openapi.py` で
`packages/contracts/openapi.yaml` を作り直す（テストが差分を検出する）。
