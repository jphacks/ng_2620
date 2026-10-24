// パスの結合。Windows の \ と macOS の / を、基準のパスに合わせて使い分ける

export function sepOf(base: string): "/" | "\\" {
  return base.includes("\\") && !base.includes("/") ? "\\" : "/";
}

export function joinPath(base: string, ...parts: string[]): string {
  const sep = sepOf(base);
  const segs = parts.flatMap((p) => p.split(/[\\/]+/)).filter((s) => s && s !== ".");
  if (segs.length === 0) return base;
  const head = base.endsWith("/") || base.endsWith("\\") ? base.slice(0, -1) : base;
  return [head, ...segs].join(sep);
}

export function dirnameOf(path: string): string {
  const i = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  return i <= 0 ? path.slice(0, i + 1) : path.slice(0, i);
}

export function basenameOf(path: string): string {
  const i = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  return path.slice(i + 1);
}
