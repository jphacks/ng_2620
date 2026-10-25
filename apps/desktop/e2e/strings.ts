// E2E が画面の文言で要素を探すための写し（src/presentation/shared/i18n/ja.ts と同じ値）。
// src を import しない理由：Playwright は Node の型除去で読むことがあり、src の TypeScript（コンストラクタの引数プロパティなど）を読めないため。
// ずれたら src/e2e-strings.test.ts が失敗する。

export const ja = {
  workspace: { edit: "編集", export: "書き出し" },
  jobsPanel: { queue: "サーバーのキュー" },
  status: { saved: "保存済み" },
  export: { start: "書き出す", done: "完了" },
  cast: { render: "アバターを描画" },
  avatar: { choose: "アバターを選ぶ" },
  home: {
    notConnected: "未接続 — サンプルは開けます",
    dropTitle: "動画をここにドロップ",
    openZip: "zip を読み込む…",
    saveZip: "zip で保存",
  },
  restricted: { title: "制限モード — データはこのブラウザの中にだけ保存されます", find: "ローカルアプリを探す" },
  connection: {
    local: { profileName: "この PC" },
    add: "プロファイルを追加",
    test: "接続テスト",
    testOk: "接続できました",
    testFailed: { reach: "サーバーに届きません" },
    consent: { title: "動画の送信先を確認してください", ok: "同意して続ける" },
  },
  importStep: { sourceMissingTitle: "元の動画が見つかりません", relink: "動画を選び直す", start: "解析を始める" },
  common: { save: "保存", cancel: "キャンセル", add: "追加" },
};

/** 画面側が必須とするヒューマノイドのボーン（infrastructure/avatar/vrmMeta.ts の REQUIRED_BONES と同じ）。E2E 用の最小の VRM に入れる */
export const VRM_BONES = [
  "hips",
  "spine",
  "head",
  "leftUpperArm",
  "leftLowerArm",
  "leftHand",
  "rightUpperArm",
  "rightLowerArm",
  "rightHand",
  "leftUpperLeg",
  "leftLowerLeg",
  "leftFoot",
  "rightUpperLeg",
  "rightLowerLeg",
  "rightFoot",
];

/** E2E の VRM（make_vrm）のメタ情報の名前 */
export const SAMPLE_AVATAR = "Sample";
