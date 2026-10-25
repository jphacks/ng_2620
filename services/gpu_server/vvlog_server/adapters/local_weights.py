"""重みのダウンロード(#33、ADR-0015)。weights/manifest.json の重み(約 7GB)を保存先に取得する。

  * huggingface_hub / torch に依存しない(環境 A で動く)。urllib で `<base>/<repo>/resolve/main/<file>` を取得する
  * `<dest>.part` に書き、Range で再開する。完了後に sha256 を検証し(固定値があれば)、`os.replace` で確定する
  * HF トークンは Authorization ヘッダーにだけ載せる。メモリ以外(ログ・エラー・ディスク・状態)には残さない。
    リダイレクト先が別ホスト(CDN)ならトークンは付けない。HF 以外の URL(source=url)にも付けない
  * 同時に走るのは 1 ジョブだけ。キャンセルしても .part は残し、次回そこから再開する
"""
from __future__ import annotations

import errno
import hashlib
import http.client
import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urljoin, urlsplit

from ..domain.errors import VvlogError

HF_BASE = "https://huggingface.co"
CHUNK = 1 << 20
MARKER = ".vvlog-weights.json"
MAX_HOPS = 5
# manifest に size が無い間の事前チェック用の目安(weights/README: 合計約 7GB)。実サイズは各ファイルの Content-Length で再確認する
NOMINAL_TOTAL_BYTES = 8 * 1024 ** 3
_BROKEN_STREAM = (OSError, TimeoutError, urllib.error.URLError, http.client.HTTPException)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # 3xx を例外として受け取り、こちらで Authorization を外して辿る
        return None


class DownloadError(Exception):
    def __init__(self, code: str, message: str, detail: dict | None = None, retriable: bool = False):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.detail, self.retriable = code, message, detail or {}, retriable


class Canceled(Exception):
    pass


def _sha256(path: Path, on_progress: Callable[[int], None] | None = None) -> str:
    h, done = hashlib.sha256(), 0
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(CHUNK), b""):
            h.update(b)
            done += len(b)
            if on_progress:
                on_progress(done)
    return h.hexdigest()


def _existing_parent(p: Path) -> Path:
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


class WeightsManager:
    chunk = CHUNK  # download read size (tests shrink it to observe progress)

    def __init__(self, manifest_path: Path, root: Path, *, hf_base: str = HF_BASE, reserve_bytes: int = 512 * 1024 ** 2,
                 nominal_total: int = NOMINAL_TOTAL_BYTES, disk_free: Callable[[Path], int] | None = None,
                 retries: int = 3, backoff_s: float = 1.0, timeout_s: float = 30.0):
        self.manifest_path, self.root = Path(manifest_path), Path(root)
        self.hf_base, self.reserve, self.nominal = hf_base.rstrip("/"), reserve_bytes, nominal_total
        self.retries, self.backoff, self.timeout = retries, backoff_s, timeout_s
        self._disk_free = disk_free or (lambda p: shutil.disk_usage(_existing_parent(p)).free)
        self._opener = urllib.request.build_opener(_NoRedirect)
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._job: dict = {"state": "idle"}

    # --- manifest / on-disk state ---------------------------------------------------------------------------------
    def manifest(self) -> dict:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def _dest(self, item: dict) -> Path:
        root = self.root.resolve()
        p = (root / item["dest"]).resolve()
        if not p.is_relative_to(root):
            raise VvlogError("VALIDATION_FAILED", "manifest の dest が保存先の外を指しています", {"dest": item["dest"]})
        return p

    def _records(self) -> dict:
        try:
            return json.loads((self.root / MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_record(self, item: dict, path: Path, digest: str) -> None:
        recs = self._records()
        st = path.stat()
        recs[item["dest"]] = {"size": st.st_size, "mtimeNs": st.st_mtime_ns, "sha256": digest}
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / (MARKER + ".tmp")
        tmp.write_text(json.dumps(recs), encoding="utf-8")
        os.replace(tmp, self.root / MARKER)

    def _file_state(self, item: dict, recs: dict) -> dict:
        p = self._dest(item)
        part = p.with_name(p.name + ".part")
        out = {"dest": item["dest"], "source": item["source"], "gated": bool(item.get("gated")),
               "license": item.get("license"), "pinned": bool(item.get("sha256")), "repo": item.get("repo"), "bytes": 0}
        if item.get("repo"):
            out["licenseUrl"] = f"{self.hf_base}/{item['repo']}"
        if p.is_file():
            st = p.stat()
            r = recs.get(item["dest"])
            known = r and r["size"] == st.st_size and r["mtimeNs"] == st.st_mtime_ns and \
                (not item.get("sha256") or r["sha256"] == item["sha256"])
            out.update(state="ok" if known else "unverified", bytes=st.st_size)
        elif part.is_file():
            out.update(state="partial", bytes=part.stat().st_size)
        else:
            out.update(state="missing")
        return out

    def status(self) -> dict:
        m = self.manifest()
        recs = self._records()
        files = [self._file_state(i, recs) for i in m["files"]]
        return {"root": str(self.root), "files": files, "allReady": all(f["state"] == "ok" for f in files),
                "freeBytes": self._disk_free(self.root),
                "needsToken": any(f["gated"] and f["state"] != "ok" for f in files),
                "externalCache": [c["repo"] for c in m.get("hf_cache", [])],  # transformers が HF キャッシュから読む分(本 API の対象外)
                "job": self.job()}

    def job(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._job))

    def _set(self, **kw) -> None:
        with self._lock:
            self._job.update(kw, updatedAt=time.time())

    # --- control ---------------------------------------------------------------------------------------------------
    def start(self, token: str | None) -> dict:
        token = (token or "").strip() or None
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise VvlogError("WEIGHTS_BUSY", "重みの取得はすでに実行中です")
        m = self.manifest()
        for i in m["files"]:
            self._dest(i)
        self.precheck_space()
        self._cancel.clear()
        with self._lock:
            self._job = {"state": "running", "phase": "preparing", "fileIndex": 0, "fileCount": len(m["files"]),
                         "file": None, "bytesDone": 0, "bytesTotal": None, "error": None, "unpinned": [],
                         "startedAt": time.time(), "updatedAt": time.time()}
            # トークンはスレッドの引数(メモリ)にだけ渡す。self には保持しない
            self._thread = threading.Thread(target=self._run, args=(m, token), name="weights", daemon=True)
            self._thread.start()
        return self.job()

    def cancel(self) -> dict:
        self._cancel.set()
        return self.job()

    def wait(self, timeout: float | None = None) -> None:
        t = self._thread
        if t:
            t.join(timeout)

    def precheck_space(self) -> None:
        """開始前の目安チェック(manifest に size が無いので、取得済み分を引いた合計の目安で見る)。"""
        have = sum(f["bytes"] for f in self.status()["files"])
        need = max(self.nominal - have, 0)
        free = self._disk_free(self.root)
        if free < need + self.reserve:
            raise VvlogError("WEIGHTS_DISK_FULL", f"ディスクの空きが足りません(目安 約 {(need + self.reserve) // 2 ** 30 + 1} GB 必要、"
                             f"空き {free // 2 ** 30} GB)。空けてからもう一度お試しください", {"needBytes": need, "freeBytes": free})

    # --- worker ----------------------------------------------------------------------------------------------------
    def _run(self, m: dict, token: str | None) -> None:
        try:
            for n, item in enumerate(m["files"]):
                self._set(fileIndex=n, file=item["dest"], bytesDone=0, bytesTotal=None, phase="checking")
                self._ensure(item, token)
            self._set(state="succeeded", phase="done", file=None, fileIndex=len(m["files"]))
        except Canceled:
            self._set(state="canceled", phase="canceled")
        except DownloadError as e:
            err = {"code": e.code, "message": e.message}
            if e.detail:
                err["detail"] = e.detail
            self._set(state="failed", phase="failed", error=err)
        except OSError as e:
            code = "WEIGHTS_DISK_FULL" if e.errno == errno.ENOSPC else "WEIGHTS_IO_ERROR"
            self._set(state="failed", phase="failed", error={"code": code, "message": "ファイルの書き込みに失敗しました"
                                                             + ("(ディスクの空き容量不足)" if code == "WEIGHTS_DISK_FULL" else "")})
        except Exception as e:  # noqa: BLE001 — 何が起きても failed にする。メッセージには型名だけ(例外文字列は出さない)
            self._set(state="failed", phase="failed", error={"code": "WEIGHTS_FAILED", "message": f"予期しないエラー: {type(e).__name__}"})

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise Canceled()

    def _verify(self, path: Path) -> str:
        self._set(phase="verifying", bytesDone=0, bytesTotal=path.stat().st_size)

        def progress(n: int) -> None:
            self._check_cancel()
            self._set(bytesDone=n)
        return _sha256(path, progress)

    def _ensure(self, item: dict, token: str | None) -> None:
        dest = self._dest(item)
        pin = item.get("sha256")
        if dest.is_file():
            if self._file_state(item, self._records())["state"] == "ok":
                return
            digest = self._verify(dest)
            if not pin or digest == pin:
                self._finish(item, dest, digest)
                return
            dest.unlink()  # 別物/壊れている -> 取り直す
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        for attempt in (1, 2):  # 検証失敗時は .part を消して 1 回だけ取り直す
            self._download(item, part, token)
            digest = self._verify(part)
            if not pin or digest == pin:
                os.replace(part, dest)
                self._finish(item, dest, digest)
                return
            part.unlink(missing_ok=True)
        raise DownloadError("WEIGHTS_CHECKSUM_MISMATCH", f"{item['dest']} の sha256 が一致しません(取り直しても同じ)。"
                            "取得元かネットワークを確認してください", {"file": item["dest"]})

    def _finish(self, item: dict, path: Path, digest: str) -> None:
        self._save_record(item, path, digest)
        if not item.get("sha256"):  # 固定されていない: 計算結果を報告する(manifest に固定する用)
            with self._lock:
                self._job["unpinned"] = [*self._job["unpinned"], {"dest": item["dest"], "sha256": digest}]

    # --- HTTP ------------------------------------------------------------------------------------------------------
    def _url(self, item: dict) -> tuple[str, bool]:
        """(url, トークンを付けてよいか)"""
        if item["source"] == "hf":
            return f"{self.hf_base}/{item['repo']}/resolve/main/{quote(item['filename'])}", True
        return item["url"], False

    def _open(self, url: str, token: str | None, start: int):
        """3xx は手で辿り、ホストが変わったら Authorization を外す。"""
        first_host = urlsplit(url).netloc
        for _ in range(MAX_HOPS + 1):
            headers = {"User-Agent": "vvlog-weights/1"}
            if token and urlsplit(url).netloc == first_host:
                headers["Authorization"] = f"Bearer {token}"
            if start:
                headers["Range"] = f"bytes={start}-"
            try:
                return self._opener.open(urllib.request.Request(url, headers=headers), timeout=self.timeout)
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                    url = urljoin(url, e.headers["Location"])
                    continue
                raise
        raise DownloadError("WEIGHTS_NETWORK", "リダイレクトが多すぎます")

    def _http_error(self, e: urllib.error.HTTPError, item: dict, token: str | None) -> DownloadError:
        repo = item.get("repo")
        if e.code in (401, 403) and item["source"] == "hf":
            link = f"{self.hf_base}/{repo}"
            if not token:
                return DownloadError("HF_TOKEN_REQUIRED", f"{repo} の取得には Hugging Face のアクセストークンが必要です。"
                                     f"{link} でアクセスを申請し、トークン(read 権限)を入力してください", {"repo": repo, "url": link})
            if e.code == 401:
                return DownloadError("HF_TOKEN_INVALID", "Hugging Face のトークンが無効です。入力し直してください", {"repo": repo})
            return DownloadError("HF_ACCESS_DENIED", f"このトークンのアカウントは {repo} へのアクセスが許可されていません。"
                                 f"{link} でライセンスに同意し、承認されてからもう一度お試しください", {"repo": repo, "url": link})
        if e.code == 404:
            return DownloadError("WEIGHTS_NOT_FOUND", f"{item['dest']} の取得元が見つかりません(404)", {"file": item["dest"]})
        return DownloadError("WEIGHTS_NETWORK", f"取得元がエラーを返しました(HTTP {e.code})", {"status": e.code},
                             retriable=e.code >= 500 or e.code == 429)

    def _download(self, item: dict, part: Path, token: str | None) -> None:
        url, may_auth = self._url(item)
        token = token if may_auth else None
        failures, reset_done = 0, False

        def broken(msg: str) -> None:
            nonlocal failures
            failures += 1
            if failures > self.retries:
                raise DownloadError("WEIGHTS_NETWORK", msg)
            self._sleep()

        while True:
            self._check_cancel()
            have = part.stat().st_size if part.is_file() else 0
            try:
                resp = self._open(url, token, have)
            except urllib.error.HTTPError as e:
                if e.code == 416 and have and not reset_done:  # .part が実体より大きい: 最初から
                    part.unlink(missing_ok=True)
                    reset_done = True
                    continue
                err = self._http_error(e, item, token)
                if err.retriable and failures < self.retries:
                    broken("")
                    continue
                raise err from None
            except _BROKEN_STREAM:
                broken("ネットワークに接続できません。つながってから「再開」してください(途中まで保存してあります)")
                continue
            with resp:
                length = resp.headers.get("Content-Length", "")
                if resp.status == 206:
                    try:
                        cr = resp.headers.get("Content-Range", "")  # bytes START-END/TOTAL
                        start, total = int(cr.split()[1].split("-")[0]), int(cr.rsplit("/", 1)[1])
                    except (IndexError, ValueError):
                        start, total = have, (have + int(length) if length.isdigit() else None)
                    if start != have:
                        part.unlink(missing_ok=True)
                        continue
                    mode = "ab"
                else:  # 200: Range を無視された。最初から
                    have, mode = 0, "wb"
                    total = int(length) if length.isdigit() else None
                if total is not None:
                    self._check_space(part, total - have)
                self._set(phase="downloading", bytesDone=have, bytesTotal=total)
                try:
                    with open(part, mode) as f:
                        while True:
                            self._check_cancel()
                            b = resp.read(self.chunk)
                            if not b:
                                break
                            f.write(b)
                            have += len(b)
                            self._set(bytesDone=have)
                except _BROKEN_STREAM as e:
                    if isinstance(e, OSError) and e.errno == errno.ENOSPC:
                        raise DownloadError("WEIGHTS_DISK_FULL", "ディスクの空き容量が足りません。空けてから再開してください") from None
                    broken("ダウンロードが途中で切れました。「再開」で続きから取得できます")
                    continue
            if total is not None and have < total:  # 相手に早く閉じられた
                broken("ダウンロードが途中で切れました。「再開」で続きから取得できます")
                continue
            return

    def _sleep(self) -> None:
        end = time.monotonic() + self.backoff
        while time.monotonic() < end:
            self._check_cancel()
            time.sleep(min(0.05, self.backoff))

    def _check_space(self, part: Path, need: int) -> None:
        free = self._disk_free(part.parent)
        if free < need + self.reserve:
            raise DownloadError("WEIGHTS_DISK_FULL", f"ディスクの空き容量が足りません(必要 約 {(need + self.reserve) // 2 ** 20} MB、"
                                f"空き {free // 2 ** 20} MB)。空けてから再開してください", {"needBytes": need, "freeBytes": free})
