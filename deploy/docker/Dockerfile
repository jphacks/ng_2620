# syntax=docker/dockerfile:1
# vvlog GPU サーバー(BYO 用・CUDA 対応)。ADR-0015 / Issue #43。使い方: designdocs/guides/byo-gpu-server.md
#
#   ビルド(リポジトリのルートで):
#     docker build -f deploy/docker/Dockerfile -t vvlog-gpu-server .
#
# 【暫定】detectron2 のビルド方針は #30 の結論待ち、ビルド済み wheel は #51 の成果物待ち。
#   - wheel が出たら: --build-arg DETECTRON2_MODE=wheel --build-arg DETECTRON2_WHEEL_URL=<URL>
#   - まだなら(既定): DETECTRON2_MODE=source で、下の stage d2-source が git からビルドする(暫定。時間がかかる)
# 【暫定】torch の版と CUDA は pipeline/README.md の例(2.7.1)を CUDA 12.8 向け index に合わせた値。#30 で固定したら更新する。
# Linux 用 wheel は Windows 用とは別ビルドになりうる(#51)。
# 重みはイメージに入れない(ADR-0008)。初回起動時に /data/weights へ取得する(deploy/docker/entrypoint.sh)。
# 【未検証】このファイルの実ビルド・起動・GPU 動作は、GPU のある環境でまだ確かめていない。

ARG CUDA_VERSION=12.8.1
ARG UBUNTU_VERSION=24.04
ARG DETECTRON2_MODE=source
ARG DETECTRON2_WHEEL_URL=""
ARG DETECTRON2_REF=a1ce2f9
ARG TORCH_SPEC="torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1"
ARG TORCH_INDEX=https://download.pytorch.org/whl/cu128
# detectron2 をソースからビルドするときの GPU 世代(A100=8.0, RTX 30xx=8.6, L4/RTX 40xx=8.9, RTX 50xx=12.0)
ARG TORCH_CUDA_ARCH_LIST="8.0;8.6;8.9;12.0"

# ---------------------------------------------------------------- torch 入りのビルド環境
FROM nvidia/cuda:${CUDA_VERSION}-cudnn-devel-ubuntu${UBUNTU_VERSION} AS build-base
ARG TORCH_SPEC
ARG TORCH_INDEX
ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-dev build-essential git ca-certificates curl ninja-build \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/pipeline \
    && /opt/pipeline/bin/pip install --upgrade pip setuptools wheel \
    && /opt/pipeline/bin/pip install ${TORCH_SPEC} --index-url ${TORCH_INDEX}

# ---------------------------------------------------------------- detectron2: ソースから(暫定。#30 / #51 の結論待ち)
FROM build-base AS d2-source
ARG DETECTRON2_REF
ARG TORCH_CUDA_ARCH_LIST
ENV TORCH_CUDA_ARCH_LIST=${TORCH_CUDA_ARCH_LIST} FORCE_CUDA=1
RUN /opt/pipeline/bin/pip wheel --no-build-isolation --no-deps -w /wheels \
      "git+https://github.com/facebookresearch/detectron2.git@${DETECTRON2_REF}"

# ---------------------------------------------------------------- detectron2: ビルド済み wheel(#51)
FROM build-base AS d2-wheel
ARG DETECTRON2_WHEEL_URL
RUN test -n "${DETECTRON2_WHEEL_URL}" || { echo "DETECTRON2_MODE=wheel には --build-arg DETECTRON2_WHEEL_URL=<URL> が要ります" >&2; exit 1; } \
    && mkdir -p /wheels && cd /wheels && curl -fsSL -O "${DETECTRON2_WHEEL_URL}"

FROM d2-${DETECTRON2_MODE} AS d2

# ---------------------------------------------------------------- 環境 A(推論パイプライン)と B(サーバー本体)
FROM build-base AS install
COPY --from=d2 /wheels /wheels
WORKDIR /app
COPY pipeline ./pipeline
COPY services/gpu_server ./services/gpu_server
COPY weights ./weights
# 環境 A。services/gpu_server/DEPLOY.md §1 の確認済みピンに合わせる(decord は eva-decord、utils3d_moge は外して upstream の utils3d)
RUN /opt/pipeline/bin/pip install /wheels/*.whl --no-deps \
    && /opt/pipeline/bin/pip install -e pipeline/models/sam3 \
    && /opt/pipeline/bin/pip install -e pipeline \
    && (/opt/pipeline/bin/pip uninstall -y decord utils3d_moge || true) \
    && /opt/pipeline/bin/pip install eva-decord moderngl "huggingface-hub<1.0" utils3d
# 環境 B(torch を入れない)
RUN python3 -m venv /opt/server \
    && /opt/server/bin/pip install --upgrade pip \
    && /opt/server/bin/pip install -e services/gpu_server

# ---------------------------------------------------------------- 実行用(devel を持ち込まない)
FROM nvidia/cuda:${CUDA_VERSION}-cudnn-runtime-ubuntu${UBUNTU_VERSION} AS runtime
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 ffmpeg libgl1 libglib2.0-0 libegl1 socat tini openssl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=install /opt/pipeline /opt/pipeline
COPY --from=install /opt/server /opt/server
COPY --from=install /app /app
COPY deploy/docker/entrypoint.sh /usr/local/bin/vvlog-entrypoint
RUN chmod +x /usr/local/bin/vvlog-entrypoint

# 重み・ジョブはボリューム(/data)に置く。コンテナを作り直しても消えない
VOLUME ["/data"]
ENV VVLOG_CKPT_ROOT=/data/weights \
    VVLOG_DATA_ROOT=/data/work \
    HF_HOME=/data/hf-cache \
    VVLOG_PORT=8765 \
    VVLOG_FORWARD_PORT=8080
# 8080 はコンテナの外へ出す側(サーバー本体は 127.0.0.1:8765 だけ)。インターネットへ直接は公開しない(ガイド参照)
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/vvlog-entrypoint"]
