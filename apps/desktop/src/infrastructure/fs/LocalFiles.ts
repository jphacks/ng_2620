// LocalFilePort の実装（FileSystem の薄い包み）

import type { LocalFilePort } from "@/application/ports/media";
import type { FileSystem } from "./FileSystem";
import { joinPath } from "./path";

export class LocalFiles implements LocalFilePort {
  constructor(private readonly fs: FileSystem) {}
  exists(path: string) {
    return this.fs.exists(path);
  }
  stat(path: string) {
    return this.fs.stat(path);
  }
  readText(path: string) {
    return this.fs.readText(path);
  }
  readBytes(path: string) {
    return this.fs.readBytes(path);
  }
  readChunk(path: string, offset: number, length: number) {
    return this.fs.readChunk(path, offset, length);
  }
  writeBytes(path: string, bytes: Uint8Array) {
    return this.fs.writeBytes(path, bytes);
  }
  toUrl(path: string) {
    return this.fs.toUrl(path);
  }
  join(base: string, ...parts: string[]) {
    return joinPath(base, ...parts);
  }
}
