// 分割アップロード（ADR-0012：8MB チャンク、再開可能）

export const DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024;
export const UPLOAD_CONCURRENCY = 3;

export function chunkCount(size: number, chunkSize: number): number {
  return Math.max(1, Math.ceil(size / chunkSize));
}

/** index 番目の断片の位置。最後だけ短い */
export function chunkRange(index: number, size: number, chunkSize: number): { offset: number; length: number } {
  const offset = index * chunkSize;
  return { offset, length: Math.max(0, Math.min(chunkSize, size - offset)) };
}

/** まだ受信されていない断片（昇順） */
export function missingChunks(total: number, received: readonly number[]): number[] {
  const got = new Set(received);
  const out: number[] = [];
  for (let i = 0; i < total; i++) if (!got.has(i)) out.push(i);
  return out;
}

export function receivedBytes(received: readonly number[], size: number, chunkSize: number): number {
  const total = chunkCount(size, chunkSize);
  let sum = 0;
  for (const i of new Set(received)) if (i >= 0 && i < total) sum += chunkRange(i, size, chunkSize).length;
  return sum;
}
