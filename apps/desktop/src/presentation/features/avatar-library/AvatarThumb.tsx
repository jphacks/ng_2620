import { UserRound } from "lucide-react";
import type { Avatar } from "@/domain/avatar";
import { useServices } from "@/presentation/shared/services";
import { cx } from "@/presentation/shared/ui";

export function AvatarThumb({
  avatar,
  size = 40,
  className,
}: {
  avatar: Avatar | null;
  size?: number;
  className?: string;
}) {
  const { platform } = useServices();
  const style = { width: size, height: size };
  if (!avatar?.meta.thumbnail) {
    return (
      <div
        style={style}
        className={cx("flex shrink-0 items-center justify-center rounded bg-raised text-faint", className)}
      >
        <UserRound size={Math.round(size * 0.55)} />
      </div>
    );
  }
  return (
    <img
      src={platform.files.toUrl(avatar.meta.thumbnail)}
      alt=""
      style={style}
      className={cx("shrink-0 rounded bg-raised object-cover", className)}
      draggable={false}
    />
  );
}
