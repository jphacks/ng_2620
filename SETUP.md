# 開発環境と起動（vvlog）

このリポジトリは 3 つの部品でできている。どれを動かすかで、必要なものが違う。

| 部品 | 場所 | 何をする | 動く機械 |
|---|---|---|---|
| デスクトップアプリ | `apps/desktop` | Tauri 2 + React。取り込み・キャスト・編集・書き出しの画面 | Mac / Windows |
| GPU サーバー | `services/gpu_server` | ジョブを受けて推論を回し、成果物を返す API | どこでも（推論は NVIDIA GPU 機だけ） |
| モックサーバー | `services/mock_server` | GPU なしで本物と同じ API を返す（中身は仮） | どこでも |
| 推論パイプライン | `pipeline` | SAM-Body4D ＋ アバター置換。サーバーが別プロセスで呼ぶ | NVIDIA GPU 機 |

設計は [designdocs/](designdocs/README.md)、アプリとサーバーの約束（API）は [packages/contracts/CLIENT_GUIDE.md](packages/contracts/CLIENT_GUIDE.md)。

## まず：make setup（macOS / Linux）

```sh
make doctor     # 何も入れずに、足りないもの（NG）と版を一覧にする。足りなければ失敗で終わる
make setup      # 足りないものを入れる：Node（.nvmrc）・pnpm・Rust・Xcode CLT・Python 環境・ffmpeg、最後に pnpm install
make help       # ほかのコマンド（make dev / make mock / make test …）
```

`make setup`（= `scripts/setup.sh`）は、ネットのスクリプトを実行するもの（rustup、uv）とシステムに入れるもの（`brew install ffmpeg`、Xcode CLT）を
実行する前に y/N で聞く（`make setup ARGS=--yes` で聞かない。アプリだけなら `ARGS=--app-only`）。Node は nvm か fnm で入れるので、
どちらも無ければ案内して止まる。`pipeline/` の環境 B（GPU 機だけ）は作らない。
終わったら、今のシェルで `nvm use`（Node を切り替えたとき）と、新しいターミナル（Rust を入れたとき）。

**Windows は make / sh の対象外**。下の表のツールを手で入れて、`pnpm install` → `pnpm tauri dev`。

## 前提のツール

| ツール | 版 | 何に要る | 入れ方（macOS） |
|---|---|---|---|
| Node.js | 26 | アプリ | `brew install node@26`（nvm・fnm はリポジトリ直下の `.nvmrc`・`.node-version` を読むので `nvm install && nvm use` / `fnm use` でよい。mise は `mise settings add idiomatic_version_file_enable_tools node` で読むようになる） |
| pnpm | 10 | アプリ | `npm i -g pnpm@10`（Node 25 以降は corepack が同梱されない。pnpm 10 以降なら `package.json` の `packageManager` の版に自動で切り替わる） |
| Rust（stable） | 1.80 以上 | アプリ（Tauri のネイティブ部分） | `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh` → 新しいターミナルを開く |
| Xcode Command Line Tools | — | アプリ（macOS） | `xcode-select --install` |
| Python | 3.10 以上 | サーバー・モック | `brew install python@3.11` |
| uv | 推奨 | サーバー・モックの環境づくり | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| ffmpeg | 6 以上 | サーバー・モック（動画の検証と変換） | `brew install ffmpeg` |

Windows：Node 26・pnpm・Rust（rustup、MSVC ビルドツール）・Python・ffmpeg（`winget install Gyan.FFmpeg`）・WebView2（Windows 11 は標準）。

## いちばん早い確認：Mac でアプリ ＋ モック（GPU なし）

```sh
# ターミナル 1：モックサーバー（初回は環境づくりで 1 分ほど）
pnpm mock                                      # = sh services/mock_server/run.sh（Windows は pnpm mock:win）。http://127.0.0.1:8766、トークン mock

# ターミナル 2：アプリ
pnpm install
pnpm tauri dev                                 # 初回は Rust のコンパイルで 1〜2 分
```

アプリの「設定（右上の歯車）> 接続」で「開発用モック」を選んで「既定にする」（開発ビルドには最初から登録されている。起動時に選ばせたいなら
`VITE_BACKEND=mock pnpm tauri dev`）。ステータスバー左のランプが緑になれば接続できている。
サーバーなしで画面だけ見るなら `pnpm --filter @vvlog/desktop dev:memory`（ブラウザ、シミュレーター）。詳しくは `apps/desktop/README.md`。

## 本物の推論：GPU 機でサーバー ＋ Mac のアプリ

1. GPU 機で [services/gpu_server/README.md](services/gpu_server/README.md) の §1〜§4（サーバーの環境、パイプラインの環境、重み、起動）。
2. デモの接続は Tailscale：[services/gpu_server/DEPLOY.md §4](services/gpu_server/DEPLOY.md#4-デモmac-から-tailscale-越し)。
3. Mac のアプリの「設定 > 接続」に URL（`https://<機名>.<tailnet>.ts.net`）とトークンを登録する。

## 接続先の切り替え（モック ⇄ 実際の GPU サーバー）

アプリは**接続プロファイル**で接続先を選ぶ。モックも実サーバーも同じ API なので、アプリ側で変えるのは接続先（URL とトークン）だけ。

| 接続先 | URL | トークン | どう用意するか |
|---|---|---|---|
| 開発用シミュレーター | `memory://sim` | — | アプリの中だけで動く（サーバー不要、成果物は合成画像）。開発ビルドに最初から入っている |
| 開発用モック | `http://127.0.0.1:8766` | `mock` | `pnpm mock`。開発ビルドに最初から入っている |
| この PC（アプリが起動） | `http://127.0.0.1:<ポート>` | アプリが自動で作る | 設定 > 接続 >「この PC の GPU サーバー」で `services/gpu_server` のフォルダと推論用 Python を選んで［起動］。接続「この PC」が自動で登録・既定になる（Windows で確認済み。事前に `services/gpu_server/.venv` が要る：README §1） |
| GPU サーバー（同じ PC、手で起動） | `http://127.0.0.1:8765` | サーバーの `VVLOG_API_TOKEN` | `services/gpu_server/README.md` §4 で起動して、プロファイルを追加 |
| GPU サーバー（別の PC） | `https://<機名>.<tailnet>.ts.net` | 同上 | GPU 機で起動 ＋ Tailscale（`DEPLOY.md` §4）して、プロファイルを追加 |

**アプリでの操作**：右上の歯車（設定）→「接続」→「プロファイルを追加」→ 名前・種別（ローカル / リモート）・URL・トークンを入れる →
「接続テスト」（GPU 名・VRAM・API 版が出れば OK）→「既定にする」。ステータスバー左のランプが緑になれば、その接続先を使っている。
「開発用」と付いた 2 つは開発ビルド（`pnpm tauri dev`）にだけ出る。

**起動時の既定**は `apps/desktop/.env.*` の `VITE_BACKEND` で決まる：

| モード | ファイル | `VITE_BACKEND` | 既定の接続 |
|---|---|---|---|
| `pnpm tauri dev` / `pnpm dev` | `.env.development` | `http` | 登録した接続（開発用の 2 つも一覧に出る。`VITE_BACKEND=mock` を付けると開発用モックを既定に） |
| `pnpm --filter @vvlog/desktop dev:memory` | `.env.memory` | `memory` | 開発用シミュレーター |
| 製品ビルド | `.env.production` | `http` | 登録したプロファイルだけ（開発用の 2 つは出ない） |

注意：

- **解析・描画の結果はサーバー側にある**。あるサーバーで解析したプロジェクトを別のサーバーで続けることはできない（開くと「接続先の切り替え」を聞かれる。モックで作ったプロジェクトは実サーバーでは描画・書き出しできないので、実サーバーでは取り込みからやり直す）。
- トークンは設定の保存先に入る（キーチェーン保存は P1）。`http://` のリモートは警告が出る（デモは Tailscale の `https://`）。
- 接続できないとき：`curl <URL>/v1/health` が `{"status":"ok"...}` を返すか、トークン付きの `curl -H "Authorization: Bearer <token>" <URL>/v1/capabilities` が通るかを先に確かめる。

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
| `pnpm tauri dev` が `failed to run 'cargo metadata' command ... No such file or directory (os error 2)`（または `cargo: command not found`）で止まる | Rust が入っていないか、PATH に無い。`make doctor` で確かめ、無ければ `make setup`。入れたあとは新しいターミナルを開く（または `source ~/.cargo/env`。VS Code の中なら VS Code ごと起動し直す） |
| アプリで動画を入れると「動画として読めません」 | サーバー（モック）側の ffprobe が PATH に無い。`brew install ffmpeg` してモックを再起動 |
| モックが `ModuleNotFoundError: fastapi` | `python -m mock_server` を素の Python で起動している。`sh services/mock_server/run.sh` を使う |
| `pnpm` が `WARN Unsupported engine: wanted: {"node":">=26"}` と出す | Node が 26 より古い（止まりはしない）。`nvm use`（`.nvmrc` を読む）などで 26 に切り替える |
| `pnpm install` が「modules directories will be removed and reinstalled」と聞く | 古い pnpm（9 以下）で入れた `node_modules` が残っている。そのまま進めてよい。`pnpm -v` が 10 以上かも確認 |
| 接続先のランプが赤 | モック / サーバーが起動していない、URL・トークン違い。`curl http://127.0.0.1:8766/v1/health` で確認 |
| GPU サーバーのジョブが失敗する | `services/gpu_server/README.md` の「つまずきやすい点」 |
