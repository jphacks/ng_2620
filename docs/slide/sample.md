---
marp: true
theme: virtualtwin
paginate: true
footer: © 2026 VirtualTwin / ng_2620
---

<!-- _class: lead -->
<!-- _paginate: false -->

![w:480 logo](../../designdocs/brand/color/svg/virtualtwin-logo-horizontal.svg)

# なりたい自分で、話そう。

JPHACKS 2026 ・ チーム ng_2620

---

## 課題と解決

- **隠したい自分**を、そのまま出さなくていい
  - 顔や背景を、アバターに置き換える
- ビデオ会議でも、*なりたい自分*で参加できる

> 白い子が隠したい自分、黄色い子がなりたい自分。

---

<!-- _class: cols -->

## 仕組み

<div>
<div class="card">

### 入力
カメラ映像を取り込む

</div>
<div class="card yellow">

### 変換
GPU サーバーでアバターに置き換え

</div>
</div>

---

<!-- _class: invert -->

## 技術構成

| レイヤー | 技術 |
|---|---|
| デスクトップ | Tauri + React |
| GPU サーバー | Python (uv) |

```bash
make setup
```

---

<!-- _class: accent -->

## ひとことで言うと

**アバターが前に出る**アプリです。

---

<!-- _class: lead -->
<!-- _paginate: false -->

# ありがとうございました
