# 開発環境と起動（vvlog）

このリポジトリは 3 つの部品でできている。どれを動かすかで、必要なものが違う。

| 部品 | 場所 | 何をする | 動く機械 |
|---|---|---|---|
| デスクトップアプリ | `apps/desktop` | Tauri 2 + React。取り込み・キャスト・編集・書き出しの画面 | Mac / Windows |
| GPU サーバー | `services/gpu_server` | ジョブを受けて推論を回し、成果物を返す API | どこでも（推論は NVIDIA GPU 機だけ） |
| モックサーバー | `services/mock_server` | GPU なしで本物と同じ API を返す（中身は仮） | どこでも |
| 推論パイプライン | `pipeline` | SAM-Body4D ＋ アバター置換。サーバーが別プロセスで呼ぶ | NVIDIA GPU 機 |

設計は [designdocs/](designdocs/README.md)、アプリとサーバーの約束（API）は [packages/contracts/CLIENT_GUIDE.md](packages/contracts/CLIENT_GUIDE.md)。

## 前提のツール

| ツール | 版 | 何に要る | 入れ方（macOS） |
|---|---|---|---|
| Node.js | 22 | アプリ | `brew install node@22` |
| pnpm | 9 | アプリ | `corepack enable`（または `npm i -g pnpm@9`） |
| Rust（stable） | 1.80 以上 | アプリ（Tauri のネイティブ部分） | `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh` → 新しいターミナルを開く |
| Xcode Command Line Tools | — | アプリ（macOS） | `xcode-select --install` |
| Python | 3.10 以上 | サーバー・モック | `brew install python@3.11` |
| uv | 推奨 | サーバー・モックの環境づくり | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| ffmpeg | 6 以上 | サーバー・モック（動画の検証と変換） | `brew install ffmpeg` |

Windows：Node 22・pnpm・Rust（rustup、MSVC ビルドツール）・Python・ffmpeg（`winget install Gyan.FFmpeg`）・WebView2（Windows 11 は標準）。

## いちばん早い確認：Mac でアプリ ＋ モック（GPU なし）

```sh
# ターミナル 1：モックサーバー（初回は環境づくりで 1 分ほど）
sh services/mock_server/run.sh                 # = pnpm mock。http://127.0.0.1:8766、トークン mock

# ターミナル 2：アプリ
pnpm install
pnpm tauri dev                                 # 初回は Rust のコンパイルで 1〜2 分
```

アプリの接続先は開発ビルドでは「開発用モック」が最初から入っている。緑のランプが点けば接続できている。
サーバーなしで画面だけ見るなら `pnpm --filter @vvlog/desktop dev:memory`（ブラウザ、シミュレーター）。詳しくは `apps/desktop/README.md`。

## 本物の推論：GPU 機でサーバー ＋ Mac のアプリ

1. GPU 機で [services/gpu_server/README.md](services/gpu_server/README.md) の §1〜§4（サーバーの環境、パイプラインの環境、重み、起動）。
2. デモの接続は Tailscale：[services/gpu_server/DEPLOY.md §4](services/gpu_server/DEPLOY.md#4-デモmac-から-tailscale-越し)。
3. Mac のアプリの「設定 > 接続」に URL（`https://<機名>.<tailnet>.ts.net`）とトークンを登録する。

## テスト

| 部品 | コマンド | GPU |
|---|---|---|
| アプリ | `pnpm test` / `pnpm typecheck` / `pnpm lint` | 不要 |
| サーバー | `cd services/gpu_server && .venv/bin/python -m pytest -q` | 不要（推論は偽物で代用） |
| モック | `cd services/mock_server && ../gpu_server/.venv/bin/python -m pytest -q` | 不要 |
| パイプラインの単体 | `uv run --no-project --with pytest --with numpy --with opencv-python-headless --with scipy python -m pytest -q pipeline/scripts/occlusion/test_units.py pipeline/scripts/avatar/test_overrides.py` | 不要 |
| 通し（実サーバー） | `services/gpu_server/.venv/bin/python services/gpu_server/scripts/smoke_e2e.py --url ... --token ... --video ... --vrm ...` | 要 |
| 速度の再現 | `pipeline/scripts/eval/e2e.py`（`pipeline/UPSTREAM.md`） | 要 |

## よくあるつまずき

| 症状 | 対処 |
|---|---|
| `pnpm tauri dev` で `cargo: command not found` | Rust を入れたあと新しいターミナルを開く（または `source ~/.cargo/env`） |
| アプリで動画を入れると「動画として読めません」 | サーバー（モック）側の ffprobe が PATH に無い。`brew install ffmpeg` してモックを再起動 |
| モックが `ModuleNotFoundError: fastapi` | `python -m mock_server` を素の Python で起動している。`sh services/mock_server/run.sh` を使う |
| 接続先のランプが赤 | モック / サーバーが起動していない、URL・トークン違い。`curl http://127.0.0.1:8766/v1/health` で確認 |
| GPU サーバーのジョブが失敗する | `services/gpu_server/README.md` の「つまずきやすい点」 |
