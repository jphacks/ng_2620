# ADR-0008 実装はハッカソン指定リポジトリの monorepo。パイプラインとモデルのコードはそこへ置き、重みは外部から取得する

- 状態：採択
- 日付：2026-10-24

## 文脈
- ハッカソンは**リポジトリ指定**で、成果物（アプリ・サーバー）とモデルを指定リポジトリに置く必要がある。
- このリポジトリ（`vlog_JPHack2026`）は研究・検証用で、パイプライン（`scripts/`、`utils/`）と上流モデルのコード（`models/sam3`、`models/sam_3d_body`、`models/diffusion_vas`）を含む。
- 重み（チェックポイント）は Hugging Face のゲート付き（SAM3、SAM-3D-Body）。サイズ：SAM3 約 3.3GB、SAM-3D-Body 約 2.7GB（MHR 含む）、ViTDet、Depth Anything V2 Metric Indoor Small（約 100MB）、MoGe-2（任意）。
- ライセンス：SAM License（SAM3 / SAM-3D-Body）は**ライセンス文を同梱すれば再配布可**。Depth Anything V2 Small は Apache-2.0。VRM は個別ライセンス（再配布禁止のものが多い）。
- GitHub LFS の 1 ファイル上限は無料プランで 2GB（Team 4GB、Enterprise 5GB）。SAM3 / SAM-3D-Body の重みはこれを超え、容量・帯域も有料になる。
- チームの方針（2026-10-24）：**重みはリポジトリに置かず、別の場所から取得する。無料であれば場所は問わない**。

## 決定
1. **monorepo**（指定リポジトリのルート）：
   ```
   apps/desktop/          Tauri アプリ（レーン A・B）
   services/gpu_server/   FastAPI サーバー（レーン C）
   services/mock_server/  モックサーバー（F14）
   pipeline/              このリポジトリから移す推論パイプライン（scripts/・utils/・configs/ を整理して配置）
   models/                上流モデルのコード（sam3, sam_3d_body）＋各 LICENSE
   weights/               manifest.json（ファイル名・sha256・サイズ・取得元・ライセンス）と取得スクリプト fetch.py のみ（重み本体は .gitignore）
   packages/contracts/    openapi.yaml、JSON Schema、合成規則のテストケース
   fixtures/              処理済みサンプルプロジェクト（モック・デモ用）
   designdocs/            このディレクトリを移す
   ```
2. **パイプラインの移植**：`scripts/offline_app_lite.py`・`scripts/avatar_app.py`・`scripts/occlusion/`・`scripts/avatar/`・`utils/`・`configs/` を `pipeline/` に移す。研究用スクリプト（`scripts/eval/` の大半、`app.py`（Gradio）、`modal_body4d.py`）は移さない。
   移植はレーン C の最初の作業。移した直後に `pipeline/` 単体で現行 e2e（10 秒動画）が通ることを確認してから改修する。
3. **重みの配置**：リポジトリには置かない。`weights/fetch.py` が `manifest.json` の取得元から `VVLOG_CKPT_ROOT` にダウンロードし、sha256 を検証する。取得元はすべて無料：
   | 重み | 取得元 | 備考 |
   |---|---|---|
   | SAM3 `sam3.pt` | Hugging Face `facebook/sam3` | ゲート付き：各自が HF でアクセス許諾を得て `HF_TOKEN` を設定 |
   | SAM-3D-Body `model.ckpt`・`assets/mhr_model.pt` | Hugging Face `facebook/sam-3d-body-dinov3` | 同上 |
   | MoGe-2 `model.pt`（FoV 推定） | Hugging Face `Ruicheng/moge-2-vitl-normal` | 公開 |
   | Depth Anything V2 Metric Indoor Small | Hugging Face `depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf` | 公開、Apache-2.0 |
   | ViTDet（人物検出） | detectron2 の公式配布 URL | 公開 |
   - ゲートの許諾が人ごとに要るのが手間になった場合は、チームの**非公開 Hugging Face モデルリポジトリ（無料）にミラー**する（SAM License はライセンス文の同梱を条件に再配布を認めている。ミラーには `LICENSE` を置く）。`manifest.json` の取得元を差し替えるだけで切り替えられる。
   - 推論サーバーの配備先では、取得済みの重みを `VVLOG_CKPT_ROOT` に置き、`configs/body4d.yaml` の `ckpt_root` を上書きする（Mac などクライアント側にはモデルを置かない）。
4. **VRM ファイルはリポジトリにコミットしない**（`.gitignore`）。サンプルプロジェクトとフィクスチャには、描画済みのレイヤーだけを含め、VRM 本体は含めない。使用する VRM は商用利用を含め利用条件を確認済み。

## 影響
- このリポジトリでの研究は継続できる。指定リポジトリへの反映は `pipeline/` へのコピーで行い、どの commit から移したかを `pipeline/UPSTREAM.md` に書く。
- 重みのダウンロードは合計約 7GB。デモ用サーバーには前日までに配置しておく。
