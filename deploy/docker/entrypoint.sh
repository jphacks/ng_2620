#!/usr/bin/env bash
# vvlog GPU サーバー(BYO 用)コンテナの起動スクリプト。deploy/docker/Dockerfile から /usr/local/bin/vvlog-entrypoint に入る。
#   1. VVLOG_API_TOKEN が無い／弱いなら openssl rand -hex 24 で生成し、起動時に 1 回だけ標準エラーへ出す
#   2. 重みが無ければ初回取得(HF_TOKEN は環境変数からだけ読み、ディスクに残さない)
#   3. VVLOG_* をそのままサーバーへ渡して起動(サーバーは 127.0.0.1 だけで待ち受ける。コンテナの外へは socat で中継)
# トークンの強さの判定は services/gpu_server/vvlog_server/main.py の weak_token_reason をそのまま呼ぶ(ここに複製しない)。
set -euo pipefail

SERVER_PY="${VVLOG_SERVER_PYTHON:-/opt/server/bin/python}"   # サーバー本体(torch 無し)の Python
PIPELINE_PY="${VVLOG_PYTHON:-/opt/pipeline/bin/python}"      # 推論パイプラインの Python
APP_DIR="${VVLOG_APP_DIR:-/app}"
CKPT_ROOT="${VVLOG_CKPT_ROOT:-/data/weights}"
LISTEN_PORT="${VVLOG_FORWARD_PORT:-8080}"                    # コンテナの外へ出す側のポート

log() { printf '[vvlog-entrypoint] %s\n' "$*" >&2; }

# 弱い理由(日本語)を標準出力へ。強ければ何も出さない。
weak_token_reason() {
  VVLOG_CHECK_TOKEN="$1" PYTHONPATH="${APP_DIR}/services/gpu_server${PYTHONPATH:+:$PYTHONPATH}" \
    "$SERVER_PY" -c 'import os; from vvlog_server.main import weak_token_reason as w; print(w(os.environ["VVLOG_CHECK_TOKEN"]) or "")'
}

# 使うトークンを決める。生成したときだけ GENERATED_TOKEN に入れる(表示は後で 1 回だけ)。
GENERATED_TOKEN=""
ensure_token() {
  local why="" r
  if [ -z "${VVLOG_API_TOKEN:-}" ]; then
    why="未設定"
  elif [ "${VVLOG_ALLOW_WEAK_TOKEN:-}" != "1" ]; then
    r="$(weak_token_reason "$VVLOG_API_TOKEN")"
    if [ -n "$r" ]; then why="弱い値のため拒否($r)"; fi
  fi
  if [ -n "$why" ]; then
    GENERATED_TOKEN="$(openssl rand -hex 24)"
    export VVLOG_API_TOKEN="$GENERATED_TOKEN"
    log "VVLOG_API_TOKEN が${why}。新しく生成しました(下に 1 回だけ表示します)。"
  fi
}

print_token_once() {
  [ -n "$GENERATED_TOKEN" ] || return 0
  cat >&2 <<BANNER
============================================================
 vvlog 接続トークン(この起動で 1 回だけ表示します。控えてください)
   ${GENERATED_TOKEN}
 アプリの接続設定の「トークン」に貼ります。
 再起動すると別の値になります。固定したいときは環境変数 VVLOG_API_TOKEN を指定してください。
============================================================
BANNER
  GENERATED_TOKEN=""
}

# 重み。揃っていれば何もしない(目印のファイルで判定。取り直すなら VVLOG_FORCE_FETCH=1)。
ensure_weights() {
  mkdir -p "$CKPT_ROOT"
  local marker="$CKPT_ROOT/.vvlog-fetched"
  if [ ! -f "$marker" ] || [ "${VVLOG_FORCE_FETCH:-}" = "1" ]; then
    if [ -z "${HF_TOKEN:-}" ] && [ -z "$(ls -A "$CKPT_ROOT" 2>/dev/null)" ]; then
      log "重みがありません。SAM3 / SAM-3D-Body は Hugging Face のゲート付きです。HF_TOKEN を環境変数で渡してください(アクセス許諾済みのアカウント)。"
      exit 1
    fi
    log "重みを取得します(合計約 7GB。初回だけ)。"
    "$PIPELINE_PY" "$APP_DIR/weights/fetch.py" --dest "$CKPT_ROOT" --write_config
    : > "$marker"
  else
    # 重みは取得済み。パイプラインの設定(コンテナ内)の向き先だけ毎回合わせる
    "$PIPELINE_PY" -c 'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); import fetch; fetch.write_config(Path(sys.argv[2]))' \
      "$APP_DIR/weights" "$CKPT_ROOT"
  fi
}

main() {
  ensure_token
  ensure_weights

  export VVLOG_API_TOKEN VVLOG_CKPT_ROOT="$CKPT_ROOT"
  export VVLOG_PYTHON="$PIPELINE_PY"
  export VVLOG_DATA_ROOT="${VVLOG_DATA_ROOT:-/data/work}"
  export VVLOG_PORT="${VVLOG_PORT:-8765}"
  mkdir -p "$VVLOG_DATA_ROOT"

  # HF のトークンはここまで。パイプライン(子プロセス)には引き継がない。
  unset HF_TOKEN HUGGING_FACE_HUB_TOKEN

  # サーバーは 127.0.0.1 だけで待ち受けるので、コンテナの外へは socat で中継する(外側は compose/クラウド側で絞る)
  if [ "${VVLOG_FORWARD:-1}" = "1" ]; then
    socat "TCP-LISTEN:${LISTEN_PORT},fork,reuseaddr" "TCP:127.0.0.1:${VVLOG_PORT}" &
  fi

  print_token_once
  cd "$APP_DIR/services/gpu_server"
  exec "$SERVER_PY" -m vvlog_server
}

# テストから source するときは実行しない
if [ "${VVLOG_ENTRYPOINT_SOURCE_ONLY:-}" != "1" ]; then
  main "$@"
fi
