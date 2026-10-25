<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="designdocs/brand/color-dark/svg/virtualtwin-logo-horizontal-dark.svg">
    <img src="designdocs/brand/color/svg/virtualtwin-logo-horizontal.svg" alt="VirtualTwin（バーチャルツイン）" width="480">
  </picture>
</h1>

実写動画に映る人物を、**本人の動きのまま VRM アバターに置き換える**ツール。
動画から人物の追跡・3D メッシュ復元・遮蔽（人物の前後関係）の判定・人物除去・VRM 描画・合成までを GPU サーバーで自動化し、
画面（ブラウザ）から取り込み・キャスト（人物へのアバター割り当て）・編集・書き出しを行う。

JPHACKS 2026 のチーム ng_2620 の作品（ロゴとブランドのガイドラインは [designdocs/brand](designdocs/brand/README.md)）。

> 動かし方は 2 通り。**GPU のある Windows 機**にインストールしてブラウザで使うか、**GPU が無くても**、GitHub Pages の画面から自分で用意した GPU サーバーにつないで使う（機能は一部制限される）。

## 製品概要

### デモ

https://github.com/user-attachments/assets/533117e1-965b-4a22-8f6f-0287970d9dfd

会場で撮った動画を VirtualTwin で仕上げたロケ動画（30 秒、音声つき）。

### 背景（製品開発のきっかけ）
VTuber はゲーム実況などのリアルタイム配信のイメージが強いが、最近はトラッキング技術で、スタジオでライブ映像を収録したり、
家で全身を動かしながら配信したりできるようになった。

一方で、**外に出て配信・ロケをする**となると、アバターが本当にその場にいるような動画を作るには高度な編集技術が要る。
VTuber の 3D ライブには、お金を払って会場まで足を運ぶファンがいる。ファンは、アバターがまるで現実にいるように感じられることに価値を感じている。
だったら、**推しが現実の街を歩くロケ動画**も見たいはず。それを、編集が得意でない VTuber でも手軽に作れるようにしたい。

既存の手段は、モーションキャプチャ機材と 3D 合成か、1 コマずつの手作業の合成。どちらも重く、特に**人物の手前にある物（机・楽器・通行人）との前後関係**が難所になる
（[PRD](designdocs/PRD.md) §1）。

### 製品説明
撮った動画を入れてアバター（VRM）を選ぶだけで、映っている人物を**本人の動きのまま**アバターに置き換えた動画を作る。
人物を追いかけて体の 3D の動きを推定し、手前にある物と奥にある物を見分けてから、人物を消して背景を埋め、同じ動きのアバターを合成する。

<table>
  <tr>
    <th width="25%">① 人を見つけて追う</th>
    <th width="25%">② 体の 3D の動き</th>
    <th width="25%">③ 前後の判定</th>
    <th width="25%">④ アバターを合成</th>
  </tr>
  <tr>
    <td align="center"><img src="docs/slide/images/flow_1_frame.jpg" width="180"></td>
    <td align="center"><img src="docs/slide/images/flow_2_mesh.jpg" width="180"></td>
    <td align="center"><img src="docs/slide/images/flow_3_occlusion.jpg" width="180"></td>
    <td align="center"><img src="docs/slide/images/flow_4_composite.jpg" width="180"></td>
  </tr>
  <tr>
    <td align="center">ViTDet（検出）<br>SAM 3（追跡）</td>
    <td align="center">SAM 3D Body<br>MoGe-2（カメラの画角）</td>
    <td align="center">黄＝手前・青＝奥<br>SAM 3（物の切り出し）<br>Depth Anything V2（奥行き）<br>＋独自の判定</td>
    <td align="center">LaMa（背景の補完）<br>MediaPipe（表情）<br>VRM の描画</td>
  </tr>
</table>

**使い方**：① 動画を取り込む（ファイル選択かドロップ） → ② 映っている人物を解析して追跡 → ③ 人物ごとに VRM アバターを割り当て（キャスト） → ④ 描画 → ⑤ エディタで前後関係を手直し → ⑥ MP4 で書き出し。
操作はすべてブラウザで行い、重い処理は GPU 機が担う。

手前のノート PC は手前のまま残り、アバターはその後ろに立つ。自動の判定が外れた物は、エディタでクリックして「前」「後ろ」を選ぶとその場で直る。

#### 前後関係の補正（デモ）
自動の判定が外れた物は、エディタでクリックして「前」「後ろ」を選ぶと、推論をやり直さずにその場で直る（下の例は、手前のバッグを「前」「後ろ」に切り替えて、足が隠れる・出るを確かめている）。

<p align="center"><img src="docs/media/occlusion_edit.gif" width="720" alt="エディタで遮蔽物を「前」「後ろ」に切り替えると、アバターの足がバッグに隠れる・バッグの上に出る"></p>

### 既存の方法との比較

| | 処理時間（4 秒の動画） | 料金 | 前後関係 |
|---|---|---|:---:|
| Flow Studio（AI サービス） | 約 38 分 | 合計 15 秒まで無料 | × |
| 生成 AI | 約 2 分 | 約 1 ＄ | ○ |
| 公開モデルの組み合わせ（高速化前） |  2 分 27 秒 | 無料（ローカル） | △ |
| **VirtualTwin（最適化後）** | **1 分 22 秒** | **無料（ローカル）** | **○** |

公開モデルの組み合わせと VirtualTwin は、3DPW（von Marcard et al., ECCV 2018）の downtown_bar_00 の 120 フレーム（4 秒）を RTX 5060 Ti で処理し、出力 1080x1920、
追跡から書き出しまでの通しの時間（モデルの読み込みを含む）。

### できること
- 動画の取り込み、解析（人物の追跡）、人物ごとの VRM アバターの割り当て（キャスト）、描画、書き出しの一連の流れ
- 遮蔽（物体が人物の前か後ろか）の自動判定と、**エディタでの手修正**（編集用レイヤーをクライアントで WebGL2 合成する設計で、修正にサーバー往復を要しない）
- 解析や描画が終わると、ブラウザの通知で知らせる
- 大きな動画は分割して送り、途切れても再開できる
- Windows の GPU 機では、インストーラーで入れて**ブラウザで操作**する（ファイル選択はネイティブダイアログ、動画はドロップでも取り込める）
- GPU が無い人は、GitHub Pages の画面から**自分で用意した GPU サーバー（BYO）**につなぐ（**制限モード**。プロジェクトはブラウザの IndexedDB に残り、zip で書き出し・読み込みできる。ローカルのファイルを直接開けないので、元の動画は開き直すたびに選び直す）
- GPU が無い機械の開発向けに、同じ API を返す**モックサーバー**

### 対応環境

| 使い方 | 環境 |
|---|---|
| ローカル実行（フルモード） | Windows + NVIDIA GPU（RTX 5060 Ti で動作確認。GPU メモリ 12GB 級以上） |
| Pages + 自分の GPU サーバー（制限モード） | Chrome / Edge / Safari（Firefox は保証外）。サーバーは Linux でも可（L4 の VM で確認、Docker イメージあり） |
| 開発 | Mac でも、モックサーバー + ブラウザで画面を動かせる |

### 入力の制限（既定）
動画は 30 秒以下、長辺 1920px 以下、mp4 / mov。人物（ターゲット）は最大 4 人（[ADR-0011](designdocs/adr/0011-input-limits-and-resolutions.md)、サーバーの環境変数 `VVLOG_MAX_*` で変更可）。

### 目標としている数値（目標であり、実測ではない）
[PRD](designdocs/PRD.md) の成功指標：10 秒・1080p の動画で、取り込みから最初のプレビューまで 2 分以内、置換プレビューまで 8 分以内、前後関係の修正の反映 0.5 秒以内。
推論の速度は RTX 5060 Ti で約 0.72 秒/フレーム（2026-10-24 計測、ADR-0011 に記載）。

### 今後の展望
- Windows 以外のローカル実行（現在は Windows + RTX のみ。Mac などは Pages 版で外部 GPU につなぐ）
- 初回ブートストラップ（Python・torch・detectron2 の自動構築）の実機検証を進め、固定する版を確定する（[#30](https://github.com/jphacks/ng_2620/issues/30)）
- インストーラーのコード署名（現在は署名なしで、初回に SmartScreen の警告が出る）
- 企業向け機能（匿名化、チーム運用、監査）はスコープ外（[ADR-0010](designdocs/adr/0010-out-of-scope-enterprise-and-later.md)）

## 技術スタックの概要

| | |
|---|---|
| 画面の入れ物 | **ブラウザ**。UI を持たない Windows アプリ（ランチャー + ローカルサーバー）が画面を配信し、既定ブラウザで操作する |
| 実行環境 | ローカル実行は **Windows（RTX）のみ**。Mac などは GitHub Pages の画面（制限モード）から外部 GPU に接続 |
| ローカルファイル | ローカルサーバー経由。ファイル選択は Windows のネイティブダイアログ、ドロップはサーバーへ分割転送して取り込み |
| ローカル推論 | ローカルサーバー自身が `pipeline/` を呼ぶ。Python・torch・重みは初回起動時に取得（薄いインストーラー + 初回ブートストラップ） |
| 外部 GPU | URL + Bearer トークンで接続。**利用者が自分で GPU サーバーを用意する（BYO）**。運営ホストなし |
| 配布 | Windows のインストーラー（Inno Setup）とポータブル zip。同じ画面のビルドを GitHub Pages にも公開 |
| 対応ブラウザ | Chrome / Edge と Safari（Mac）を保証。Firefox は保証外 |
| 実装言語 | TypeScript + Python（Rust は使わない） |

決定の経緯は [ADR-0015](designdocs/adr/0015-web-first-windows-local-server.md)（親チケット [#18](https://github.com/jphacks/ng_2620/issues/18)）。
Windows での detectron2 の自動セットアップなど、固定する版が暫定の項目がある（`services/gpu_server/bootstrap-spec.json` の `provisional`）。
API の契約（`packages/contracts`）は画面とサーバーで共通に保つ。

## 構成

| 場所 | 中身 |
|---|---|
| `apps/desktop` | 画面（React + TypeScript）。ローカルサーバーが配信するほか、GitHub Pages にも公開する |
| `services/gpu_server` | GPU サーバー（FastAPI）。ジョブの受付・アップロード・成果物・進捗（SSE）を担い、推論は `pipeline/` を別プロセスで呼ぶ。`VVLOG_LOCAL=1` で、画面の配信・ファイル／ダイアログ API・初回ブートストラップを持つローカルアプリサーバーになる |
| `services/mock_server` | GPU なしで本物と同じ API・イベント・成果物名を返すモック（中身は `gpu_server` で、ステージだけを再生用に差し替え） |
| `pipeline` | 推論パイプライン（SAM-Body4D 系 + アバター置換）。出どころは [pipeline/UPSTREAM.md](pipeline/UPSTREAM.md) |
| `packages/contracts` | API（OpenAPI）・プロジェクトファイル・遮蔽の手修正の JSON Schema。[CLIENT_GUIDE](packages/contracts/CLIENT_GUIDE.md) |
| `installer` / `scripts` / `deploy` | Windows インストーラー（Inno Setup・`vvlog.cmd`）と配布物の組み立て、開発用スクリプト、外部 GPU 用（BYO）の Docker イメージ |
| `weights` | 重みの取得スクリプトとマニフェスト（重み自体はコミットしない） |
| `designdocs` | PRD・ADR・アーキテクチャ・UX・ブランド。入口は [designdocs/README.md](designdocs/README.md) |
| `docs/slide` | 発表スライド（Marp） |

動かし方は [SETUP.md](SETUP.md)。

## 開発技術

### 活用した技術

#### 画面（`apps/desktop`）
TypeScript ~5.6、React 18、Vite 6、Tailwind CSS 3.4、Radix UI、TanStack Query 5、Zustand 5、react-router-dom 6、react-resizable-panels、lucide-react、fast-png。
合成は WebGL2 を直接使う（`src/presentation/features/editor/compositor/Compositor.ts`）。テストは Vitest 4（jsdom、Testing Library）と Playwright（Chromium / WebKit の E2E）。
起動時にローカルサーバーを見つけると**フルモード**（ファイル・ハッシュ・ffprobe をサーバー越しに）、見つからなければ**制限モード**（IndexedDB・メモリ上のファイル）で動く。ビルドは相対パス（`base: "./"`）で、ローカルサーバーの `/` でも GitHub Pages でも同じ `dist` が動く。
構成は domain / application / infrastructure / presentation の層に分け、OS 機能はポートの裏に置いている（[ADR-0003](designdocs/adr/0003-clean-architecture-and-contract-first.md)）。
なお [ADR-0013](designdocs/adr/0013-frontend-stack.md) に書かれた `openapi-typescript` / `openapi-fetch` / `twgl.js` は `package.json` に入っておらず、現行コードでは使っていない。

#### ローカルアプリ（Windows、`services/gpu_server` の `VVLOG_LOCAL=1`）
UI を持たないランチャー（`vvlog_server.launcher`）がサーバーを起動して既定ブラウザで開く。待受は `127.0.0.1` のみで、起動ごとのトークンで保護する。多重起動防止、トレイアイコン（pystray）つき。
ファイル選択はサーバーが出す Windows のネイティブダイアログ（PowerShell + System.Windows.Forms）、動画のドロップは `<appData>/imports/` へ分割転送する。
配布は薄いインストーラー（Inno Setup 6、ユーザー単位・署名なし）で、uv・ffmpeg / ffprobe・コード・画面だけを入れる。初回起動で uv が環境 A を作り、環境 B（Python 3.12・PyTorch CUDA・detectron2）と重みは画面の案内から取得する。
detectron2 の Windows 用 wheel は GitHub Actions で作る。

#### GPU サーバー（`services/gpu_server`）
Python 3.11 以上、FastAPI、uvicorn、sse-starlette、pydantic 2、python-multipart、PyYAML、NumPy、Pillow、send2trash（Windows は pystray も）。ffmpeg / ffprobe（動画の検証・変換）が要る。
ジョブは GPU 1 枚につき 1 ワーカーの FIFO で、各ステージをパイプラインのサブプロセスとして実行する（[ADR-0014](designdocs/adr/0014-server-stack.md)）。
通信は HTTP(S) + SSE、認証は `Authorization: Bearer`（[ADR-0012](designdocs/adr/0012-transport-and-auth.md)）。ブラウザから外部のサーバーを使うために、CORS の許可・サイズ上限・TTL・トークン必須化の設定を持つ。
外部 GPU 用（BYO）には CUDA 対応の Docker イメージ（`deploy/docker`）がある。

#### 推論パイプライン（`pipeline`）
Python 3.12 以上、PyTorch（CUDA）。`weights/manifest.json` にある重み：

| モデル | 用途 | ライセンス（マニフェストの記載） |
|---|---|---|
| SAM 3（`facebook/sam3`） | 人物の追跡 | SAM License（Hugging Face で利用許諾が必要） |
| SAM-3D-Body（`facebook/sam-3d-body-dinov3`） | 3D メッシュ復元 | SAM License（同上） |
| MoGe-2（`Ruicheng/moge-2-vitl-normal`） | カメラの画角 | MIT |
| Depth Anything V2 Metric Indoor Small（`depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf`） | 前後判定の奥行き（初回に Hugging Face から取得） | Apache-2.0 |
| ViTDet（detectron2） | 人物検出 | コードは Apache-2.0、重みは未確定（下の「ライセンス」） |
| LaMa（big-lama） | 人物除去（インペイント） | Apache-2.0 |
| MediaPipe Face Landmarker | 表情（`ai-edge-litert` で実行） | Apache-2.0 |

遮蔽の補完（`pipeline/configs/body4d.yaml` の `completion`、既定で無効）には Diffusion-VAS と Depth Anything V2 Large を使う。
そのほか detectron2、pyrender / moderngl（描画）、decord（動画読み込み）、imageio-ffmpeg、diffusers 0.29.1、transformers 4.57 系。
重みは合計約 7GB で、リポジトリには含めず `weights/fetch.py` が取得して sha256 を検証する。

#### API の契約（`packages/contracts`）
OpenAPI（`openapi.yaml`）と JSON Schema（プロジェクトファイル、遮蔽の手修正）。

#### 開発・CI
Node 26、pnpm 10（ワークスペース）、uv、ESLint 9、Prettier、lefthook（pre-push の自動整形）、release-please（CHANGELOG と版管理）、Marp（スライド）。
GitHub Actions：`ci.yml`（prettier / eslint、画面の型チェック・vitest・ビルド、サーバーの pytest、画面 ⇄ ローカルサーバーの通しテスト）、`e2e.yml`（Playwright。手動実行）、
`pages.yml`（GitHub Pages へ公開）、`build-windows.yml`（インストーラーと zip）、`detectron2-wheel.yml`（Windows 用 wheel）、
`release.yml`（`main` への push で Windows 配布物をつけた draft Release を作る。公開は手動）、`release-please.yml`、`slide-pdf.yml`。Rust 関連のジョブは無い。

#### デバイス
- 推論：NVIDIA GPU。Windows の RTX 5060 Ti 機と、Linux の L4 の VM で動作を確認している（[services/gpu_server/README.md](services/gpu_server/README.md)）
- 画面：Windows（ローカルアプリ）か、Chrome / Edge / Safari で開く GitHub Pages。開発は MacBook（Apple Silicon）で、モックサーバー + ブラウザで続ける。デモでは Mac のブラウザから Windows GPU 機に Tailscale 越しでつなぐ（[ADR-0012](designdocs/adr/0012-transport-and-auth.md)）

### 独自技術

#### 注力したこと：速く、しかも前後関係まで
公開モデルをそのまま組み合わせると時間がかかり、3D 推定の段だけで GPU メモリを 16GB 使うので、手元の GPU（12GB 級）では動かない。
画質を目で確かめながら、処理の形そのものを変えて速くした（計測は実験用リポジトリで、RTX 5060 Ti・120 フレームの動画。採用した改善だけを `pipeline/` に取り込んでいる）。

| 工夫 | 効果 |
|---|---|
| SAM 3 を追跡に必要な部分（トラッカーと画像の特徴抽出）だけで組み、フレームは CPU に置く | 3D 推定の段：GPU メモリ 16.1 → 7.95GB、826 → 90 秒（RTX 4070 SUPER） |
| 前後判定で、物の切り出し（SAM 3）をキーフレームだけで行い、間のフレームは持ち越す | 前後判定：119 → 41 秒（判定のマスクの一致度 0.993） |
| 人物の追跡を 672px と Triton の注意機構で行う | 追跡：42.8 → 14.6 秒 |
| 体の 3D 推定を 3 フレームおきにし、間を補間する | 通し：94.2 → 86.2 秒（目視で差なし） |
| 人物を消した穴を、短い区間ごとに周りのフレームから作った背景で埋める | 通し：89.0 → 82.2 秒（画質は同等） |
| アバターの描画と隠しマスクの計算を並行に走らせる | アバターの段：49.1 → 42.8 秒（出力はバイト単位で一致） |

#### ハッカソンで開発した独自機能・技術
土台は上流の SAM-Body4D（MIT）。ハッカソンの期間中に開発したもの：

- 推論パイプラインの高速化と画質の改善：実験用リポジトリで試し、効果を確かめたものだけを `pipeline/` に取り込んだ（取り込みの記録は [pipeline/UPSTREAM.md](pipeline/UPSTREAM.md)、主な工夫と効果は上の「[注力したこと](#注力したこと速くしかも前後関係まで)」）。
  あわせて、人物除去（背景の補完）、表情、アバターの大きさと配置、前後判定の改善も行った
- 推論のサービス化：遮蔽判定の保存と遮蔽物ユニット（[pipeline/scripts/occlusion/](pipeline/scripts/occlusion)）、ターゲット指定・VRM 割り当て・手修正の適用・編集用レイヤーの書き出し（[avatar_app.py](pipeline/scripts/avatar_app.py)、[occlusion.py](pipeline/scripts/avatar/occlusion.py)、[layers_export.py](pipeline/scripts/avatar/layers_export.py)）
- GPU サーバー：ジョブキュー、分割アップロード（8MB、再開可）、成果物の Range 配信、SSE による段階的な進捗（[services/gpu_server/vvlog_server/](services/gpu_server/vvlog_server)）
- モックサーバー：実機なしで同じ API を再生（[services/mock_server/](services/mock_server)）
- 画面：取り込みから書き出しまでの流れと、遮蔽の手修正を行うエディタ（[apps/desktop/src/](apps/desktop/src)）
- Web 化：ローカルアプリサーバー（ランチャー、ネイティブダイアログ、ファイル API、初回ブートストラップ、重みの取得）と、Pages 向けの制限モード・外部 GPU への接続（[services/gpu_server/vvlog_server/](services/gpu_server/vvlog_server)、[installer/](installer)）

## ライセンス

このリポジトリのコードは MIT（[LICENSE](LICENSE)）。推論パイプラインの土台の [SAM-Body4D](https://github.com/gaomingqi/sam-body4d) も MIT。
各ライセンスは 2026-10-25 に公式のページ（モデルカード、LICENSE ファイル、PyPI）で確認した。

### モデルと重み（既定の処理で使うもの）

| モデル | 用途 | コード | 重み | 商用利用 | 主な条件 |
|---|---|---|---|:---:|---|
| [SAM 3](https://huggingface.co/facebook/sam3) | 人物の追跡、物の切り出し | SAM License | SAM License | 可 | Hugging Face で利用規約への同意が要る。再配布には規約の写しを付ける。論文等では利用を明記する。軍事・兵器などの用途は禁止 |
| [SAM 3D Body](https://huggingface.co/facebook/sam-3d-body-dinov3)（体のモデル MHR を含む） | 体の 3D の動き | SAM License | SAM License | 可 | 同上 |
| [DINOv3](https://github.com/facebookresearch/dinov3)（SAM 3D Body の骨格のコード） | 同上 | DINOv3 License | （別に取得しない） | 可 | SAM License と同様 |
| [MoGe-2](https://huggingface.co/Ruicheng/moge-2-vitl-normal) | カメラの画角 | MIT | MIT | 可 | 著作権表示 |
| [Depth Anything V2](https://github.com/DepthAnything/Depth-Anything-V2) Metric Indoor **Small** | 前後判定の奥行き | Apache-2.0 | Apache-2.0 | 可 | Small のみ Apache-2.0（Base / Large は CC-BY-NC-4.0 で、既定では使わない）。`-hf` 版のモデルカードにはライセンスの記載がなく、元の Small の公開時のライセンスによる |
| [ViTDet-H](https://github.com/facebookresearch/detectron2/tree/main/projects/ViTDet)（detectron2） | 人物の検出 | Apache-2.0 | **未確定** | 要確認 | 公式に重みのライセンスの記載がない。detectron2 の model zoo の重みは CC-BY-SA-3.0、事前学習に使った MAE は CC-BY-NC-4.0 |
| [LaMa](https://github.com/advimman/lama)（big-lama） | 人を消した背景の補完 | Apache-2.0 | Apache-2.0 | 可 | 著作権表示 |
| [MediaPipe Face Landmarker](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker) | 表情 | Apache-2.0 | Apache-2.0 | 可 | 著作権表示（実行は `ai-edge-litert`、Apache-2.0） |

既定では使わないもの：Depth Anything V2 **Large**（CC-BY-NC-4.0、非商用）と Diffusion-VAS（MIT の表記だが、元の Stable Video Diffusion は Stability AI Community License）は、
遮蔽の補完（`pipeline/configs/body4d.yaml` の `completion`、既定で無効）だけが使う。上流の `pipeline/scripts/setup.py` はこれらも取得するので、商用で使うときは `weights/fetch.py` を使う。
EdgeTAM（Apache-2.0）は実験用の選択肢で、既定では使わない。

### ライブラリ

| 部分 | 確認した範囲 | ライセンス |
|---|---|---|
| 画面（`apps/desktop`） | 実行時に入る npm パッケージ 53 個 | MIT 46、MIT OR Apache-2.0 5、ISC 1（lucide-react）、MIT AND Zlib 1（pako）。React・Radix UI・TanStack Query・Zustand・react-router・Tailwind CSS・Vite は MIT、TypeScript は Apache-2.0 |
| GPU サーバー（`services/gpu_server`） | 直接の依存 | FastAPI・pydantic・PyYAML は MIT、uvicorn・sse-starlette は BSD-3-Clause、python-multipart は Apache-2.0、NumPy は BSD-3-Clause ほか、Pillow は MIT-CMU |
| 推論パイプライン（`pipeline`） | 直接の依存 | PyTorch は BSD 系（同梱の部品に Apache-2.0 などを含む）、torchvision は BSD、detectron2・diffusers・transformers・accelerate・OpenCV・decord・fvcore・gradio・pytorch-lightning は Apache-2.0、pyrender・moderngl・einops・triton は MIT、imageio・imageio-ffmpeg は BSD-2-Clause、matplotlib は PSF 系、MoGe のコードは MIT |
| 外部コマンド | ffmpeg / ffprobe | ビルドにより LGPL-2.1 または GPL。このリポジトリには同梱しない |

### データとアバター

- 比較表の計測に使った [3DPW](https://virtualhumans.mpi-inf.mpg.de/3DPW/)（downtown_bar_00）は、**非商用の学術研究に限る**データセット。このリポジトリには 3DPW の映像を含めていない。
  出典：T. von Marcard, R. Henschel, M. J. Black, B. Rosenhahn, G. Pons-Moll. "Recovering Accurate 3D Human Pose in the Wild Using IMUs and a Moving Camera", ECCV 2018.
- VRM アバターは、各アバターのライセンス（VRM のメタ情報）に従う。リポジトリには含めない。
- スライド（`docs/slide`）の画像のうち、配信・ライブの画像は各社のプレスリリース（PR TIMES）から引用し、スライドに出典を記載している。
