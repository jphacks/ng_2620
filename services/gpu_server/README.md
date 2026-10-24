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

### Linux（L4 で確認済み、Python 3.11〜3.13）

```sh
cd pipeline
python3 -m venv .venv && . .venv/bin/activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124      # GPU の CUDA に合わせる
pip install --no-build-isolation --no-deps 'git+https://github.com/facebookresearch/detectron2.git@a1ce2f9'
pip install --no-deps -e models/sam3
pip install opencv-python~=4.12.0.88 einops~=0.8.1 pycocotools~=2.0.10 roma~=1.5.4 omegaconf~=2.3.0 pytorch_lightning \
  yacs~=0.1.8 fvcore pyrender~=0.1.45 termcolor~=3.2.0 diffusers==0.29.1 transformers~=4.57.3 accelerate~=1.12.0 \
  'imageio[ffmpeg]' timm ftfy regex iopath trimesh braceexpand~=0.1.7 cloudpickle~=3.1.2 psutil torchmetrics \
  webdataset scipy moderngl ai-edge-litert 'huggingface-hub<1.0'
pip install 'git+https://github.com/microsoft/MoGe.git' && pip uninstall -y utils3d_moge \
  && pip install --no-deps 'git+https://github.com/EasternJournalist/utils3d.git'
pip install eva-decord            # Python 3.13。3.12 以下なら decord
```

ヘッドレスの Linux では描画に EGL を使う：サーバーが `PYOPENGL_PLATFORM=egl`・`MODERNGL_BACKEND=egl` を自動で渡す（`libegl1` が要る）。

### Windows（RTX）

`pipeline/README.md` の Installation（conda か venv、torch は CUDA に合わせる、detectron2・`models/sam3`・`pip install -e .`）に加えて
`pip install moderngl ai-edge-litert triton-windows`。チームの RTX 5060 Ti 機には既存の `.venv` がある。

### 確認

```sh
<環境 B の python> -c "import torch; print(torch.cuda.is_available())"     # True
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
| `VVLOG_PYTHON` | GPU 機 | `pipeline/.venv/bin/python` | 環境 B の Python |
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

## つまずきやすい点

| 症状 | 原因と対処 |
|---|---|
| アップロードの `complete` が `MEDIA_UNSUPPORTED`「動画として読めません」 | ffprobe が PATH に無い。§0 |
| `VVLOG_API_TOKEN を設定してください` で起動しない | 必須。空ではない値を入れる |
| ジョブが `PIPELINE_FAILED`、ログに `No module named ...` | `VVLOG_PYTHON` が環境 B を指していない（既定は `python`）。§2・§4 |
| ログに `... does not exist`（`sam3.pt`・`big-lama.pt`・`face_landmarker.task` など） | 重みが無いか `ckpt_root` が違う。§3 を `--write_config` 付きで |
| `401 Unauthorized`（重みの取得） | Hugging Face のアクセス許諾がまだ、または `HF_TOKEN` 未設定 |
| 描画で segfault（Linux） | TF / JAX が読み込まれて EGL が壊れる。サーバーは `USE_TF=0` 等を自動で渡す。手でスクリプトを動かすときも付ける |
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
