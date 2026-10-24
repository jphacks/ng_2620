// canvas に描くときの色。値は tokens.css から読む

const FALLBACK = ["#4f8cff", "#f0883e", "#3fb950", "#db61a2"];

export function personColor(personId: number): string {
  const i = (personId - 1) % 4;
  if (typeof document === "undefined") return FALLBACK[i]!;
  const v = getComputedStyle(document.documentElement)
    .getPropertyValue(`--color-person-${i + 1}`)
    .trim();
  return v || FALLBACK[i]!;
}

export function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.replace(/./g, (c) => c + c) : h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
