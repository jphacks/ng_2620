// 成果物の名前とローカルでの置き場所（contracts §4）。cache/<jobId>/ の下に置く

import type { ArtifactKind, StageName } from "./job";

export type ArtifactName =
  | "meta"
  | "proxy_frames"
  | "persons"
  | "person_thumbs"
  | "track_masks"
  | "mesh_preview"
  | "units"
  | "units_index"
  | "layers"
  | "layers_index"
  | "preview_video"
  | "export_video";

interface ArtifactSpec {
  kind: ArtifactKind;
  stage: StageName;
  /** json / video はファイル名。zip は展開先のフォルダ（zip の中のパスは contracts §4 のとおり） */
  local: string;
}

export const ARTIFACTS: Record<ArtifactName, ArtifactSpec> = {
  meta: { kind: "json", stage: "ingest", local: "meta.json" },
  proxy_frames: { kind: "zip", stage: "ingest", local: "frames" },
  persons: { kind: "json", stage: "track", local: "persons.json" },
  person_thumbs: { kind: "zip", stage: "track", local: "person_thumbs" },
  track_masks: { kind: "zip", stage: "track", local: "masks" },
  mesh_preview: { kind: "video", stage: "mesh", local: "mesh_preview.mp4" },
  units: { kind: "zip", stage: "occlusion", local: "units" },
  units_index: { kind: "json", stage: "occlusion", local: "units.json" },
  layers: { kind: "zip", stage: "layers", local: "." }, // clean/ avatar/ hide/
  layers_index: { kind: "json", stage: "layers", local: "layers.json" },
  preview_video: { kind: "video", stage: "layers", local: "preview.mp4" },
  export_video: { kind: "video", stage: "encode", local: "export.mp4" },
};

export function isKnownArtifact(name: string): name is ArtifactName {
  return Object.prototype.hasOwnProperty.call(ARTIFACTS, name);
}

/** cache/<jobId>/ からの相対パス（zip は展開先フォルダ） */
export function artifactLocalPath(name: string, kind: ArtifactKind): string {
  if (isKnownArtifact(name)) return ARTIFACTS[name].local;
  if (kind === "zip") return name;
  const ext = kind === "json" ? ".json" : kind === "video" ? ".mp4" : "";
  return `${name}${ext}`;
}

/** フレーム番号のファイル名（5 桁ゼロ埋め） */
export function frameFile(f: number, ext: string): string {
  return `${String(f).padStart(5, "0")}.${ext}`;
}

/** プロジェクトフォルダからの相対パス */
export function cacheRel(jobId: string, ...parts: string[]): string {
  return ["cache", jobId, ...parts.filter((p) => p && p !== ".")].join("/");
}
