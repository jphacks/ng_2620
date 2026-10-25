# apps/desktop — vvlog デスクトップアプリ（Tauri 2 + React + TS）

レーン A（デスクトップ・ワークフロー）の実装。F15 アプリ枠 / F01 プロジェクト / F02 取り込み / F03 接続 / F05 ジョブ（UI）/ F06 キャスト / F07 アバターライブラリ / F11 書き出し（UI）。
設計は [designdocs/architecture/overview.md](../../designdocs/architecture/overview.md) と [parallel-plan.md](../../designdocs/architecture/parallel-plan.md)。

## 起動

前提：Node 26（`nvm use` / `fnm use` はリポジトリ直下の `.nvmrc`・`.node-version` を読む。mise は idiomatic version file を有効にすると読む）、pnpm 10（`npm i -g pnpm@10`。Node 25 以降は corepack が同梱されない）。

初めては、リポジトリのルートで `make doctor`（足りないものの一覧）→ `make setup`（入れる。[SETUP.md](../../SETUP.md)）。Windows は make / sh の対象外なので、下の pnpm のコマンドをそのまま使う。

```sh
pnpm install                         # リポジトリのルートで（make install）

# ブラウザで http://localhost:1420（ローカルサーバーなし＝制限モード。ファイルは IndexedDB に残る）
pnpm --filter @vvlog/desktop dev          # 既定の接続 = 登録した接続（開発用モック・シミュレーターも一覧に出る。モックは `pnpm mock` で起動）
pnpm --filter @vvlog/desktop dev:memory   # 既定の接続 = シミュレーター（サーバー不要。ウィザードを端から端まで流せる）

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

フルモードのファイル選択は Windows のネイティブダイアログ（サーバー側、#46）、ドロップは `<appData>/imports/<id>/` へ分割転送してからサーバー上のパスで扱う（#47。受け付けない先には送らない／起動時に、プロジェクトから参照されていない 24 時間以上前の取り込みを掃除）。
`pnpm test` には実サーバーを起動する通しテスト（`localApi.integration.test.ts`）が含まれる（`services/gpu_server/.venv` が無ければ skip）。

### E2E（Playwright、#39）

実ブラウザ（Chromium・WebKit）で、モックサーバー（GPU なし。`services/mock_server`、再生用のステージ）を相手に主要フローを通す。`e2e/` に 2 本：

| spec | 画面の配信元 | 通すこと |
|---|---|---|
| `full-mode.spec.ts` | ローカルアプリサーバー（`mock_server --local`） | 起動 → 動画のドロップ（サーバーへ取り込み・ffprobe）→ 解析 → アバターのドロップ → キャスト → 描画 → エディタ → 書き出し（MP4 ができる）→ 開き直し → ショートカット |
| `restricted-mode.spec.ts` | 静的配信（`/ng_2620/` の下。Pages 相当） | 案内バナー → 外部 GPU の登録・接続テスト（**CORS 拒否／届かない**の診断）→ IndexedDB に残る → zip の読み込み → 元の動画の選び直し → 送信先の確認 → 外部 GPU へ送って解析 → zip の書き出し |

```sh
pnpm --filter @vvlog/desktop build                                      # dist/（画面）
pnpm --filter @vvlog/desktop exec playwright install chromium webkit    # 初回だけ（Linux は --with-deps）
pnpm --filter @vvlog/desktop e2e                                        # 3 つのサーバーは playwright.config.ts が起動する
pnpm --filter @vvlog/desktop e2e:chromium                               # Chromium だけ
```
前提：`services/gpu_server/.venv`（環境 A）と ffmpeg。`VVLOG_E2E_PYTHON` で Python を変えられる。CI は `.github/workflows/e2e.yml`。
E2E は src を import しない（Node の型除去が src の TypeScript を読めないため）。画面の文言の写しは `e2e/strings.ts` で、ずれると `pnpm test`（`src/e2e-strings.test.ts`）が失敗する。
Playwright 同梱の Chromium は H.264 を再生できない。動画の `<video>` プレビューは E2E の対象外。差分と手動確認の一覧は [designdocs/ux/browser-parity-checklist.md](../../designdocs/ux/browser-parity-checklist.md)。

### GitHub Pages への公開（#37）

`.github/workflows/pages.yml` が `main` への push で `pnpm --filter @vvlog/desktop build` の `dist/` を Pages に公開する（初回はリポジトリの Settings > Pages で Source を **GitHub Actions** にする）。ビルドは**相対パス**（`base: "./"`）なので、ローカルサーバーの `/` でも Pages の `/<リポジトリ名>/` でも同じ `dist` が動く。ルーティングは `#` 付きなので 404.html のフォールバックは要らない。公開した画面はローカルサーバーが無いので制限モードで動く。

### 制限モード（Pages 単体、#27）

ローカルサーバーが見つからないとき（GitHub Pages など）は**制限モード**になる。保存先はブラウザの IndexedDB（`vvlog-fs`）で、プロジェクト・キャッシュ・アバター・設定が残る。ドロップ／選択した端末の動画ファイルだけは、ブラウザを閉じると消える（`File` への参照のため。解析済みなら以後は不要）。

- **Safari は一定期間操作が無いと IndexedDB を消すことがある**（ITP）。起動時に永続化（`navigator.storage.persist()`）を頼むが保証はない。大事なプロジェクトは、ホームの最近のプロジェクトの右クリック >［zip で保存］で書き出し、［zip を読み込む…］で戻す（`cache/` は入れない。サーバーから取り直せる）。
- 画面上部に案内バナー（閉じられる）。File System Access API は Safari 非対応のため使わない。
- **この PC のローカルアプリへの案内（#38）**: https の Pages で開いているときは、バナーに『ローカルアプリを探す』（Chrome／Edge）と、アドレス `http://127.0.0.1:8765/` の案内が出る。押したときだけ `no-cors` で既定ポートにつながるか確かめ、見つかればローカル版を開くリンクを出す（自動では切り替えない。CORS・トークンは不要）。Safari／Firefox は https のページから `127.0.0.1` を呼べないので、アドレスの案内だけ。
- **外部 GPU への接続（BYO、#44）**: 設定 > 接続で名前・URL・トークンを複数登録し、既定を切り替える。トークンはこのブラウザの中（制限モードは IndexedDB、フルモードはローカルサーバーの設定フォルダ）にだけ保存し、その接続先以外へは送らない（画面にも明記）。［接続テスト］は health → API 版 → 認証の順に調べ、**届かなかったときは理由を推定して案内する**：https のページから http:// の宛先（混在コンテンツ）／no-cors ではつながる＝CORS で拒否（許可に加えるオリジンを表示）／届かない。外部サーバーへ**初めて動画を送るとき**は、送信先の確認ダイアログを出す（URL を変えたら確認し直し。この PC には出さない）。

### この PC の GPU サーバー（フルモード）

フルモードでは、画面を配信しているローカルアプリサーバー自身が接続「この PC」として**自動で登録され、既定**になる（トークンは起動ごとに自動。設定 > 接続では編集できない）。別の GPU サーバーを既定にしている場合は、その選択を尊重する。
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
