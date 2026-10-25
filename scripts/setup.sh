#!/usr/bin/env bash
# vvlog の開発環境の初期セットアップ（macOS 向け。Linux でも使える。Windows は対象外：SETUP.md の手動手順）
#
#   scripts/setup.sh              足りないものを確かめて入れる（ネットのスクリプトの実行やシステムへの導入は y/N で聞く）
#   scripts/setup.sh --yes        確認を省いて入れる
#   scripts/setup.sh --check      何も入れずに、ツールの有無と版を一覧にする（足りなければ終了コード 1）
#   scripts/setup.sh --app-only   画面（Node / pnpm）だけ。Python・uv・ffmpeg・サーバーの venv を扱わない
#
# 入れるもの（SETUP.md「前提のツール」）：Node（.nvmrc の版。nvm か fnm で）、pnpm（package.json の packageManager の版）、
# Python 3.11 以上（uv があれば uv が用意する）、ffmpeg、
# services/gpu_server/.venv（サーバーとモックの環境 A）、最後に pnpm install。
# pipeline/ の環境 B（torch・CUDA）は GPU 機だけなので扱わない（services/gpu_server/README.md §2）。
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

MODE=install
ASSUME_YES=0
APP_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --check) MODE=check ;;
    --yes | -y) ASSUME_YES=1 ;;
    --app-only) APP_ONLY=1 ;;
    -h | --help)
      awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      echo "不明なオプション: $arg（--check / --yes / --app-only / --help）" >&2
      exit 2
      ;;
  esac
done

OS=$(uname -s)
case "$OS" in
  MINGW* | MSYS* | CYGWIN*)
    echo "Windows はこのスクリプトの対象外。SETUP.md の手動手順（Windows の行）で入れる。" >&2
    exit 1
    ;;
esac

# ---- 版の決まり（リポジトリのファイルから読む）
NODE_WANT=$(tr -d '[:space:]' <.nvmrc)                                                  # 例 26
PNPM_WANT=$(sed -n 's/.*"packageManager": *"pnpm@\([^"]*\)".*/\1/p' package.json)       # 例 10.34.8
PYTHON_MIN=3.11.0 # services/gpu_server/pyproject.toml の requires-python
FFMPEG_MIN=6.0.0
SERVER_DIR=services/gpu_server

# ---- 表示
if [ -t 1 ]; then
  C_OK=$'\033[32m' C_NG=$'\033[31m' C_WARN=$'\033[33m' C_DIM=$'\033[2m' C_OFF=$'\033[0m'
else
  C_OK='' C_NG='' C_WARN='' C_DIM='' C_OFF=''
fi
FAILED=0
row() { # row <OK|NG|WARN|SKIP> <名前> <版・状態> [ヒント]
  local mark color
  case "$1" in
    OK) mark=OK color=$C_OK ;;
    NG) mark=NG color=$C_NG FAILED=1 ;;
    WARN) mark=WARN color=$C_WARN ;;
    *) mark=$1 color=$C_DIM ;;
  esac
  printf '  %s%-4s%s %-14s %s' "$color" "$mark" "$C_OFF" "$2" "$3"
  if [ -n "${4:-}" ]; then printf '  %s→ %s%s' "$C_DIM" "$4" "$C_OFF"; fi
  printf '\n'
}
step() { printf '\n==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die() {
  printf '\n%s中断%s: %s\n' "$C_NG" "$C_OFF" "$*" >&2
  exit 1
}

# confirm "<何をするか>"：--yes なら即 yes。端末が無ければ no
confirm() {
  if [ "$ASSUME_YES" = 1 ]; then return 0; fi
  if [ ! -t 0 ]; then
    note "（確認できない環境なので実行しない。--yes で実行する）"
    return 1
  fi
  local ans
  read -r -p "    $1 [y/N] " ans
  case "$ans" in y | Y | yes | YES) return 0 ;; *) return 1 ;; esac
}

# ---- 版の比較（bash 3.2 でも動くように数値で比べる）
# ver "<コマンドの出力>"：最初に出てくる 1.2.3 の形を取り出す（パイプを使わないので pipefail で落ちない）
ver() { awk 'match($0, /[0-9]+(\.[0-9]+)*/) { print substr($0, RSTART, RLENGTH); exit }' <<<"$1"; }
ver_ge() { # ver_ge <a> <b>：a >= b
  local a b i x y
  IFS=. read -r -a a <<<"$1"
  IFS=. read -r -a b <<<"$2"
  for i in 0 1 2; do
    x=${a[$i]:-0} y=${b[$i]:-0}
    if ((10#$x > 10#$y)); then return 0; fi
    if ((10#$x < 10#$y)); then return 1; fi
  done
  return 0
}
has() { command -v "$1" >/dev/null 2>&1; }

# ---- 版管理ツールを、このスクリプトのプロセスで使えるようにする
load_nvm() {
  if has nvm; then return 0; fi
  local d=${NVM_DIR:-$HOME/.nvm}
  if [ -s "$d/nvm.sh" ]; then
    export NVM_DIR=$d
    set +eu # nvm.sh は set -eu を想定していない
    # shellcheck disable=SC1091
    . "$d/nvm.sh" --no-use
    set -eu
    has nvm
  else
    return 1
  fi
}
# nvm の関数は set -eu を想定していないので、呼ぶ間だけ外す
nvm_q() {
  local rc=0
  set +eu
  nvm "$@" || rc=$?
  set -eu
  return "$rc"
}
node_version() { if has node; then ver "$(node --version)"; fi; }
pnpm_version() { if has pnpm; then ver "$(pnpm --version 2>/dev/null)"; fi; }
python_version() { if has python3; then ver "$(python3 --version 2>&1)"; fi; }
ffmpeg_version() { if has ffmpeg; then ver "$(ffmpeg -version 2>/dev/null)"; fi; }

# =====================================================================
# --check：一覧だけ
# =====================================================================
check_all() {
  printf 'vvlog の開発環境（%s、%s）\n\n' "$OS" "$ROOT"
  printf '  %s\n' "画面（apps/desktop）"

  local v
  v=$(node_version)
  if [ -z "$v" ]; then
    row NG node "なし" "make setup（nvm か fnm で Node $NODE_WANT を入れる）"
  elif [ "${v%%.*}" = "$NODE_WANT" ]; then
    row OK node "v$v"
  else
    local hint="nvm use（.nvmrc を読む）"
    if load_nvm; then
      local inst
      inst=$(nvm_q version "$NODE_WANT" 2>/dev/null || true)
      if [ "$inst" = "N/A" ] || [ -z "$inst" ]; then hint="nvm install && nvm use"; else hint="nvm use（$inst は入っている）"; fi
    elif has fnm; then
      hint="fnm use --install-if-missing"
    fi
    row NG node "v$v（.nvmrc は $NODE_WANT）" "$hint"
  fi

  if has nvm || load_nvm; then
    row OK nvm "$(nvm_q --version 2>/dev/null)"
  elif has fnm; then
    row OK fnm "$(ver "$(fnm --version)")"
  else
    row WARN "nvm / fnm" "なし" "Node の版を切り替えられない。nvm か fnm を入れる"
  fi

  v=$(pnpm_version)
  if [ -z "$v" ]; then
    row NG pnpm "なし" "npm i -g pnpm@$PNPM_WANT"
  elif [ "$v" = "$PNPM_WANT" ]; then
    row OK pnpm "$v"
  elif ver_ge "$v" 10.0.0; then
    row WARN pnpm "$v（packageManager は $PNPM_WANT）" "pnpm 10 以上なら packageManager の版に切り替わるはず"
  else
    row NG pnpm "$v（packageManager は $PNPM_WANT）" "npm i -g pnpm@$PNPM_WANT"
  fi

  if [ -d node_modules ] && [ -d apps/desktop/node_modules ]; then
    row OK node_modules "あり"
  else
    row NG node_modules "なし" "make install"
  fi

  printf '\n  %s\n' "サーバー・モック（services/）"
  if [ "$APP_ONLY" = 1 ]; then
    row SKIP "(--app-only)" "Python・uv・ffmpeg は確かめない"
  else
    local uv_ok=0
    if has uv; then
      uv_ok=1
      row OK uv "$(ver "$(uv --version)")"
    else
      row WARN uv "なし" "推奨。無ければ python3 -m venv で作る"
    fi

    v=$(python_version)
    if [ -n "$v" ] && ver_ge "$v" "$PYTHON_MIN"; then
      row OK python3 "$v"
    elif [ "$uv_ok" = 1 ]; then
      row OK python3 "${v:-なし}（uv が ${PYTHON_MIN%.0} 以上を用意する）"
    else
      row NG python3 "${v:-なし}（${PYTHON_MIN%.0} 以上が要る）" "brew install python@3.11 か uv を入れる"
    fi

    v=$(ffmpeg_version)
    if [ -z "$v" ] || ! has ffprobe; then
      row NG ffmpeg "なし（ffprobe も要る）" "brew install ffmpeg（Ubuntu: sudo apt install ffmpeg）"
    elif ver_ge "$v" "$FFMPEG_MIN"; then
      row OK ffmpeg "$v"
    else
      row NG ffmpeg "$v（${FFMPEG_MIN%.0.0} 以上が要る）" "brew upgrade ffmpeg"
    fi

    if [ -x "$SERVER_DIR/.venv/bin/python" ] && "$SERVER_DIR/.venv/bin/python" -c "import fastapi, sse_starlette, numpy, PIL" 2>/dev/null; then
      row OK "server .venv" "$SERVER_DIR/.venv"
    else
      row WARN "server .venv" "なし" "make setup（pnpm mock の初回にも作られる）"
    fi
    row SKIP "pipeline" "環境 B は GPU 機だけ（services/gpu_server/README.md §2）"
  fi

  if [ "$FAILED" = 1 ]; then
    printf '\n%s足りないものがある%s（NG の行）。make setup で入れられる。\n' "$C_NG" "$C_OFF"
    return 1
  fi
  printf '\n%sそろっている%s。\n' "$C_OK" "$C_OFF"
}

# =====================================================================
# インストール
# =====================================================================
NODE_SWITCHED=0

setup_node() {
  step "Node $NODE_WANT（.nvmrc）"
  local v
  v=$(node_version)
  if [ -n "$v" ] && [ "${v%%.*}" = "$NODE_WANT" ]; then
    note "v$v（そのまま使う）"
    return 0
  fi
  if load_nvm; then
    note "nvm で入れて使う（このスクリプトの中だけ。シェルの既定は変えない）"
    local inst
    inst=$(nvm_q version "$NODE_WANT" 2>/dev/null || true)
    if [ -z "$inst" ] || [ "$inst" = "N/A" ]; then
      note "nvm install $NODE_WANT（Node のダウンロード）"
      nvm_q install "$NODE_WANT" >/dev/null || die "nvm install $NODE_WANT に失敗した"
    fi
    nvm_q use "$NODE_WANT" >/dev/null || die "nvm use $NODE_WANT に失敗した"
  elif has fnm; then
    note "fnm で入れて使う（このスクリプトの中だけ）"
    eval "$(fnm env --shell bash)"
    fnm use --install-if-missing "$NODE_WANT"
  else
    die "Node $NODE_WANT が無く、nvm も fnm も無い。nvm（https://github.com/nvm-sh/nvm）か fnm を入れるか、brew install node@$NODE_WANT してからもう一度。"
  fi
  NODE_SWITCHED=1
  note "v$(node_version)"
}

setup_pnpm() {
  step "pnpm $PNPM_WANT（package.json の packageManager）"
  local v
  v=$(pnpm_version)
  if [ "$v" = "$PNPM_WANT" ]; then
    note "$v（そのまま使う）"
    return 0
  fi
  note "${v:+今は $v。}npm i -g pnpm@$PNPM_WANT（いま使っている Node の global に入る）"
  npm install -g "pnpm@$PNPM_WANT" >/dev/null
  hash -r
  note "$(pnpm_version)"
}

UV_OK=0
setup_uv() {
  if has uv; then
    UV_OK=1
    return 0
  fi
  local v
  v=$(python_version)
  if [ -n "$v" ] && ver_ge "$v" "$PYTHON_MIN"; then return 0; fi
  step "uv（python3 が ${v:-無い}。${PYTHON_MIN%.0} 以上の Python を uv に用意させる）"
  if [ "$OS" = Darwin ] && has brew; then
    if confirm "brew install uv を実行する？"; then
      brew install uv
      UV_OK=1
      return 0
    fi
  elif confirm "curl -LsSf https://astral.sh/uv/install.sh | sh（ネットのスクリプト）を実行して uv を入れる？"; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    has uv && UV_OK=1 && return 0
  fi
  die "Python ${PYTHON_MIN%.0} 以上か uv が要る（brew install python@3.11 / brew install uv）。--app-only ならアプリだけ用意する。"
}

setup_ffmpeg() {
  step "ffmpeg / ffprobe（$FFMPEG_MIN 以上。モック・サーバーが動画の検証に使う）"
  local v
  v=$(ffmpeg_version)
  if [ -n "$v" ] && has ffprobe && ver_ge "$v" "$FFMPEG_MIN"; then
    note "$v"
    return 0
  fi
  if [ "$OS" = Darwin ] && has brew; then
    if confirm "brew install ffmpeg を実行する（今は ${v:-なし}）？"; then
      if [ -n "$v" ]; then brew upgrade ffmpeg; else brew install ffmpeg; fi
      note "$(ffmpeg_version)"
      return 0
    fi
  fi
  note "入れていない。macOS: brew install ffmpeg / Ubuntu: sudo apt install ffmpeg（無いとモックが動画のアップロードを断る）"
}

setup_server_venv() {
  step "サーバー・モックの環境 A（$SERVER_DIR/.venv）"
  local py=$SERVER_DIR/.venv/bin/python
  # 手順は services/gpu_server/README.md §1 と同じ
  if [ "$UV_OK" = 1 ]; then
    (cd "$SERVER_DIR" && { [ -x .venv/bin/python ] || uv venv -q .venv -p 3.11; } && uv pip install -q -p .venv/bin/python -e '.[dev]')
  else
    (cd "$SERVER_DIR" && { [ -x .venv/bin/python ] || python3 -m venv .venv; } && .venv/bin/pip install -q -e '.[dev]')
  fi
  note "$("$py" --version)（pytest: cd $SERVER_DIR && .venv/bin/python -m pytest -q）"
  note "pipeline/ の環境 B は GPU 機だけ（services/gpu_server/README.md §2）。ここでは作らない。"
}

setup_deps() {
  step "pnpm install"
  pnpm install --frozen-lockfile
}

install_all() {
  setup_node
  setup_pnpm
  if [ "$APP_ONLY" = 0 ]; then
    setup_uv
    setup_ffmpeg
    setup_server_venv
  fi
  setup_deps

  step "確認"
  check_all || true

  printf '\n次に：\n'
  if [ "$NODE_SWITCHED" = 1 ]; then
    printf '  - 今のシェルはまだ別の Node。リポジトリで nvm use（fnm なら fnm use）してから make dev\n'
  fi
  printf '  - make mock（ターミナル 1）と make dev（ターミナル 2）。make help でほかのコマンド\n'
}

if [ "$MODE" = check ]; then
  check_all
else
  install_all
fi
