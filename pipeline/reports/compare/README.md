# 推論結果の比較画像

SSH 経由で動画を開けないため、確認用の比較画像をここに置く。画像は `scripts/eval/make_compare_sheet.py` で作成(各タイル左上にラベル)。

- 実行環境: RTX 5060 Ti、120 フレーム、1080×1920 出力、アバターは特記がなければ RAYNOS-chan
- リポジトリは非公開。アバターの利用規約と 3DPW のライセンス上、公開リポジトリにする場合はこのディレクトリを外すこと

## HMR(SAM 3D Body)の間引き

左から 入力 / 全フレーム / 2フレームに1回 / 3フレームに1回(現在の既定) / 4フレームに1回 / 差分(3と4)。目視では見分けがつかないため 3 を既定にした。

`courtyard_basketball_00_020.jpg`

![courtyard_basketball_00_020.jpg](2026-10-24/hmr_stride/courtyard_basketball_00_020.jpg)

`courtyard_basketball_00_046.jpg`

![courtyard_basketball_00_046.jpg](2026-10-24/hmr_stride/courtyard_basketball_00_046.jpg)

`courtyard_basketball_00_100.jpg`

![courtyard_basketball_00_100.jpg](2026-10-24/hmr_stride/courtyard_basketball_00_100.jpg)

`downtown_bar_00_020.jpg`

![downtown_bar_00_020.jpg](2026-10-24/hmr_stride/downtown_bar_00_020.jpg)

`downtown_bar_00_046.jpg`

![downtown_bar_00_046.jpg](2026-10-24/hmr_stride/downtown_bar_00_046.jpg)

`downtown_bar_00_100.jpg`

![downtown_bar_00_100.jpg](2026-10-24/hmr_stride/downtown_bar_00_100.jpg)

## 人物トラッキングを 1008 → 672 + Triton

左から 入力 / 1008 / 672+Triton(現在の既定) / 差分。アバター出力の画素差は平均約 1.1%。

`courtyard_basketball_00_020.jpg`

![courtyard_basketball_00_020.jpg](2026-10-24/tracker_672/courtyard_basketball_00_020.jpg)

`courtyard_basketball_00_060.jpg`

![courtyard_basketball_00_060.jpg](2026-10-24/tracker_672/courtyard_basketball_00_060.jpg)

`courtyard_basketball_00_100.jpg`

![courtyard_basketball_00_100.jpg](2026-10-24/tracker_672/courtyard_basketball_00_100.jpg)

`downtown_bar_00_020.jpg`

![downtown_bar_00_020.jpg](2026-10-24/tracker_672/downtown_bar_00_020.jpg)

`downtown_bar_00_060.jpg`

![downtown_bar_00_060.jpg](2026-10-24/tracker_672/downtown_bar_00_060.jpg)

`downtown_bar_00_100.jpg`

![downtown_bar_00_100.jpg](2026-10-24/tracker_672/downtown_bar_00_100.jpg)

## シーン深度を MoGe-2 → Depth Anything V2

左から 入力 / MoGe-2 / DA-V2(現在の既定) / 差分。downtown_bar の 32 フレーム目は、MoGe-2 で手前の台の上に描かれていたスカートが DA-V2 では正しく隠れている(改善)。

`courtyard_basketball_00_032.jpg`

![courtyard_basketball_00_032.jpg](2026-10-24/depth_dav2/courtyard_basketball_00_032.jpg)

`courtyard_basketball_00_060.jpg`

![courtyard_basketball_00_060.jpg](2026-10-24/depth_dav2/courtyard_basketball_00_060.jpg)

`courtyard_basketball_00_100.jpg`

![courtyard_basketball_00_100.jpg](2026-10-24/depth_dav2/courtyard_basketball_00_100.jpg)

`downtown_bar_00_032.jpg`

![downtown_bar_00_032.jpg](2026-10-24/depth_dav2/downtown_bar_00_032.jpg)

`downtown_bar_00_060.jpg`

![downtown_bar_00_060.jpg](2026-10-24/depth_dav2/downtown_bar_00_060.jpg)

`downtown_bar_00_100.jpg`

![downtown_bar_00_100.jpg](2026-10-24/depth_dav2/downtown_bar_00_100.jpg)

## 現在の人物除去の品質(調査用)

左から 入力 / 人物除去後の背景 / 最終出力(赤 = 人物除去で埋めた穴のうち、アバターに覆われず見えている部分)。穴の約半分が見えている。downtown_bar(固定カメラ)は補間(Telea)が灰色ににじみ、basketball は影が残る。

`courtyard_basketball_00_060.jpg`

![courtyard_basketball_00_060.jpg](2026-10-24/removal_current/courtyard_basketball_00_060.jpg)

`downtown_bar_00_060.jpg`

![downtown_bar_00_060.jpg](2026-10-24/removal_current/downtown_bar_00_060.jpg)

## 既知の不具合: kf_lite でスカートの端が誤って隠れる

左から 入力 / 毎フレーム SAM3(c2f) / kf_lite / 差分。つくよみちゃんでの実行(当時)。原因は調査中。

`courtyard_basketball_00_014.jpg`

![courtyard_basketball_00_014.jpg](2026-10-24/kf_lite_skirt_bug/courtyard_basketball_00_014.jpg)
