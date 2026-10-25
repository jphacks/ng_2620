# スライド（Marp）

VirtualTwin のロゴ（`designdocs/brand/`）に合わせた Marp テーマ `virtualtwin` を置いている。

- テーマ: [`themes/virtualtwin.css`](themes/virtualtwin.css)
- 見本: [`sample.md`](sample.md)（全クラスの使用例）

ロゴは `designdocs/brand/` の SVG を参照する。`url()` はスライドの場所から解決されるので、**スライドは `docs/slide/` 直下に置く**（別の深さに置くなら CSS のパスを直す）。ロゴ一式（PR #7）が入っていないブランチでは、右下のマークとタイトルのロゴが出ない。

## 使い方

スライドの先頭に次を書く。

```markdown
---
marp: true
theme: virtualtwin
paginate: true
footer: © 2026 VirtualTwin / ng_2620
---
```

プレビュー・書き出しではテーマファイルを指定する。

```bash
npx @marp-team/marp-cli --theme-set docs/slide/themes/virtualtwin.css --allow-local-files docs/slide/sample.md -o docs/slide/sample.pdf
```

VS Code の Marp 拡張（`marp-team.marp-vscode`）では、リポジトリの `.vscode/settings.json` がテーマを登録しているので、`ng_2620` をフォルダとして開けばプレビューにテーマが反映される。親のフォルダを開いている場合は、そのフォルダの `.vscode/settings.json` の `markdown.marp.themes` に、そこからのパス（例 `./ng_2620/docs/slide/themes/virtualtwin.css`）を足す。

- ページ番号は右上、正方形マークは右下、コピーライトは最下部の中央（`footer`）。`lead` では出さない。

## クラス

`<!-- _class: lead -->` のようにスライドごとに指定する。

| クラス | 用途 |
|---|---|
| `lead` | タイトル・章扉。緑のグラデーション背景、中央寄せ |
| `invert` | 暗い緑の背景（暗い背景用のマークに切り替わる） |
| `accent` | 黄色の背景 |
| `cols` | 2 カラム（直下に `<div>` を 2 つ置く） |
| `small` | 本文を小さく |
| `nologo` | 右下のマークを出さない |

部品: `<div class="card">`（緑）/ `<div class="card yellow">`（黄色）。

## 配色

ロゴの色に合わせている（`designdocs/brand/README.md` §3）。緑がメイン、黄色がサブ。

| 用途 | 色 |
|---|---|
| 緑 | `#7BEBA6` / `#2DBE6C` / `#128A48` / `#14532D` |
| 黄色 | `#FFE57A` / `#FFC93C` / `#E89C0C` |

## 守ること

- ロゴの色を変えたり、引き伸ばしたりしない（ブランドのガイドライン §6.2）。
- 中間の明るさの背景（`lead` の緑など）にカラー版のロゴを直接置かない。`lead` では alt に `logo` を入れた画像（`![w:480 logo](...)`）が自動で白い板に載る。
- フォントは Google Fonts の M PLUS Rounded 1c を読み込む。オフラインでは Hiragino Maru Gothic ProN に落ちる。
