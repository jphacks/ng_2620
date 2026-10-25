# apps/desktop — vvlog デスクトップアプリ（Tauri 2 + React + TS）

レーン A（デスクトップ・ワークフロー）の実装。F15 アプリ枠 / F01 プロジェクト / F02 取り込み / F03 接続 / F05 ジョブ（UI）/ F06 キャスト / F07 アバターライブラリ / F11 書き出し（UI）。
設計は [designdocs/architecture/overview.md](../../designdocs/architecture/overview.md) と [parallel-plan.md](../../designdocs/architecture/parallel-plan.md)。

## 起動

前提：Node 26（`nvm use` / `fnm use` はリポジトリ直下の `.nvmrc`・`.node-version` を読む。mise は idiomatic version file を有効にすると読む）、pnpm 10（`npm i -g pnpm@10`。Node 25 以降は corepack が同梱されない）。Tauri で動かすときだけ Rust（stable）も必要。

初めては、リポジトリのルートで `make doctor`（足りないものの一覧）→ `make setup`（入れる。[SETUP.md](../../SETUP.md)）。Windows は make / sh の対象外なので、下の pnpm のコマンドをそのまま使う。

```sh
pnpm install                         # リポジトリのルートで（make install）

# Tauri なし（ブラウザで http://localhost:1420）。ファイルはメモリ上にだけ置かれ、再読み込みで消える
pnpm --filter @vvlog/desktop dev          # 既定の接続 = 登録した接続（開発用モック・シミュレーターも一覧に出る。モックは `pnpm mock` で起動）
pnpm --filter @vvlog/desktop dev:memory   # 既定の接続 = シミュレーター（サーバー不要。ウィザードを端から端まで流せる）

# デスクトップアプリ
pnpm tauri dev                       # macOS / Windows

pnpm test        # Vitest（domain / application / SSE / VRM / 画面の通しテスト）
pnpm lint        # ESLint（層の依存規則を含む）
pnpm typecheck
pnpm build       # 型チェック + vite build（dist/）
```

開発ビルドでは、接続プロファイルに「開発用モック」と「開発用シミュレーター（`memory://sim`）」が自動で入る（設定 > 接続で切り替えられる）。

### フルモード（ローカルアプリサーバー）で試す（ADR-0015）

画面がローカルサーバーから配信されているとき、起動時に `GET /v1/local/info` で**フルモード**を判定し、ファイル・ハッシュ・ffprobe をサーバー越しにする（Tauri 版の `invoke` は使わない）。判定できなければ制限モード（メモリ上のファイル）。

```sh
pnpm --filter @vvlog/desktop build                                   # dist/
(cd services/gpu_server && VVLOG_LOCAL=1 VVLOG_PORT=8765 .venv/bin/python -m vvlog_server)   # → http://127.0.0.1:8765/ をブラウザで開く
# 画面を `pnpm dev` で編集しながら試す：Vite がサーバーへ /v1 を中継する（Host を書き換え Origin を外す）
VVLOG_DEV_SERVER=http://127.0.0.1:8765 pnpm --filter @vvlog/desktop dev       # → http://localhost:1420/
```

ファイル選択（#46）とドロップ取り込み（#47）は準備中なので、フルモードでは動画を開く操作がまだできない。
`pnpm test` には実サーバーを起動する通しテスト（`localApi.integration.test.ts`）が含まれる（`services/gpu_server/.venv` が無ければ skip）。

### この PC の GPU サーバーを使う（Windows）

設定 > 接続 >「この PC の GPU サーバー」で、`services/gpu_server` のフォルダと推論用の Python（`VVLOG_PYTHON`）を選んで［起動］。
アプリがサーバーを子プロセスとして起動し（`src-tauri/src/commands/server.rs`）、トークンを作って接続「この PC」（`http://127.0.0.1:<ポート>`）を既定にする。
アプリを閉じるとサーバーも止まる。`services/gpu_server/.venv` は事前に要る（`run_local.ps1` を一度実行すると作られる）。
シミュレーターの速さと失敗の再現は `.env.memory` の `VITE_SIM_SPEED` / `VITE_SIM_FAIL=track:NO_PERSON_FOUND`。

## 構成

```
src/
  domain/          外部依存なし。project / job / cast / avatar / connection / validation / upload / artifacts / overrides（形式のみ）
  application/
    ports/         ComputeBackendPort ほか（A・B 共有）
    usecases/      1 クラス 1 ユースケース（execute）。プロジェクトへの変更は ProjectMutation を返し、ストアが最新の状態に適用する
  infrastructure/
    compute/       HttpComputeBackend、sse.ts（Last-Event-ID 再開・バックオフ・heartbeat 監視）、Offline、InMemory（シミュレーター）
    project/       FsProjectRepository（= TauriProjectRepository）、InMemoryProjectRepository
    cache/         FsLayerCache（= TauriLayerCache：ダウンロード → sha256 照合 → zip 展開）
    avatar/        FsAvatarLibrary、vrmMeta.ts（VRM 0.x / 1.0 のメタ情報・必須ボーン・サムネイル）
    settings/ media/ system/ fs/   Tauri 版とブラウザ版
  presentation/
    app/           container.ts（DI）、ルーティング、タイトルバー・ステータスバー、ウィザード、エディタ枠
    shared/        UI 部品、tokens.css、i18n/ja.ts、ストア（project / jobs / connection）、CommandRegistry
    features/      home / import / connection / jobs / cast / avatar-library / export / settings
src-tauri/         Rust（fs / media(ffprobe) / hash / net / server（この PC の GPU サーバー））。capabilities/default.json
```

依存規則は ESLint（`import/no-restricted-paths`）で強制している：domain は外部ライブラリを使わない、presentation から infrastructure を直接 import しない（`container.ts` だけ例外）、feature 同士で import しない。
feature をまたぐ画面（取り込みウィザード、設定）は `presentation/app` で組み合わせる。

## 他レーンとの接点

| 相手 | どこ | 内容 |
|---|---|---|
| B（エディタ） | `src/presentation/app/editorSlots.tsx` | Viewer / Inspector / Timeline / LayersPanel / DevEditor のプレースホルダー。B の部品に差し替える（このファイルだけを変えればよい）。`/#/dev/editor` のルートも用意済み |
| B | `src/domain/overrides.ts` | `overrides.json` の型だけを置いた。区間演算などのロジックは B の `domain/occlusion.ts` |
| B | `InMemoryProjectRepository.seed()` | フィクスチャの `project.json` を入れてエディタを単独起動する |
| B（F12） | `Services.openSample` | ホームの「サンプルを開く」から呼ぶ。未設定なら「まだ同梱されていません」 |
| C（契約） | `src/domain/job.ts` ほか、`infrastructure/compute/normalize.ts` | `packages/contracts/openapi.yaml` がまだ無いので、contracts.md から型を起こした。入ったら `openapi-typescript` の生成型と突き合わせる（`/v1/capabilities` の形は推測で、normalize.ts で吸収している） |
| C（モック） | `pnpm mock` | ルートのスクリプトは `services/mock_server` を起動する（C が用意） |

### 契約への追加の提案（contracts.md に未記載のもの）

- `project.json` の `compute.upload: { uploadId, chunkSize }`：アップロードの再開用（complete で消す）。
- `project.json` の `jobs.exports[].renderJobId / settings`：再書き出しの要否の判定と再試行用。
- `AvatarMeta.license.modification`（VRM 1.0 の改変可否。アプリ内だけで使う）。

## 未対応・確認していないこと

- Rust 側は Windows（stable、MSVC）で `cargo check` / `cargo clippy` / `cargo build` / `cargo test` が通ることを確かめた。macOS ではまだビルドしていない。
- `pnpm tauri dev` が `failed to run 'cargo metadata' command ... No such file or directory (os error 2)`（`program not found`）で止まるときは、Rust が無いか `cargo` が PATH に無い。`make doctor` で確かめ、無ければ `make setup`。Rust を入れた後はターミナル（VS Code なら VS Code ごと）を起動し直す。
- ffprobe のサイドカーは同梱していない。実行ファイルの隣か PATH の `ffprobe` を使い、無ければ `<video>` で長さと寸法だけを読む（コーデック・音声はサーバーの ingest に任せる）。
- macOS の `.icns` は未作成。`pnpm tauri icon src-tauri/icons/icon.png` で作れる。
- キーチェーン保存（P1）、HTTP 許可範囲の絞り込み（P1）。
- この PC の GPU サーバーは macOS では試していない（停止は子プロセスだけで、推論のサブプロセスまでは止めない）。
