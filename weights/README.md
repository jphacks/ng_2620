# weights

重みはリポジトリに置かない（[ADR-0008](../designdocs/adr/0008-implementation-repo-and-model-placement.md)）。GPU サーバーの機械で取得する。

```sh
pip install huggingface_hub
HF_TOKEN=hf_... python weights/fetch.py --dest /path/to/ckpt --write_config
```

- 取得元とライセンスは `manifest.json`。SAM3・SAM-3D-Body は Hugging Face のゲート付き（各自でアクセス許諾）。
- `--write_config` は `pipeline/configs/body4d.yaml` の `ckpt_root`・`detector_path` を書き換える（機械ごとの設定）。
- 合計約 7GB。Depth Anything V2 Metric Indoor Small は Hugging Face のキャッシュ（`HF_HOME`）に入る。
