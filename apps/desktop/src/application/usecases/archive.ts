// プロジェクトの zip 書き出し／読み込み（制限モード、#27）

import type { FileExchangePort, ProjectArchivePort } from "../ports/archive";
import type { ProjectRepositoryPort } from "../ports/project";

const stripExt = (name: string) => name.replace(/\.zip$/i, "").replace(/\.vvlog$/i, "");

export class ExportProjectZip {
  constructor(private readonly d: { archive: ProjectArchivePort; exchange: FileExchangePort }) {}

  /** 返り値は保存したファイル名 */
  async execute(input: { path: string; name: string }): Promise<string> {
    const bytes = await this.d.archive.pack(input.path);
    const filename = `${input.name.replace(/[\\/:*?"<>|]/g, "_") || "project"}.vvlog.zip`;
    await this.d.exchange.download(filename, bytes, "application/zip");
    return filename;
  }
}

export class ImportProjectZip {
  constructor(
    private readonly d: { archive: ProjectArchivePort; exchange: FileExchangePort; projects: ProjectRepositoryPort },
  ) {}

  /** zip を選ばせて、新しいプロジェクトフォルダに展開する。選ばれなければ null。開くのは呼び出し側（OpenProject） */
  async execute(): Promise<{ path: string } | null> {
    const { archive, exchange, projects } = this.d;
    const file = await exchange.pickFile({ accept: [".zip"] });
    if (!file) return null;
    const { path } = await projects.allocate(stripExt(file.name) || "project", await projects.defaultDir());
    try {
      await archive.unpack(file.bytes, path);
    } catch (e) {
      await projects.remove(path).catch(() => {});
      throw e;
    }
    return { path };
  }
}
