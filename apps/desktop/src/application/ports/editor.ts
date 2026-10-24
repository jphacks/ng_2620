// EditorMediaPort：エディタが読む画像のデコード（F09）。キャッシュ（cache/<jobId>/）のファイルを、WebGL に渡せる形にする

export interface DecodedLabels {
  width: number;
  height: number;
  /** 遮蔽物ユニットのラベル（0 = なし、値 = unitId）。16bit PNG をそのまま */
  data: Uint16Array;
}

export interface EditorMediaPort {
  /** JPEG / WebP / PNG（8bit）→ ImageBitmap（プレマルチプライしない） */
  image(path: string): Promise<ImageBitmap>;
  /** 16bit グレースケール PNG（units/）→ ラベル配列。createImageBitmap では 8bit に丸められるので自前でデコードする */
  labels(path: string): Promise<DecodedLabels>;
}
