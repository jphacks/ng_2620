// ライセンスのバッジと概要（作者・商用可否・改変可否）。商用不可は黄色の注記（ブロックはしない）

import type { Avatar } from "@/domain/avatar";
import { licenseBadge, modificationAllowed } from "@/domain/avatar";
import { ja } from "@/presentation/shared/i18n/ja";
import { Badge } from "@/presentation/shared/ui";

export function LicenseBadge({ avatar }: { avatar: Avatar }) {
  const b = licenseBadge(avatar.meta);
  return (
    <Badge tone={b === "commercial_ok" ? "success" : b === "commercial_ng" ? "warning" : "muted"}>
      {ja.avatar.badge[b]}
    </Badge>
  );
}

export function LicenseSummary({ avatar }: { avatar: Avatar }) {
  const b = licenseBadge(avatar.meta);
  const authors = avatar.meta.authors.join("、") || ja.avatar.noAuthor;
  const parts = [
    authors,
    avatar.meta.license.name,
    ja.avatar.badge[b],
    ja.avatar.modification(modificationAllowed(avatar.meta)),
  ].filter(Boolean);
  return (
    <div className="text-xs">
      <span className="text-muted">{parts.join("・")}</span>
      {b === "commercial_ng" && <div className="mt-0.5 text-warning">{ja.cast.commercialNg}</div>}
    </div>
  );
}
