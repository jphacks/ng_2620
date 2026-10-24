#!/bin/sh
# lefthook の pre-push から呼ぶ。使い方: prepush.sh init | ts | rust | commit（lefthook.yml 参照）
# 直せるものを直し、直せなかった lint は警告として出す（終了コードは 0）。commit だけが差分を見て 1 を返す。
set -u
cd "$(git rev-parse --show-toplevel)" || exit 0

dirty_file="$(git rev-parse --git-path vvlog-prepush-dirty)"
case "$1" in
init)
  rm -f "$dirty_file"
  [ -n "$(git status --porcelain)" ] && : >"$dirty_file"
  exit 0
  ;;
ts | rust)
  [ -f "$dirty_file" ] && {
    echo "⚠ 未コミットの変更があるので、自動修正はせずチェックだけ行います"
    mode=check
  } || mode=fix
  ;;
esac

case "$1" in
ts)
  if [ "$mode" = fix ]; then
    pnpm exec prettier --write apps/desktop --log-level warn
    pnpm --filter @vvlog/desktop exec eslint . --fix || echo "⚠ 直せない ESLint の指摘があります（上を確認）"
  else
    pnpm exec prettier --check apps/desktop --log-level warn || echo "⚠ prettier の差分があります"
    pnpm --filter @vvlog/desktop exec eslint . || echo "⚠ ESLint の指摘があります"
  fi
  ;;
rust)
  cd apps/desktop/src-tauri || exit 0
  if [ "$mode" = fix ]; then
    cargo fmt
    cargo clippy --fix --allow-dirty --allow-staged --quiet >/dev/null 2>&1
  else
    cargo fmt --check >/dev/null 2>&1 || echo "⚠ rustfmt の差分があります"
  fi
  cargo clippy --quiet 2>&1 | grep -E '^(warning|error)' | sort | uniq -c | sed 's/^/⚠ clippy: /'
  ;;
commit)
  if [ -f "$dirty_file" ]; then
    rm -f "$dirty_file"
    exit 0
  fi
  if [ -n "$(git status --porcelain)" ]; then
    git add -A apps pnpm-lock.yaml
    git commit -q -m "style: autofix（pre-push の linter / formatter）"
    echo "✔ 自動修正を commit しました。内容を確認して、もう一度 push してください。"
    exit 1
  fi
  ;;
esac
exit 0
