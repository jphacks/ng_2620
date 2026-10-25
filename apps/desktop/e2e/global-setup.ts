// E2E の素材を作る：2 秒の縦動画（H.264 + AAC）と、最小の VRM（人型のボーンだけ持つ GLB）。run ごとに作り直す

import { execFileSync } from "node:child_process";
import { mkdirSync, rmSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { VRM_BONES } from "./strings";

export const RUN = fileURLToPath(new URL("./.run", import.meta.url));
export const ASSETS = `${RUN}/assets`;

export default function globalSetup() {
  // reuseExistingServer のときは、動いているサーバーが使っているフォルダを消さない（素材だけ作り直す）
  rmSync(ASSETS, { recursive: true, force: true });
  mkdirSync(ASSETS, { recursive: true });
  execFileSync(
    "ffmpeg",
    [
      "-v",
      "error",
      "-y",
      "-f",
      "lavfi",
      "-i",
      "testsrc2=size=360x640:rate=30:duration=2",
      "-f",
      "lavfi",
      "-i",
      "sine=frequency=440:duration=2",
      "-c:v",
      "libx264",
      "-pix_fmt",
      "yuv420p",
      "-c:a",
      "aac",
      "-shortest",
      `${ASSETS}/clip.mp4`,
    ],
    { stdio: "inherit" },
  );
  const python =
    process.env.VVLOG_E2E_PYTHON ??
    fileURLToPath(new URL("../../../services/gpu_server/.venv/bin/python", import.meta.url));
  const tests = fileURLToPath(new URL("../../../services/gpu_server/tests", import.meta.url));
  execFileSync(
    python,
    [
      "-c",
      `import sys; sys.path.insert(0, ${JSON.stringify(tests)}); from test_adapters import make_vrm; open(${JSON.stringify(`${ASSETS}/avatar.vrm`)}, "wb").write(make_vrm(bones=${JSON.stringify(VRM_BONES)}))`,
    ],
    { stdio: "inherit" },
  );
  // 『別の PC で作られた』プロジェクトの zip（元の動画が無い・送信前）。制限モードの zip 読み込みと動画の選び直しに使う
  execFileSync(
    python,
    [
      "-c",
      `import json, zipfile; p = {"schemaVersion": 1, "id": "loc_e2e_zip", "name": "他のPCのプロジェクト", "createdAt": "2026-10-24T00:00:00.000Z", "updatedAt": "2026-10-24T00:00:00.000Z", "source": {"path": "C:\\\\Users\\\\me\\\\clip.mp4", "copied": False, "durationSec": 2, "width": 360, "height": 640, "fps": 30, "hasAudio": True}, "compute": {"profileId": None}, "jobs": {"exports": []}, "meta": None, "persons": [], "cast": [], "avatars": {}, "ui": {"playhead": 0, "zoom": 1, "panelLayout": "default"}}; z = zipfile.ZipFile(${JSON.stringify(`${ASSETS}/project.vvlog.zip`)}, "w"); z.writestr("remote.vvlog/project.json", json.dumps(p, ensure_ascii=False)); z.writestr("remote.vvlog/overrides.json", json.dumps({"schemaVersion": 1, "items": []})); z.close()`,
    ],
    { stdio: "inherit" },
  );
}
