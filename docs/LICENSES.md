# ライセンス

VirtualTwin が使うモデル・ライブラリ・データのライセンス。README の「ライセンス」の詳細。

このリポジトリのコードは MIT（[LICENSE](../LICENSE)）。推論パイプラインの土台の [SAM-Body4D](https://github.com/gaomingqi/sam-body4d) も MIT。
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
遮蔽の補完（[`pipeline/configs/body4d.yaml`](../pipeline/configs/body4d.yaml) の `completion`、既定で無効）だけが使う。上流の `pipeline/scripts/setup.py` はこれらも取得するので、商用で使うときは `weights/fetch.py` を使う。
EdgeTAM（Apache-2.0）は実験用の選択肢で、既定では使わない。

### ライブラリ

| 部分 | 確認した範囲 | ライセンス |
|---|---|---|
| 画面（`apps/desktop`） | 実行時に入る npm パッケージ 53 個 | MIT 46、MIT OR Apache-2.0 5、ISC 1（lucide-react）、MIT AND Zlib 1（pako）。React・Radix UI・TanStack Query・Zustand・react-router・Tailwind CSS・Vite は MIT、TypeScript は Apache-2.0 |
| GPU サーバー（`services/gpu_server`） | 直接の依存 | FastAPI・pydantic・PyYAML は MIT、uvicorn・sse-starlette は BSD-3-Clause、python-multipart は Apache-2.0、NumPy は BSD-3-Clause ほか、Pillow は MIT-CMU、send2trash は BSD-3-Clause、pystray（Windows のトレイアイコン）は LGPL-3.0 |
| 推論パイプライン（`pipeline`） | 直接の依存 | PyTorch は BSD 系（同梱の部品に Apache-2.0 などを含む）、torchvision は BSD、detectron2・diffusers・transformers・accelerate・OpenCV・decord・fvcore・gradio・pytorch-lightning は Apache-2.0、pyrender・moderngl・einops・triton は MIT、imageio・imageio-ffmpeg は BSD-2-Clause、matplotlib は PSF 系、MoGe のコードは MIT |
| 外部コマンド | ffmpeg / ffprobe、uv | Windows の配布物に同梱：ffmpeg は BtbN の LGPL ビルド（LGPL-3.0-or-later）、uv は MIT OR Apache-2.0。同梱物の一覧は [NOTICE](../NOTICE) |

### データとアバター

- 比較表の計測に使った [3DPW](https://virtualhumans.mpi-inf.mpg.de/3DPW/)（downtown_bar_00）は、**非商用の学術研究に限る**データセット。このリポジトリには 3DPW の映像を含めていない。
  出典：T. von Marcard, R. Henschel, M. J. Black, B. Rosenhahn, G. Pons-Moll. "Recovering Accurate 3D Human Pose in the Wild Using IMUs and a Moving Camera", ECCV 2018.
- VRM アバターは、各アバターのライセンス（VRM のメタ情報）に従う。リポジトリには含めない。
- スライド（`docs/slide`）の画像のうち、配信・ライブの画像は各社のプレスリリース（PR TIMES）から引用し、スライドに出典を記載している。
