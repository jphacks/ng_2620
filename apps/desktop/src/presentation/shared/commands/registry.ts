// CommandRegistry：ショートカット → コマンド。各 feature が自分のコマンドを登録する（F15）。
// キーの表記は "mod+s"（macOS は ⌘、Windows は Ctrl）、"ctrl+j"（macOS でも Control）、"shift+mod+z" など

export type Workspace = "import" | "edit" | "export";
export type CommandScope = "global" | "project" | `workspace:${Workspace}`;

export interface CommandContext {
  projectOpen: boolean;
  workspace: Workspace | null;
}

export interface Command {
  id: string;
  /** 同じコマンドに複数のキーを割り当ててよい */
  keys: string[];
  scope: CommandScope;
  label?: string;
  /** 入力欄にフォーカスがあっても動かす（⌘S など修飾キー付きは既定で動く） */
  allowInInput?: boolean;
  run(): void;
}

const MODS = ["mod", "ctrl", "alt", "shift"] as const;
const KEY_ALIASES: Record<string, string> = {
  " ": "space",
  spacebar: "space",
  arrowleft: "left",
  arrowright: "right",
  arrowup: "up",
  arrowdown: "down",
  esc: "escape",
  del: "delete",
  cmd: "mod",
  meta: "mod",
  option: "alt",
};

/** "Shift+Mod+Z" → "mod+shift+z"。Windows では ctrl と mod は同じキー */
export function normalizeCombo(combo: string, isMac: boolean): string {
  const parts = combo
    .toLowerCase()
    .split("+")
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => KEY_ALIASES[p] ?? p);
  // "mod++"（⌘+）のように + そのものがキーのとき
  if (combo.endsWith("++")) parts.push("+");
  const mods = new Set(
    parts.filter((p) => (MODS as readonly string[]).includes(p)).map((p) => (!isMac && p === "ctrl" ? "mod" : p)),
  );
  const key = parts.filter((p) => !(MODS as readonly string[]).includes(p)).at(-1) ?? "";
  return [...MODS.filter((m) => mods.has(m)), key].join("+");
}

/** キー入力を正規化した表記に */
export function comboFromEvent(
  e: Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "altKey" | "shiftKey" | "code">,
  isMac: boolean,
): string {
  const mods: string[] = [];
  if (isMac ? e.metaKey : e.ctrlKey) mods.push("mod");
  if (isMac && e.ctrlKey) mods.push("ctrl");
  if (e.altKey) mods.push("alt");
  if (e.shiftKey) mods.push("shift");
  // ⌥ や ⇧ で文字が変わる配列でも数字は数字として扱う
  let key = /^Digit\d$/.test(e.code) ? e.code.slice(5) : e.key.toLowerCase();
  key = KEY_ALIASES[key] ?? key;
  return [...mods, key].join("+");
}

const SPECIFICITY: Record<string, number> = { global: 0, project: 1 };
const specificity = (s: CommandScope) => SPECIFICITY[s] ?? 2;

function scopesOverlap(a: CommandScope, b: CommandScope): boolean {
  if (a === b) return true;
  if (a === "global" || b === "global") return true;
  if (a === "project" || b === "project") return true; // project は全ワークスペースで有効
  return false; // 別々のワークスペース同士は同じキーを使ってよい
}

function isActive(scope: CommandScope, ctx: CommandContext): boolean {
  if (scope === "global") return true;
  if (!ctx.projectOpen) return false;
  if (scope === "project") return true;
  return scope === `workspace:${ctx.workspace}`;
}

export class DuplicateShortcutError extends Error {}

export class CommandRegistry {
  private readonly commands = new Map<string, Command & { combos: string[] }>();

  constructor(private readonly isMac: boolean) {}

  register(cmd: Command): () => void {
    const combos = cmd.keys.map((k) => normalizeCombo(k, this.isMac));
    for (const other of this.commands.values()) {
      if (other.id === cmd.id) continue;
      const clash = combos.find((c) => other.combos.includes(c));
      if (clash && scopesOverlap(other.scope, cmd.scope)) {
        throw new DuplicateShortcutError(`ショートカット ${clash} が重複しています（${other.id} と ${cmd.id}）`);
      }
    }
    const entry = { ...cmd, combos };
    this.commands.set(cmd.id, entry);
    return () => {
      if (this.commands.get(cmd.id) === entry) this.commands.delete(cmd.id);
    };
  }

  /** 有効なコマンドのうち、最も狭い範囲のもの */
  resolve(combo: string, ctx: CommandContext): Command | null {
    let best: (Command & { combos: string[] }) | null = null;
    for (const c of this.commands.values()) {
      if (!c.combos.includes(combo) || !isActive(c.scope, ctx)) continue;
      if (!best || specificity(c.scope) > specificity(best.scope)) best = c;
    }
    return best;
  }

  list(): Command[] {
    return [...this.commands.values()];
  }
}
