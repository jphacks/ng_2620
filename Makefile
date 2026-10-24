# vvlog の開発用コマンド（package.json のスクリプトの薄いラッパー）。macOS / Linux 向け。
# Windows は make / sh の対象外：SETUP.md の手動手順と pnpm のコマンドをそのまま使う。
#
#   make            このヘルプ
#   make setup      初期セットアップ（scripts/setup.sh。確認を省くなら make setup ARGS=--yes）
#   make doctor     何も入れずに、足りないものを一覧にする

SHELL := /bin/bash
PNPM ?= pnpm
ARGS ?=

.DEFAULT_GOAL := help
.PHONY: help setup doctor install dev web web-memory mock test lint typecheck build check clean clean-rust

help: ## このヘルプ
	@printf 'vvlog の開発用コマンド（make <ターゲット>）\n\n'
	@awk 'BEGIN { FS = ":.*## " } /^[a-zA-Z_-]+:.*## / { printf "  %-12s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@printf '\n初めては make setup（足りないものを確かめて入れる）→ make mock と make dev。\n'

setup: ## 初期セットアップ（Node・pnpm・Rust・Python・ffmpeg・依存。ARGS=--yes / --app-only）
	@scripts/setup.sh $(ARGS)

doctor: ## 何も入れずに、ツールの有無と版を一覧にする（足りなければ失敗）
	@scripts/setup.sh --check $(ARGS)

install: ## pnpm install（ロックファイルどおり）
	$(PNPM) install --frozen-lockfile

dev: ## デスクトップアプリを起動（pnpm tauri dev。Rust が要る）
	$(PNPM) tauri dev

web: ## ブラウザで画面だけ（pnpm dev、http://localhost:1420）
	$(PNPM) dev

web-memory: ## ブラウザ＋シミュレーター（サーバー不要）
	$(PNPM) --filter @vvlog/desktop dev:memory

mock: ## モックサーバー（http://127.0.0.1:8766、トークン mock）
	$(PNPM) mock

test: ## Vitest
	$(PNPM) test

lint: ## ESLint
	$(PNPM) lint

typecheck: ## tsc --noEmit
	$(PNPM) typecheck

build: ## 型チェック＋vite build（apps/desktop/dist）
	$(PNPM) build

check: typecheck lint test ## typecheck・lint・test をまとめて

clean: ## node_modules と apps/desktop/dist を消す（次は make install）
	rm -rf node_modules apps/desktop/node_modules apps/desktop/dist

clean-rust: ## Rust のビルド結果（apps/desktop/src-tauri/target）を消す。次の make dev は時間がかかる
	rm -rf apps/desktop/src-tauri/target
