// WebGL2 のプレビュー合成（F09、contracts §5）。domain/composite.ts の compositeFrame と同じ式をシェーダーで行う。
// 人物は最大 4 人。テクスチャは毎フレーム「奥 → 手前」の順でスロット 0..3 に割り当てる（GLSL ES 3.0 は
// サンプラー配列を可変の添字で引けないので、並べ替えは CPU 側で行う）。
// 手修正は overrideTable（R8UI、横 = unitId、縦 = 人物の行）で渡す：0 = なし、1 = 前、2 = 後ろ

export const MAX_PERSONS = 4;

export interface FrameTextures {
  frame: TexImageSource;
  clean: TexImageSource;
  /** 奥 → 手前の順。row は overrideTable の行（人物ごとに固定） */
  persons: { row: number; avatar: TexImageSource; hide: TexImageSource | null }[];
  units: { width: number; height: number; data: Uint16Array } | null;
  mask: TexImageSource | null;
}

import type { ViewMode } from "@/presentation/shared/stores/editorStore";

export type { ViewMode };

export interface RenderOptions {
  view: ViewMode;
  /** split の境界（0..1、左が合成） */
  split: number;
  showAvatar: boolean;
  showUnits: boolean;
  showHide: boolean;
  showMask: boolean;
  hoverUnit: number;
  selectedUnit: number;
  /** Alt 押下中など：ホバー中のユニットを強調する */
  highlightHover: boolean;
}

const VS = `#version 300 es
out vec2 vUv;
void main() {
  vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
  vUv = p;
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`;

const FS = `#version 300 es
precision highp float;
precision highp int;
precision highp usampler2D;
in vec2 vUv;
out vec4 outColor;
uniform sampler2D uFrame, uClean, uMask;
uniform sampler2D uAvatar0, uAvatar1, uAvatar2, uAvatar3;
uniform sampler2D uHide0, uHide1, uHide2, uHide3;
uniform usampler2D uUnits, uModes;
uniform int uCount;
uniform ivec4 uRow;
uniform ivec4 uHasHide;
uniform int uHasUnits, uHasMask;
uniform ivec2 uSize;
uniform int uView;
uniform float uSplit;
uniform int uShowAvatar, uShowUnits, uShowHide, uShowMask;
uniform int uHover, uSelected, uHighlightHover;
uniform vec3 uPersonColor[4];

int unitAt(ivec2 px) {
  if (uHasUnits == 0) return 0;
  return int(texelFetch(uUnits, clamp(px, ivec2(0), uSize - 1), 0).r);
}

int modeOf(int row, int unit) {
  if (unit == 0) return 0;
  ivec2 ts = textureSize(uModes, 0);
  if (unit >= ts.x || row >= ts.y) return 0;
  return int(texelFetch(uModes, ivec2(unit, row), 0).r);
}

bool effHide(vec4 av, float hv, int row, int unit) {
  bool h = hv >= 0.5;
  int m = modeOf(row, unit);
  if (m == 1) h = av.a > 0.0;
  else if (m == 2) h = false;
  return h;
}

vec3 hueOf(int u) {
  float h = fract(float(u) * 0.61803398875);
  vec3 k = vec3(1.0, 2.0 / 3.0, 1.0 / 3.0);
  return clamp(abs(fract(h + k) * 6.0 - 3.0) - 1.0, 0.0, 1.0) * 0.85 + 0.15;
}

void layer(inout vec3 outc, inout bool anyHide, vec4 av, float hv, int row, int unit, vec3 frame) {
  if (av.a <= 0.0) return;
  bool h = effHide(av, hv, row, unit);
  anyHide = anyHide || h;
  vec3 c = h ? frame : av.rgb;
  outc = outc * (1.0 - av.a) + c * av.a;
}

void main() {
  vec2 uv = vec2(vUv.x, 1.0 - vUv.y);
  ivec2 px = ivec2(uv * vec2(uSize));
  vec3 frame = texture(uFrame, uv).rgb;
  bool original = uView == 1 || (uView == 2 && uv.x > uSplit);
  vec3 outc = texture(uClean, uv).rgb;
  int unit = unitAt(px);
  bool anyHide = false;
  if (original) {
    outc = frame;
  } else if (uShowAvatar == 1) {
    if (uCount > 0) layer(outc, anyHide, texture(uAvatar0, uv), uHasHide.x == 1 ? texture(uHide0, uv).r : 0.0, uRow.x, unit, frame);
    if (uCount > 1) layer(outc, anyHide, texture(uAvatar1, uv), uHasHide.y == 1 ? texture(uHide1, uv).r : 0.0, uRow.y, unit, frame);
    if (uCount > 2) layer(outc, anyHide, texture(uAvatar2, uv), uHasHide.z == 1 ? texture(uHide2, uv).r : 0.0, uRow.z, unit, frame);
    if (uCount > 3) layer(outc, anyHide, texture(uAvatar3, uv), uHasHide.w == 1 ? texture(uHide3, uv).r : 0.0, uRow.w, unit, frame);
  }
  if (uShowMask == 1 && uHasMask == 1) {
    int pid = int(texture(uMask, uv).r * 255.0 + 0.5);
    if (pid >= 1 && pid <= 4) outc = mix(outc, uPersonColor[pid - 1], 0.4);
  }
  if (uShowHide == 1 && anyHide) outc = mix(outc, vec3(1.0, 0.15, 0.15), 0.45);
  if (unit != 0) {
    bool hov = unit == uHover && (uShowUnits == 1 || uHighlightHover == 1);
    bool sel = unit == uSelected;
    if (uShowUnits == 1) outc = mix(outc, hueOf(unit), sel ? 0.45 : 0.28);
    if (hov && !sel) outc = mix(outc, vec3(1.0), 0.25);
    if (sel) {
      bool edge = unitAt(px + ivec2(1, 0)) != unit || unitAt(px - ivec2(1, 0)) != unit ||
                  unitAt(px + ivec2(0, 1)) != unit || unitAt(px - ivec2(0, 1)) != unit;
      if (edge) outc = vec3(0.31, 0.55, 1.0);
    }
  }
  if (uView == 2 && abs(uv.x - uSplit) * float(uSize.x) < 1.0) outc = vec3(1.0);
  outColor = vec4(outc, 1.0);
}`;

const PERSON_COLORS: [number, number, number][] = [
  [79, 140, 255],
  [240, 136, 62],
  [63, 185, 80],
  [219, 97, 162],
];

function compile(gl: WebGL2RenderingContext, type: number, src: string): WebGLShader {
  const s = gl.createShader(type)!;
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(`shader: ${gl.getShaderInfoLog(s)}`);
  return s;
}

export class Compositor {
  readonly gl: WebGL2RenderingContext;
  private readonly prog: WebGLProgram;
  private readonly tex = new Map<string, WebGLTexture>();
  private readonly loc = new Map<string, WebGLUniformLocation | null>();
  private size: [number, number] = [1, 1];
  private count = 0;
  private rows: [number, number, number, number] = [0, 0, 0, 0];
  private hasHide: [number, number, number, number] = [0, 0, 0, 0];
  private hasUnits = 0;
  private hasMask = 0;

  constructor(canvas: HTMLCanvasElement | OffscreenCanvas) {
    const gl = canvas.getContext("webgl2", {
      premultipliedAlpha: false,
      preserveDrawingBuffer: true,
      antialias: false,
    }) as WebGL2RenderingContext | null;
    if (!gl) throw new Error("WebGL2 が使えません");
    this.gl = gl;
    const p = gl.createProgram()!;
    gl.attachShader(p, compile(gl, gl.VERTEX_SHADER, VS));
    gl.attachShader(p, compile(gl, gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(`program: ${gl.getProgramInfoLog(p)}`);
    this.prog = p;
    gl.useProgram(p);
    gl.bindVertexArray(gl.createVertexArray());
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    const samplers = [
      "uFrame",
      "uClean",
      "uMask",
      "uAvatar0",
      "uAvatar1",
      "uAvatar2",
      "uAvatar3",
      "uHide0",
      "uHide1",
      "uHide2",
      "uHide3",
      "uUnits",
      "uModes",
    ];
    samplers.forEach((name, unit) => {
      gl.uniform1i(this.u(name), unit);
      this.texture(name, name === "uUnits" || name === "uModes");
    });
    this.uploadModes(new Uint8Array([0]), 1, 1);
    this.uploadUnits(null);
    gl.uniform3fv(
      this.u("uPersonColor"),
      PERSON_COLORS.flatMap((c) => c.map((v) => v / 255)),
    );
  }

  private u(name: string): WebGLUniformLocation | null {
    if (!this.loc.has(name)) this.loc.set(name, this.gl.getUniformLocation(this.prog, name));
    return this.loc.get(name)!;
  }

  private texture(name: string, integer = false): WebGLTexture {
    let t = this.tex.get(name);
    if (!t) {
      const gl = this.gl;
      t = gl.createTexture()!;
      gl.bindTexture(gl.TEXTURE_2D, t);
      const filter = integer ? gl.NEAREST : gl.LINEAR;
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      if (!integer) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array(4));
      this.tex.set(name, t);
    }
    return t;
  }

  private bind(name: string, unit: number) {
    const gl = this.gl;
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(gl.TEXTURE_2D, this.texture(name));
  }

  private uploadImage(name: string, unit: number, src: TexImageSource) {
    const gl = this.gl;
    this.bind(name, unit);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, src);
  }

  private uploadUnits(units: FrameTextures["units"]) {
    const gl = this.gl;
    this.bind("uUnits", 11);
    if (units)
      gl.texImage2D(
        gl.TEXTURE_2D,
        0,
        gl.R16UI,
        units.width,
        units.height,
        0,
        gl.RED_INTEGER,
        gl.UNSIGNED_SHORT,
        units.data,
      );
    else gl.texImage2D(gl.TEXTURE_2D, 0, gl.R16UI, 1, 1, 0, gl.RED_INTEGER, gl.UNSIGNED_SHORT, new Uint16Array(1));
    this.hasUnits = units ? 1 : 0;
  }

  /** overrideTable：table[row * width + unitId] = 0 / 1（前）/ 2（後ろ） */
  uploadModes(table: Uint8Array, width: number, height: number) {
    const gl = this.gl;
    this.bind("uModes", 12);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8UI, width, height, 0, gl.RED_INTEGER, gl.UNSIGNED_BYTE, table);
  }

  /** 1 フレーム分のテクスチャを差し替える */
  setFrame(t: FrameTextures, width: number, height: number) {
    this.size = [width, height];
    this.uploadImage("uFrame", 0, t.frame);
    this.uploadImage("uClean", 1, t.clean);
    if (t.mask) this.uploadImage("uMask", 2, t.mask);
    this.hasMask = t.mask ? 1 : 0;
    const persons = t.persons.slice(0, MAX_PERSONS);
    this.count = persons.length;
    persons.forEach((p, k) => {
      this.uploadImage(`uAvatar${k}`, 3 + k, p.avatar);
      if (p.hide) this.uploadImage(`uHide${k}`, 7 + k, p.hide);
      this.rows[k] = p.row;
      this.hasHide[k] = p.hide ? 1 : 0;
    });
    this.uploadUnits(t.units);
  }

  render(o: RenderOptions) {
    const gl = this.gl;
    const [w, h] = this.size;
    const canvas = gl.canvas;
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    gl.viewport(0, 0, w, h);
    gl.useProgram(this.prog);
    const names = [
      "uFrame",
      "uClean",
      "uMask",
      "uAvatar0",
      "uAvatar1",
      "uAvatar2",
      "uAvatar3",
      "uHide0",
      "uHide1",
      "uHide2",
      "uHide3",
      "uUnits",
      "uModes",
    ];
    names.forEach((n, unit) => this.bind(n, unit));
    gl.uniform1i(this.u("uCount"), this.count);
    gl.uniform4i(this.u("uRow"), ...this.rows);
    gl.uniform4i(this.u("uHasHide"), ...this.hasHide);
    gl.uniform1i(this.u("uHasUnits"), this.hasUnits);
    gl.uniform1i(this.u("uHasMask"), this.hasMask);
    gl.uniform2i(this.u("uSize"), w, h);
    gl.uniform1i(this.u("uView"), o.view === "composite" ? 0 : o.view === "original" ? 1 : 2);
    gl.uniform1f(this.u("uSplit"), o.split);
    gl.uniform1i(this.u("uShowAvatar"), o.showAvatar ? 1 : 0);
    gl.uniform1i(this.u("uShowUnits"), o.showUnits ? 1 : 0);
    gl.uniform1i(this.u("uShowHide"), o.showHide ? 1 : 0);
    gl.uniform1i(this.u("uShowMask"), o.showMask ? 1 : 0);
    gl.uniform1i(this.u("uHover"), o.hoverUnit);
    gl.uniform1i(this.u("uSelected"), o.selectedUnit);
    gl.uniform1i(this.u("uHighlightHover"), o.highlightHover ? 1 : 0);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  /** 描いた画像（上が 0 行目の RGBA）。一致テスト用 */
  readPixels(): Uint8Array {
    const gl = this.gl;
    const [w, h] = this.size;
    const buf = new Uint8Array(w * h * 4);
    gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, buf);
    const out = new Uint8Array(buf.length);
    for (let y = 0; y < h; y++) out.set(buf.subarray((h - 1 - y) * w * 4, (h - y) * w * 4), y * w * 4);
    return out;
  }

  dispose() {
    const gl = this.gl;
    this.tex.forEach((t) => gl.deleteTexture(t));
    gl.deleteProgram(this.prog);
  }
}

/** フレーム f の overrideTable（rows = 人物の並び、resolveModes の結果を詰める） */
export function buildModeTable(
  rows: readonly number[],
  maxUnit: number,
  modeOf: (personId: number, unitId: number) => "front" | "behind" | null,
): { table: Uint8Array; width: number; height: number } {
  const width = Math.max(1, maxUnit + 1);
  const height = Math.max(1, rows.length);
  const table = new Uint8Array(width * height);
  rows.forEach((pid, r) => {
    for (let u = 1; u <= maxUnit; u++) {
      const m = modeOf(pid, u);
      if (m) table[r * width + u] = m === "front" ? 1 : 2;
    }
  });
  return { table, width, height };
}
