// AvatarLibraryPort：VRM のライブラリ（F07）。アプリデータの avatars/ に sha256 名でコピーして持つ

import type { Avatar } from "@/domain/avatar";

export interface AvatarLibraryPort {
  list(): Promise<Avatar[]>;
  get(avatarId: string): Promise<Avatar | null>;
  /** 検証（GLB・VRM 拡張・必須ボーン）して取り込む。同じファイルは既存を返す。失敗は AVATAR_INVALID */
  import(path: string): Promise<Avatar>;
  remove(avatarId: string): Promise<void>;
  readBytes(avatarId: string): Promise<Uint8Array>;
}
