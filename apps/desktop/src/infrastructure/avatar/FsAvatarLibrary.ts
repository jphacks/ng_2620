// AvatarLibraryPort の実装（F07）。アプリデータの avatars/ に <sha256>.vrm でコピーし、library.json に一覧を持つ

import type { AvatarLibraryPort } from "@/application/ports/avatar";
import type { Avatar } from "@/domain/avatar";
import { avatarIdFromSha256 } from "@/domain/avatar";
import { AppError } from "@/domain/errors";
import type { FileSystem } from "../fs/FileSystem";
import { joinPath } from "../fs/path";
import { sha256Hex } from "../media/hashers";
import { readVrm } from "./vrmMeta";

interface LibraryDoc {
  schemaVersion: 1;
  items: Avatar[];
}

const EXT_OF: Record<string, string> = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp" };

export class FsAvatarLibrary implements AvatarLibraryPort {
  constructor(
    private readonly fs: FileSystem,
    private readonly dir: () => Promise<string>,
    private readonly now: () => Date = () => new Date(),
  ) {}

  private async libraryFile() {
    const d = await this.dir();
    await this.fs.ensureDir(d);
    return joinPath(d, "library.json");
  }

  private async load(): Promise<LibraryDoc> {
    const file = await this.libraryFile();
    if (!(await this.fs.exists(file))) return { schemaVersion: 1, items: [] };
    try {
      const raw = JSON.parse(await this.fs.readText(file)) as Partial<LibraryDoc>;
      return { schemaVersion: 1, items: Array.isArray(raw.items) ? raw.items : [] };
    } catch {
      return { schemaVersion: 1, items: [] };
    }
  }

  private async store(doc: LibraryDoc) {
    await this.fs.writeTextAtomic(await this.libraryFile(), JSON.stringify(doc, null, 2));
  }

  async list() {
    return (await this.load()).items;
  }

  async get(avatarId: string) {
    return (await this.load()).items.find((a) => a.avatarId === avatarId) ?? null;
  }

  async import(path: string): Promise<Avatar> {
    if (!/\.vrm$/i.test(path)) throw new AppError("AVATAR_INVALID", "拡張子が .vrm ではありません");
    const bytes = await this.fs.readBytes(path);
    const parsed = readVrm(bytes); // 失敗は AVATAR_INVALID
    const sha256 = await sha256Hex(bytes);
    const avatarId = avatarIdFromSha256(sha256);
    const doc = await this.load();
    const existing = doc.items.find((a) => a.avatarId === avatarId);
    if (existing) return existing;

    const d = await this.dir();
    const file = joinPath(d, `${sha256}.vrm`);
    await this.fs.copyFile(path, file);
    let thumbnail: string | undefined;
    if (parsed.thumbnail) {
      thumbnail = joinPath(d, `${sha256}.${EXT_OF[parsed.thumbnail.mimeType] ?? "png"}`);
      await this.fs.writeBytes(thumbnail, parsed.thumbnail.bytes);
    }
    const avatar: Avatar = {
      avatarId,
      sha256,
      file,
      meta: { ...parsed.meta, name: parsed.meta.name || fileStem(path), thumbnail },
      importedAt: this.now().toISOString(),
    };
    await this.store({ ...doc, items: [...doc.items, avatar] });
    return avatar;
  }

  async remove(avatarId: string) {
    const doc = await this.load();
    const target = doc.items.find((a) => a.avatarId === avatarId);
    if (!target) return;
    await this.fs.remove(target.file);
    if (target.meta.thumbnail) await this.fs.remove(target.meta.thumbnail);
    await this.store({ ...doc, items: doc.items.filter((a) => a.avatarId !== avatarId) });
  }

  async readBytes(avatarId: string) {
    const a = await this.get(avatarId);
    if (!a) throw new AppError("AVATAR_INVALID", "アバターがライブラリにありません", { detail: { avatarId } });
    return this.fs.readBytes(a.file);
  }
}

function fileStem(path: string) {
  const name = path.split(/[\\/]/).pop() ?? path;
  return name.replace(/\.vrm$/i, "");
}
