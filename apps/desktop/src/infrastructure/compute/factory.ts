// ComputeBackendFactory：接続プロファイル → 実装。memory:// はシミュレーター、それ以外は HTTP

import type { ComputeBackendFactory, ComputeBackendPort } from "@/application/ports/compute";
import type { SecretStorePort } from "@/application/ports/settings";
import type { ComputeProfile } from "@/domain/connection";
import { HttpComputeBackend } from "./HttpComputeBackend";
import type { HttpTransport } from "./HttpComputeBackend";
import { OfflineComputeBackend } from "./OfflineComputeBackend";

export class DefaultComputeBackendFactory implements ComputeBackendFactory {
  private readonly cache = new Map<string, ComputeBackendPort>();
  private readonly offlineBackend = new OfflineComputeBackend();

  constructor(
    private readonly d: {
      secrets: SecretStorePort;
      transport: HttpTransport;
      simulator?: () => ComputeBackendPort;
    },
  ) {}

  forProfile(profile: ComputeProfile, opts?: { token?: string | null }): ComputeBackendPort {
    if (profile.baseUrl.startsWith("memory://")) {
      if (!this.d.simulator) return this.offlineBackend;
      return this.d.simulator();
    }
    const make = (getToken: () => Promise<string | null>) =>
      new HttpComputeBackend({
        baseUrl: profile.baseUrl,
        profileId: profile.id,
        getToken,
        transport: this.d.transport,
      });
    if (opts && opts.token !== undefined) return make(async () => opts.token ?? null);
    const key = `${profile.id}|${profile.baseUrl}|${profile.tokenRef}`;
    let b = this.cache.get(key);
    if (!b) {
      b = make(() => this.d.secrets.get(profile.tokenRef));
      this.cache.set(key, b);
    }
    return b;
  }

  offline(): ComputeBackendPort {
    return this.offlineBackend;
  }
}
