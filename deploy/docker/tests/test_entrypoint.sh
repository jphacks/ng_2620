#!/usr/bin/env bash
# entrypoint.sh のトークン決定ロジックの小さなテスト(Docker も GPU も要らない)。
#   VVLOG_SERVER_PYTHON=<fastapi 等(services/gpu_server の依存)が入った python> deploy/docker/tests/test_entrypoint.sh
# 弱さの判定はサーバーの weak_token_reason を呼ぶので、その python が vvlog_server を import できることが要る。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
export VVLOG_APP_DIR="$ROOT" VVLOG_ENTRYPOINT_SOURCE_ONLY=1
export VVLOG_SERVER_PYTHON="${VVLOG_SERVER_PYTHON:-python3}"

if ! PYTHONPATH="$ROOT/services/gpu_server" "$VVLOG_SERVER_PYTHON" -c 'import vvlog_server.main' 2>/dev/null; then
  echo "SKIP: $VVLOG_SERVER_PYTHON で vvlog_server.main を import できません(VVLOG_SERVER_PYTHON を指定してください)"
  exit 0
fi

# shellcheck source=../entrypoint.sh
source "$HERE/../entrypoint.sh"

fail=0
check() { # 名前 条件(0=成功)
  if [ "$2" -eq 0 ]; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi
}
run_ensure() { # 環境を変えて ensure_token を実行し、最終的な VVLOG_API_TOKEN と GENERATED_TOKEN を得る(標準エラーは捨てる)
  unset VVLOG_API_TOKEN VVLOG_ALLOW_WEAK_TOKEN || true
  GENERATED_TOKEN=""
  for kv in "$@"; do export "${kv?}"; done
  ensure_token 2>/dev/null
}

STRONG="$(openssl rand -hex 24)"

run_ensure
check "未設定なら生成する(48桁の16進)" "$([[ "$VVLOG_API_TOKEN" =~ ^[0-9a-f]{48}$ && "$GENERATED_TOKEN" == "$VVLOG_API_TOKEN" ]]; echo $?)"

for weak in password test-token short aaaaaaaaaaaaaaaaaaaa; do
  run_ensure "VVLOG_API_TOKEN=$weak"
  check "弱い値($weak)は拒否して生成する" "$([[ "$VVLOG_API_TOKEN" != "$weak" && -n "$GENERATED_TOKEN" ]]; echo $?)"
done

run_ensure "VVLOG_API_TOKEN=$STRONG"
check "強い値はそのまま使い、生成しない" "$([[ "$VVLOG_API_TOKEN" == "$STRONG" && -z "$GENERATED_TOKEN" ]]; echo $?)"

run_ensure "VVLOG_API_TOKEN=password" "VVLOG_ALLOW_WEAK_TOKEN=1"
check "VVLOG_ALLOW_WEAK_TOKEN=1 なら弱くてもそのまま(サーバー側が警告する)" "$([[ "$VVLOG_API_TOKEN" == "password" && -z "$GENERATED_TOKEN" ]]; echo $?)"

# 表示は 1 回だけ。2 回目は何も出さない。ユーザーが決めたトークンは表示しない
run_ensure
tok="$GENERATED_TOKEN"
tmp="$(mktemp)"
print_token_once 2>"$tmp"; first="$(cat "$tmp")"
print_token_once 2>"$tmp"; second="$(cat "$tmp")"
rm -f "$tmp"
check "生成したトークンは 1 回だけ表示する" "$([[ "$first" == *"$tok"* && -z "$second" ]]; echo $?)"
run_ensure "VVLOG_API_TOKEN=$STRONG"
out="$(print_token_once 2>&1)"
check "ユーザー指定のトークンは表示しない" "$([[ -z "$out" ]]; echo $?)"

exit "$fail"
