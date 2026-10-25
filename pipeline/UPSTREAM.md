# pipeline/ の出どころと取り込み方

| 層 | 出どころ |
|---|---|
| 土台 | 公式 SAM-Body4D（Mingqi Gao）`21af1020979e`（2026-01-27）。MIT。上流モデルのコード `models/`（sam3・sam_3d_body：SAM License、diffusion_vas：MIT） |
| 改善 | 実験用 sandbox `vlog_JPHack2026` の `image_analytics`。**`3216c68` まで取り込み済み**（人物除去 mosaic・表情・遠い顔の表情の平滑化・近い人物の首の配置と頭の大きさ・描画ループと fusion の並列化・全身高からのアバターの大きさ・足裏より上の「地面」を隠さない・人物除去で前にある物を残す／穴の周りの色合わせ／周りが合わない候補フレームを捨てる、まで。レポートだけのコミット・レポートの動画・vlog 自体の `.gitignore` は除く）（作者・メッセージはそのまま） |
| サービス化 | このリポジトリで追加（下表） |

## 取り込みの方針

- `vlog_JPHack2026` は実験用。一定の改善が確認できたものだけを、ここへ取り込んで採用する。
- 取り込むのは推論に要るコードだけ。実験の記録やスナップショット（`reports/`、`testdata/`）、検証・比較用スクリプト（`scripts/eval/` の
  `e2e.py` 以外）、デモ（`app.py`、`modal_body4d.py`、`gpu_test.py`、`assets/` の動画）は入れない。
- 取り込んだ後は `scripts/eval/e2e.py` で、高速化が再現していること（ステージ別の時間）と出力が変わっていないことを確かめる。

## サービス化で加えたもの（vlog にはない）

| ファイル | 内容 |
|---|---|
| `scripts/occlusion/run_occlusion_stage.py`、`units.py` | occlusion ステージ：fusion v2 の判定の保存（`fusion_state.pkl`）と遮蔽物ユニット |
| `scripts/avatar_app.py` | `--targets` / `--vrm_map` / `--avatar_ids` / `--fusion_state` / `--export_layers` / `--overrides` |
| `scripts/avatar/occlusion.py` | 置換しない人物の前後判定（`real`）、手修正（`Overrides`） |
| `scripts/avatar/layers_export.py` | 編集用レイヤーの書き出し |
| `scripts/offline_app_lite.py` | `--max_targets` |
| `configs/body4d.yaml` | 機械ごとの設定（`weights/fetch.py --write_config` が `ckpt_root` を書く） |

vlog の更新を取り込むときに衝突するのは、主に `avatar_app.py` と `offline_app_lite.py`（上の引数の部分）。
