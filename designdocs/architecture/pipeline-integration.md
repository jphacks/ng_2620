# パイプライン統合（既存スクリプト → サーバーのステージ）

レーン C の作業の根拠。既存パイプラインは `vlog_JPHack2026@image_analytics`（2026-10-24 時点 `87bffa6`）。
移植先は実装リポジトリの `pipeline/`（[ADR-0008](../adr/0008-implementation-repo-and-model-placement.md)）。

## 1. 現状のパイプライン

### 1.1 全体の時間（`87bffa6`、デモ用の RTX 5060 Ti、3DPW 120 フレーム、`scripts/eval/e2e.py`）

| 素材 | HMR（`--hmr_stride 1` → `3`） | 全体（`--hmr_stride 1` → `3`） |
|---|---|---|
| downtown_bar | 16.3 → 8.6 秒 | 94.2 → **86.2 秒** |
| courtyard_basketball | 16.3 → 10.9 秒 | 91.9 → **87.1 秒** |

約 **0.72 秒/フレーム**（10 秒 = 300 フレームで約 3.6 分。プロキシ 540x960 では 1〜2 割増の見込み）。
`--hmr_stride 3`（既定）は SAM 3D Body をキーフレーム（0, 3, 6, …、最終フレーム、前後のキーフレームと人物の組が違うフレーム）だけで回し、間のフレームは頂点・カメラ並進・関節を線形補間、回転を slerp で埋める（`utils/hmr_stride.py`）。アバター出力は毎フレーム HMR と目視で区別できない。関節の差（毎フレーム HMR 比）は、映り続ける人物で 5〜9 mm、動きの大きい人物で 11 mm（K=3）、画面に入ってくる人物で 28〜64 mm。

### 1.2 内訳（`21f65fe`＝`--hmr_stride` 導入前、L4、3DPW 300 フレーム 480x854）

`87bffa6` では SAM-3D-Body が約 3〜5 割減り、fusion の未使用の描画（約 25 ms/フレーム）と人物ごとのオーバーレイ保存（約 45 ms/フレーム）が無くなっている。他の段の比率の目安として残す。

| プロセス | 中身 | 時間（ms/フレーム） |
|---|---|---|
| `scripts/offline_app_lite.py --input_video <proxy frames> --output_dir <pout>` | 検出（ViTDet）63*、SAM3 追跡（672、Triton）169、FoV 7、SAM-3D-Body 196 | 527（プロセス全体） |
| `scripts/avatar_app.py --pipeline_out <pout> --out <av> --gpu_removal [--hq <full frames>]` | fusion v2 792（前処理 193、GPU 353（深度 DA-V2 84）、CPU 243）、除去 151（スレッドで並走）、当てはめ＋描画 96、合成 6 | 931 |
| 合計 | | 1459 |

\* 初回実行のためモデル読み込みを含む。2 回目は 20 ms 程度。

`pipeline_out/` の中身：`images/`（プロキシフレーム）、`masks/`（追跡マスク）、`mesh_4d_individual/`、`focal_4d_individual/`、`skeleton_4d_individual/<pid>/`、`4d_*.mp4`（メッシュのオーバーレイ）。人物ごとのオーバーレイ `rendered_frames_individual/` は既定で書かない（`--individual_overlays` で書く）。
fusion v2 の中間は `<av>/occlusion/_r3cache/<name>/`（`mesh.npz`、`moge.npz`（深度）、`cands.npz`（候補マスク）、`person.npz`）。

## 2. ステージへの割り当て

| ステージ | 呼ぶもの | 改修（★ = P0 必須） |
|---|---|---|
| `ingest` | `ffprobe` / `ffmpeg`（サーバーの `media_ffmpeg.py`） | ★ 回転適用・CFR 化（30fps 以下）・制約検証・`meta.json`。フル解像度フレーム `full/` とプロキシ `proxy/`（整数分の 1）を JPEG で書き出す。音声を `audio.m4a` に分離 |
| `track` + `mesh` | `offline_app_lite.py --input_video proxy/ --output_dir pout/`（`--hmr_stride` は既定 3。`VVLOG_HMR_STRIDE` で上書き） | ★ `max_targets` を `VVLOG_MAX_TARGETS`（既定 4）で上書き（`configs/body4d.yaml` の `runtime.max_targets`）。★ 追跡完了時点で `persons.json`・サムネイル・`track_masks` を公開する（下記 §3.1） |
| `occlusion` | `scripts/occlusion/run_fusion_v2.py --pipeline_out pout/ --out occ/` | ★ 遮蔽物ユニットの連結と `units/`・`units.json` の書き出し（§3.2）。★ `ev`・`dec` を pickle で保存し、後段で再計算しない（§3.4） |
| `removal` + `avatar` + `layers` | `avatar_app.py --pipeline_out pout/ --out av/ --gpu_removal --targets ... --vrm_map ... --export_layers` | ★ 置換対象の人物の指定と人物ごとの VRM（§3.3）。★ プロキシ解像度のレイヤー書き出し（§3.5） |
| `composite` + `encode` | `avatar_app.py ... --hq full/ --overrides overrides.json` → `ffmpeg` で音声を多重化 | ★ 手修正の適用（§3.6）。★ 音声の再多重化（CRF 18、yuv420p、AAC） |

**進捗の取り方**：各スクリプトの標準出力から `[STAGE][name] time=...`・tqdm の `n/total`・`[fusion_v2] ... <frame>` 行を正規表現で読み、`progress` イベントに変換する（`adapters/pipeline_subprocess.py`）。読めない行は無視し、ステージの開始・終了だけでも成立させる。`mesh` は `--hmr_stride` でキーフレームを先に処理し、間のフレームを後から埋めるので、tqdm の総数はフレーム数と一致しない。ステージの完了は `[STAGE][hmr_4d]` 行で判定する。

## 3. 必要な改修（詳細）

### 3.1 追跡完了時点での人物一覧（★）

`offline_app_lite.py` は検出 → 追跡 → メッシュを 1 プロセスで行う。追跡（`sam3_propagate`）が終わった時点で `masks/` が揃う。

- 方式：`--emit_persons <dir>` を追加し、`sam3_propagate` の直後に `persons.json` とサムネイル（各 pid の可視面積が最大のフレームから bbox を切り出し 256px 正方）と `masks` の zip を書き、標準出力に `[ARTIFACT] persons <path>` を出す。サーバーはこの行を見て `artifact_ready` を送る。
- 代替（改修できない場合）：サブプロセスの `[STAGE][sam3_propagate]` 行を見て、サーバー側で `masks/` から同じものを作る。

### 3.2 遮蔽物ユニット（★、[ADR-0006](../adr/0006-occlusion-overrides-model.md)）

fusion v2 の判定はトラック単位：`dec[(tr, pid, part, "o")] = (front, why, z, R, h)`（物体）、`dec[(-q, pid, part, "p", i)]`（人物同士、フレームごと）。
`ev["units"]` の各要素はフレーム `i` の候補（`ci` = `cands.npz` のマスク番号、`tr` = トラック、`pid`、`part`）。

1. **トラックの連結**：トラックは短い（キーフレーム間隔 10 フレーム程度で切れうる）。トラック `a` の最終フレームのマスクと、トラック `b` の最初のフレームのマスク（`b` が `a` の終了後 3 フレーム以内に始まる）の IoU ≥ 0.5 なら同じユニットにする（union-find）。
2. **ユニットのラベル画像**：フレームごとに、ユニットに属する候補マスクを面積の大きい順に塗り、小さいものが上書きする（重なりは小さい物体を優先）。16bit PNG。
3. **人物ごとの自動判定**：ユニットに属するトラックの `dec[(tr, pid, part, "o")]` を部位をまたいで集約する。1 つでも `front` なら `front`（`why` はその部位のもの）、判定キーが無ければ `none`。`confidence` は `min(1, |z| / 3)`（z が NaN なら `R` から）を目安とし、後で調整してよい。
4. **ターゲット以外の人物**：`max_targets` に入ったがキャストで選ばれなかった人物は、実物として映像に残る。fusion v2 の `person.npz` の可視マスクをユニット（`label: "person"`）として出し、判定は `dec[(-q, pid, ...)]` から作る（P0 は表示と手修正の対象になればよい）。

### 3.3 キャスト（置換対象と VRM）（★）

`avatar_app.py` は現在、追跡したすべての人物を同じ VRM で置換する。

- `--targets 1,3`：置換する pid。指定外の人物は除去も置換もしない（実物のまま残る）。
- `--vrm_map 1=/path/a.vrm,3=/path/b.vrm`：人物ごとの VRM。
- 置換しない人物が残る場合、`AvatarHider(people=False)` は「他のターゲットの実物は消えている」前提で人物同士の規則を外している。**置換しない人物は実物が残るので、その人物の手前判定（`people=True` の分岐）を、置換しない人物 q についてだけ有効にする**。

### 3.4 fusion の中間の再利用（★）

`avatar_app.py` は内部で `run_fusion(render=False)` を呼び直す（`_r3cache` があると GPU 段は飛ぶが CPU 段は再計算。L4・300 フレームで約 73 秒）。
`occlusion` ステージで `ev`・`dec` を `occ/fusion_state.pkl` に保存し、`avatar_app.py --fusion_state <pkl>` で読み込んで `run_fusion()` を飛ばす。
（`render` と `export` の両方で効く。書き出しの待ち時間が 1 分以上短くなる）

### 3.5 レイヤーの書き出し（★、[ADR-0005](../adr/0005-preview-composite-client-export-server.md)）

`avatar_app.py --export_layers <dir>`：描画ループの中で、合成の直前に次を書く（プロキシ解像度、`--hq` なしのとき）。

- `clean/{f}.webp`：除去後の背景（合成の `base`）。
- `avatar/{pid}/{f}.webp`：人物 pid のアバター RGBA（SSAA 後、ストレートアルファ）。
- `hide/{pid}/{f}.png`：`AvatarHider.smooth()` 後の隠れマスク（0/255）。
- `layers.json` の `order[f]`：その人物のアバター深度の中央値で奥→手前に並べた pid。

書き込みは既存の `Sink`（順序付きワーカースレッド）に載せ、描画ループを止めない。

### 3.6 手修正の適用（書き出し、★）

`avatar_app.py --overrides overrides.json`：

- ユニットの手修正 `{personId, unitId, range, mode}` を、`units.json` の `tracks` でトラック集合に展開する。
- `hide_frame()` の判定参照 `d = dec.get((u["tr"], u["pid"], u["part"], "o"))` を、フレーム `i` が `range` に入るときだけ上書きする：
  `front` → `(True, "OVR")`（`PIXCHK` の深度確認は `why == "D+"` のときだけなので、手修正の領域は丸ごと隠れる）、`behind` → `(False, "OVR")`。
  判定キーが存在しないトラック（`OWNMAX` などで判定対象外になったもの）も、`front` の手修正があれば上書き辞書から返す。
- ブラシ（P1）：`AvatarHider.smooth()` の後に、そのフレームのブラシマスクを `front` なら OR、`behind` なら AND NOT で適用する。

### 3.7 依存とピン（Colab / Linux で確認済みの組み合わせ）

`transformers~=4.57.3`、`diffusers==0.29.1`、`roma~=1.5.4`、`huggingface-hub<1.0`、`eva-decord`（Python 3.13）、detectron2 `a1ce2f9`、`moderngl`（`MODERNGL_BACKEND=egl`）、
環境変数 `USE_TF=0 USE_JAX=0 USE_FLAX=0 PYOPENGL_PLATFORM=egl`（pyrender の EGL が TF/JAX の読み込みで落ちるため）。
Windows（RTX 5060 Ti）では既存の `.venv` 構成。Triton 注意機構は Linux の torch 同梱の triton、Windows は `triton-windows`。

## 4. 移植の手順（レーン C、Day 1 午前）

1. `pipeline/` にコピー（`scripts/offline_app_lite.py`, `scripts/avatar_app.py`, `scripts/avatar/`, `scripts/occlusion/`（`fusion_v2/` を含む）, `scripts/hmr_fast.py`, `scripts/setup.py`, `utils/`（`hmr_stride.py` を含む）, `configs/`）。`models/` は上流コードと LICENSE。
2. `pipeline/UPSTREAM.md` に移植元の commit を書く。
3. `scripts/eval/e2e.py` も移し、GPU 機で 1 本通す（10 秒のサンプル）。ここで得た `pipeline_out/` と `avatar/` から **フィクスチャ（F14）を作る**。
4. §3 の改修を ★ から順に。各改修の後に e2e を通し、出力動画を前回と目視比較する。
