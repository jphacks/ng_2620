// F07 アバターライブラリ

import type { Avatar } from "@/domain/avatar";
import type { AvatarLibraryPort } from "../ports/avatar";

export class ListAvatars {
  constructor(private readonly d: { avatars: AvatarLibraryPort }) {}
  async execute(): Promise<Avatar[]> {
    const list = await this.d.avatars.list();
    return [...list].sort((a, b) => b.importedAt.localeCompare(a.importedAt));
  }
}

export class ImportAvatar {
  constructor(private readonly d: { avatars: AvatarLibraryPort }) {}
  /** 複数をまとめて取り込む。失敗したものはエラーとして返す（他は続ける） */
  async execute(paths: readonly string[]): Promise<{ imported: Avatar[]; failed: { path: string; error: unknown }[] }> {
    const imported: Avatar[] = [];
    const failed: { path: string; error: unknown }[] = [];
    for (const path of paths) {
      try {
        imported.push(await this.d.avatars.import(path));
      } catch (error) {
        failed.push({ path, error });
      }
    }
    return { imported, failed };
  }
}

export class RemoveAvatar {
  constructor(private readonly d: { avatars: AvatarLibraryPort }) {}
  execute(avatarId: string): Promise<void> {
    return this.d.avatars.remove(avatarId);
  }
}
