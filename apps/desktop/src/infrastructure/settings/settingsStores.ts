// SettingsPort / SecretStorePort の実装。
// フルモード：ローカルサーバーの設定フォルダの settings.json / secrets.json（権限 600。P1 で OS のキーチェーンへ）
// 制限モード：localStorage

import type { AppSettings, SecretStorePort, SettingsPort } from "@/application/ports/settings";
import { defaultSettings } from "@/application/ports/settings";
import type { FileSystem } from "../fs/FileSystem";

export function normalizeSettings(raw: unknown): AppSettings {
  const d = defaultSettings();
  if (!raw || typeof raw !== "object") return d;
  const r = raw as Partial<AppSettings>;
  return {
    schemaVersion: 1,
    profiles: Array.isArray(r.profiles)
      ? r.profiles.filter((p) => p && typeof p.id === "string" && typeof p.baseUrl === "string")
      : d.profiles,
    activeProfileId: typeof r.activeProfileId === "string" ? r.activeProfileId : null,
    panelLayouts: r.panelLayouts && typeof r.panelLayouts === "object" ? r.panelLayouts : {},
    projectsDir: typeof r.projectsDir === "string" ? r.projectsDir : undefined,
    uploadConsents: normalizeConsents(r.uploadConsents),
  };
}

function normalizeConsents(raw: unknown): Record<string, string> | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(raw)) if (typeof v === "string") out[k] = v;
  return Object.keys(out).length ? out : undefined;
}

export class FileSettings implements SettingsPort {
  constructor(
    private readonly fs: FileSystem,
    private readonly file: () => Promise<string>,
  ) {}
  async load() {
    const f = await this.file();
    if (!(await this.fs.exists(f))) return defaultSettings();
    try {
      return normalizeSettings(JSON.parse(await this.fs.readText(f)));
    } catch {
      return defaultSettings();
    }
  }
  async save(settings: AppSettings) {
    await this.fs.writeTextAtomic(await this.file(), JSON.stringify(settings, null, 2), { private: true });
  }
}

export class FileSecretStore implements SecretStorePort {
  constructor(
    private readonly fs: FileSystem,
    private readonly file: () => Promise<string>,
  ) {}
  private async all(): Promise<Record<string, string>> {
    const f = await this.file();
    if (!(await this.fs.exists(f))) return {};
    try {
      return JSON.parse(await this.fs.readText(f)) as Record<string, string>;
    } catch {
      return {};
    }
  }
  private async write(map: Record<string, string>) {
    await this.fs.writeTextAtomic(await this.file(), JSON.stringify(map), { private: true });
  }
  async get(ref: string) {
    return (await this.all())[ref] ?? null;
  }
  async set(ref: string, value: string) {
    await this.write({ ...(await this.all()), [ref]: value });
  }
  async delete(ref: string) {
    const { [ref]: _drop, ...rest } = await this.all();
    await this.write(rest);
  }
}

function safeStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export class LocalStorageSettings implements SettingsPort {
  private memory: AppSettings | null = null;
  constructor(private readonly key = "vvlog.settings") {}
  async load() {
    try {
      const raw = safeStorage()?.getItem(this.key);
      return raw ? normalizeSettings(JSON.parse(raw)) : (this.memory ?? defaultSettings());
    } catch {
      return this.memory ?? defaultSettings();
    }
  }
  async save(settings: AppSettings) {
    this.memory = settings;
    try {
      safeStorage()?.setItem(this.key, JSON.stringify(settings));
    } catch {
      // 保存できない環境ではメモリだけに持つ
    }
  }
}

export class LocalStorageSecretStore implements SecretStorePort {
  private memory = new Map<string, string>();
  constructor(private readonly prefix = "vvlog.secret.") {}
  async get(ref: string) {
    try {
      return safeStorage()?.getItem(this.prefix + ref) ?? this.memory.get(ref) ?? null;
    } catch {
      return this.memory.get(ref) ?? null;
    }
  }
  async set(ref: string, value: string) {
    this.memory.set(ref, value);
    try {
      safeStorage()?.setItem(this.prefix + ref, value);
    } catch {
      // メモリだけに持つ
    }
  }
  async delete(ref: string) {
    this.memory.delete(ref);
    try {
      safeStorage()?.removeItem(this.prefix + ref);
    } catch {
      // 何もしない
    }
  }
}
