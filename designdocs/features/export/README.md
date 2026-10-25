# F11 export — MP4（H.264＋音声）書き出し

> **注（ADR-0015、2026-10-25）**：この文書の Tauri・Rust・`src-tauri` に関する記述は、Tauri 廃止（ブラウザ ＋ ローカルアプリサーバー）より前のものです。現在の構成は [ADR-0015](../../adr/0015-web-first-windows-local-server.md)・[tauri-migration.md](../../architecture/tauri-migration.md)・[mode-matrix.md](../mode-matrix.md) を正とします。


| 項目 | 内容 |
|---|---|
| レーン | C（合成・エンコード）＋ A（書き出し画面） |
| 優先度 | P0（MP4 のみ）。他形式は FR-24（P2） |
| 関連 | FR-16 / [ADR-0005](../../adr/0005-preview-composite-client-export-server.md) / [ADR-0010](../../adr/0010-out-of-scope-enterprise-and-later.md) / [contracts §2.2 ExportParams](../../architecture/contracts.md#22-型) / [ux/screens S4](../../ux/screens.md#s4-書き出しf11) |
| 依存 | F08（render の成功）、F10（overrides）、F05（進捗・ダウンロード） |

## ユーザーストーリー
- 配信者として、元の解像度・元の音声のまま、そのまま投稿できる MP4 が欲しい。
- プロとして、直した前後関係が書き出しに反映されていてほしい。

## 受け入れ条件
- [ ] 書き出し画面（S4）：形式（MP4 のみ選択可、他は「今後対応」で無効表示）、解像度（元 / プロキシ）、画質（標準 CRF 18 / 高 CRF 14 / 軽量 CRF 23）、音声の有無、保存先、反映される手修正の件数。
- [ ] [書き出す] で `export` ジョブを投入（`overrides` を同梱）。進捗（合成 → エンコード）。完了で `export_video` をダウンロードして保存先に置き、`project.jobs.exports` に記録。[再生] [Finder で表示]。
- [ ] 出力：H.264 High、yuv420p、元の fps、元の解像度（回転適用後）、AAC 音声（元に音声があれば）、`+faststart`。
- [ ] 手修正（ユニット）が反映されている。手修正なしで書き出した場合、`preview_video` を拡大したものと見た目が同じ。
- [ ] 10 秒・1080x1920 で 2 分以内（L4 / RTX 級）。
- [ ] render 後にキャストが変わっているときは「再描画が必要です」で書き出しを止め、[再描画して書き出す] を出す。

## 設計
- サーバー：`ExportJob = [CompositeStage, EncodeStage]`
  - `composite`：`avatar_app.py --pipeline_out <pout> --out <work>/export --hq <full frames> --fusion_state ... --targets ... --vrm_map ... --overrides <overrides.json>`（[pipeline-integration §3.6](../../architecture/pipeline-integration.md#36-手修正の適用書き出し)）。`avatar_app` の mp4 出力（音声なし）を中間ファイルに。
  - `encode`：`ffmpeg -i video.mp4 -i audio.m4a -c:v libx264 -crf <crf> -pix_fmt yuv420p -c:a aac -movflags +faststart -shortest export.mp4`（`avatar_app` の出力が既に H.264 なら `-c:v copy` で音声だけ多重化し、CRF 指定時のみ再エンコード）。
  - `format` が `mp4_h264` 以外なら `NOT_IMPLEMENTED`。
- アプリ（A）：
  - usecases：`StartExport`（`needsRender` の確認 → overrides を読み → `submitJob("export", ...)`）、`SaveExportResult`（ダウンロード → 保存先 → 記録）。
  - presentation/features/export：設定フォーム、進捗、完了表示。保存先の選択は Tauri の `dialog` プラグイン。

## タスク分解
- C：
  1. §3.6 `--overrides`（`hide_frame` の判定上書き、範囲判定）＋単体テスト（人工の dec で上書きが効く）。
  2. `CompositeStage` / `EncodeStage`（音声の有無、CRF、faststart）。
  3. 実 GPU で手修正 1 件ありの書き出しを目視確認。
- A：
  4. 設定フォームと `StartExport` / `SaveExportResult` ＋テスト。
  5. 進捗・完了表示、Finder で表示（`tauri-plugin-opener`）。

## P1 / スコープ外
- 書き出しプリセットの保存（P1）、ProRes 4444 / PNG 連番 / レイヤー別 / VRMA・BVH（P2、[ADR-0010](../../adr/0010-out-of-scope-enterprise-and-later.md)）、アプリ内での書き出し（スコープ外、ADR-0005 で却下）。
