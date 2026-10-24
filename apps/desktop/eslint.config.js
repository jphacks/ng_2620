import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import js from "@eslint/js";
import importPlugin from "eslint-plugin-import";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

const root = dirname(fileURLToPath(import.meta.url));
const features = [
  "home",
  "import",
  "connection",
  "jobs",
  "cast",
  "avatar-library",
  "export",
  "settings",
  "editor",
  "occlusion-edit",
];

// 層の依存規則（architecture/overview §3）
//   presentation → application → domain の一方向。infrastructure は application のポートを実装する。
//   組み立て（DI）は presentation/app/container.ts だけが infrastructure を import してよい。
//   feature 間の直接 import は禁止（共有は domain / application/ports / presentation/shared）。
const layerZones = [
  { target: "./src/domain", from: "./src/application", message: "domain は外側の層に依存しない" },
  { target: "./src/domain", from: "./src/infrastructure", message: "domain は外側の層に依存しない" },
  { target: "./src/domain", from: "./src/presentation", message: "domain は外側の層に依存しない" },
  {
    target: "./src/application",
    from: "./src/infrastructure",
    message: "application はポート越しにだけ infrastructure を使う",
  },
  { target: "./src/application", from: "./src/presentation", message: "application は presentation に依存しない" },
  {
    target: "./src/infrastructure",
    from: "./src/presentation",
    message: "infrastructure は presentation に依存しない",
  },
  {
    target: "./src/presentation",
    from: "./src/infrastructure",
    except: [],
    message: "presentation から infrastructure を直接 import しない（container.ts で組み立てる）",
  },
  ...features.map((f) => ({
    target: `./src/presentation/features/${f}`,
    from: "./src/presentation/features",
    except: [`./${f}`],
    message: "feature 間の直接 import は禁止。共有するものは presentation/shared へ",
  })),
  {
    target: "./src/presentation/shared",
    from: "./src/presentation/features",
    message: "shared は feature に依存しない",
  },
];

export default tseslint.config(
  { ignores: ["dist", "src-tauri", "node_modules", "*.config.js"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: { globals: globals.browser },
    plugins: { import: importPlugin, "react-hooks": reactHooks },
    settings: {
      "import/resolver": { typescript: { project: `${root}/tsconfig.json` } },
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "import/no-restricted-paths": ["error", { basePath: root, zones: layerZones }],
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },
  {
    // composition root だけは infrastructure を組み立ててよい
    files: ["src/presentation/app/container.ts"],
    rules: {
      "import/no-restricted-paths": [
        "error",
        { basePath: root, zones: layerZones.filter((z) => z.from !== "./src/infrastructure") },
      ],
    },
  },
  {
    // domain は外部ライブラリ・I/O に依存しない
    files: ["src/domain/**/*.ts"],
    ignores: ["src/domain/**/*.test.ts"],
    rules: {
      "no-restricted-imports": [
        "error",
        { patterns: [{ regex: "^(?!\.{1,2}/|@/domain/)", message: "domain は外部ライブラリに依存しない" }] },
      ],
    },
  },
);
