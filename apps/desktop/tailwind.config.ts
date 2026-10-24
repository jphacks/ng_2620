import type { Config } from "tailwindcss";

// 色・余白・角丸は tokens.css の CSS 変数を参照する（値を直接書かない）
const v = (name: string) => `var(--${name})`;
// 透明度の指定（bg-accent/20 など）が効くように RGB の 3 値から作る
const c = (name: string) => `rgb(var(--rgb-${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: c("bg"),
        panel: c("panel"),
        "panel-2": c("panel-2"),
        raised: c("raised"),
        border: c("border"),
        fg: c("text"),
        muted: c("text-muted"),
        faint: c("text-faint"),
        accent: c("accent"),
        "accent-fg": c("accent-fg"),
        success: c("success"),
        warning: c("warning"),
        danger: c("danger"),
        overlay: v("color-overlay"),
      },
      borderRadius: {
        sm: v("radius-sm"),
        DEFAULT: v("radius-md"),
        md: v("radius-md"),
        lg: v("radius-lg"),
      },
      spacing: {
        gutter: v("space-gutter"),
        titlebar: v("titlebar-height"),
        statusbar: v("statusbar-height"),
      },
      fontSize: {
        xs: [v("font-xs"), "1.4"],
        sm: [v("font-sm"), "1.5"],
        base: [v("font-md"), "1.5"],
        lg: [v("font-lg"), "1.4"],
        xl: [v("font-xl"), "1.3"],
      },
    },
  },
  plugins: [],
} satisfies Config;
