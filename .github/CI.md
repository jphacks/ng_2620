# CI / リリース

| workflow | きっかけ | 内容 |
| --- | --- | --- |
| `ci.yml` | PR（base は問わない）、develop / main への push | prettier・rustfmt は差分があれば失敗。ESLint・clippy は error で失敗、warning は annotation のみ |
| `release-please.yml` | develop への push | Conventional Commits から次の version と CHANGELOG を決め、Release PR（develop 向け）を作る |
| `release.yml` | main への push | `tauri.conf.json` の version で `release/vX.Y.Z` ブランチ・タグを切り、Windows(x64) / macOS(arm64, x64) をビルドして Release に添付（未署名）。同じタグがあれば何もしない |

## 流れ

1. 機能ブランチ → develop（`feat:` / `fix:` などの Conventional Commits で書く）
2. release-please が Release PR を作る（version が `package.json` ×2・`tauri.conf.json`・`Cargo.toml`・`Cargo.lock` に入る）。マージする
3. develop → main の PR を出す。1 approve でマージ → `release.yml` が走る

## ローカル

`pnpm install` で lefthook の pre-push が入る。push 時に prettier / eslint --fix / cargo fmt / clippy --fix を掛け、
直した分は `style: autofix` として commit して push を止める（もう一度 push する）。`git push --no-verify` で飛ばせる。

## 手動で設定するもの（リポジトリの admin）

- Settings → Rules（または Branch protection）で `main`：Require a pull request before merging（Required approvals: 1）、
  Require status checks（`TypeScript (prettier / eslint)`、`Rust (rustfmt / clippy)`）、`develop` からの PR を想定
- Secrets に `RELEASE_PLEASE_TOKEN`（`contents` と `pull requests` の write を持つ PAT か GitHub App のトークン）。
  無いと GITHUB_TOKEN で動くが、Release PR に CI が走らない
- Settings → Actions → General → Workflow permissions で "Allow GitHub Actions to create and approve pull requests"
