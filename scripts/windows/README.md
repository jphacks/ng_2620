# scripts/windows

Windows + NVIDIA GPU 機で手を動かして確かめるためのもの（CI では確かめられない部分）。

## verify-env-b.ps1（ISSUE #30）

ドライバだけ入れた PC で、環境 B（`pipeline/` の torch/CUDA 環境）が手作業ゼロで作れるかを確かめる。
MSVC・CUDA Toolkit は使わない。uv と NVIDIA ドライバだけで完結するのが合格基準。

```powershell
# リポジトリのルートで。uv が PATH に要る
powershell -ExecutionPolicy Bypass -File scripts\windows\verify-env-b.ps1 -Python 3.12 -TorchIndex cu128
powershell -ExecutionPolicy Bypass -File scripts\windows\verify-env-b.ps1 -Python 3.13 -TorchIndex cu128 `
  -Detectron2Wheel <ビルド済み wheel のパスか URL>   # #51 でできたもの
```

- 毎回 `%TEMP%\vvlog-verify-env-b` に新しい venv を作る（既存の `.venv` は触らない）。
- 出力：`verify-env-b-report.md`（ISSUE #30 にそのまま貼る）と `.json`。手順ごとの OK/NG・所要秒・venv のディスク量、
  import の通し、`torch.cuda` の情報（デバイス名・sm・VRAM・実際の GPU 演算）が入る。
- 依存は `--only-binary :all:` で 1 つずつ入れて、**Windows 用 wheel が無くビルドが要るもの**を洗い出す（NG になる）。
- 比べたい組み合わせ：Python 3.12 / 3.13 × `cu128`（Blackwell は 12.8 以上）。ドライバ版との対応は `nvidia-smi` の行から読む。
- MSVC/nvcc があると『ドライバのみ』の検証にならないので、レポートの気づきに出る。無い PC（クリーンな Windows）でも 1 回通す。

この後の重み取得（`weights/fetch.py`）とパイプライン通し（`services/gpu_server/scripts/smoke_e2e.py` 相当）は時間がかかるので別手順：
`services/gpu_server/README.md` §3・§4。かかった時間と VRAM（`nvidia-smi` の最大値）を ISSUE に追記する。

## probe_env_b.py

環境 B の Python で動かす確認本体（単体でも実行できる）：`<環境 B の python> scripts/windows/probe_env_b.py`。
JSON を 1 つ出し、全部 import できて GPU で計算できれば終了コード 0。
