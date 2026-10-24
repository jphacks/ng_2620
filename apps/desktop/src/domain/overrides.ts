// overrides.json の形式（contracts §6.2）。
// 区間演算・resolveModes など手修正のロジックはレーン B の domain/occlusion.ts が持ち、この型を使う。

export type OverrideMode = "front" | "behind";

export interface UnitOverride {
  id: string;
  kind: "unit";
  personId: number;
  unitId: number;
  range: [number, number]; // 両端を含むフレーム番号
  mode: OverrideMode;
  createdAt: string;
}

/** P1。マスクはプロキシ解像度の 8bit PNG（brush/ 配下） */
export interface BrushOverride {
  id: string;
  kind: "brush";
  personId: number;
  frame: number;
  mask: string;
  mode: OverrideMode;
  createdAt: string;
}

export type OcclusionOverride = UnitOverride | BrushOverride;

export interface OverridesDoc {
  schemaVersion: 1;
  items: OcclusionOverride[];
}

export function emptyOverrides(): OverridesDoc {
  return { schemaVersion: 1, items: [] };
}

export function parseOverrides(raw: unknown): OverridesDoc {
  if (!raw || typeof raw !== "object") return emptyOverrides();
  const items = (raw as { items?: unknown }).items;
  if (!Array.isArray(items)) return emptyOverrides();
  return {
    schemaVersion: 1,
    items: items.filter(
      (x): x is OcclusionOverride =>
        !!x && typeof x === "object" && ((x as { kind?: unknown }).kind === "unit" || (x as { kind?: unknown }).kind === "brush"),
    ),
  };
}
