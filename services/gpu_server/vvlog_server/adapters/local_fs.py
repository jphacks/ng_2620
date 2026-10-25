"""Local file access for the browser UI (port of src-tauri/src/commands/fs.rs, #23).

Unlike Tauri's in-process commands, this is reachable over HTTP, so every path is checked against an explicit set of
allowed roots (project folder, app data, config, plus folders the user grants). A path is resolved with realpath first,
so `..`, symlinks and Windows case differences cannot step outside a root.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import stat as stat_mod
import zipfile
from pathlib import Path
from typing import Iterator

from ..domain.errors import VvlogError

MAX_UNZIP_BYTES = 64 << 30


def _norm(p: str) -> str:
    return os.path.normcase(os.path.realpath(p))


def _within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # different drives
        return False


class LocalRoots:
    """What /v1/local/* may touch. Fixed roots come from the settings; the user widens them only by picking something in
    a native dialog (grant_user), which is remembered across restarts in `store` (outside every root, so the browser
    cannot edit it through the file API)."""

    MAX_FILES = 500

    def __init__(self, roots=(), store: Path | None = None):
        self._roots: list[str] = []
        self._user_dirs: list[str] = []
        self._user_files: list[str] = []
        self._store = store
        for r in roots:
            self.grant(r)
        self._load()

    def grant(self, path: str | Path) -> None:
        r = _norm(str(path))
        if r not in self._roots:
            self._roots.append(r)

    def grant_user(self, path: str | Path, *, is_dir: bool) -> None:
        """The user picked this in a native dialog: a folder becomes a root, a file is allowed on its own."""
        r = _norm(str(path))
        bucket = self._user_dirs if is_dir else self._user_files
        if r in bucket:
            bucket.remove(r)
        bucket.append(r)
        del self._user_files[: -self.MAX_FILES]
        self._save()

    def list(self) -> list[str]:
        return [*self._roots, *self._user_dirs]

    def _load(self) -> None:
        if not self._store or not self._store.is_file():
            return
        try:
            d = json.loads(self._store.read_text(encoding="utf-8"))
            self._user_dirs = [_norm(p) for p in d.get("dirs", []) if isinstance(p, str)]
            self._user_files = [_norm(p) for p in d.get("files", []) if isinstance(p, str)][-self.MAX_FILES:]
        except (OSError, ValueError):
            pass  # a broken grants file just means nothing is remembered

    def _save(self) -> None:
        if not self._store:
            return
        w = AtomicWriter(self._store, private=True)
        try:
            w.write(json.dumps({"dirs": self._user_dirs, "files": self._user_files}, ensure_ascii=False).encode())
            w.commit()
        except BaseException:
            w.abort()
            raise

    def resolve(self, raw: str, *, forbid_root: bool = False) -> Path:
        """The real path of `raw`, or PATH_NOT_ALLOWED. forbid_root: the root folders themselves are off limits too."""
        if not raw or "\x00" in raw or not os.path.isabs(raw):
            raise VvlogError("PATH_NOT_ALLOWED", "絶対パスを指定してください", {"path": raw})
        real = _norm(raw)
        if real in self._user_files:  # picked by the user: that very file, nothing around it
            return Path(os.path.realpath(raw))
        roots = self.list()
        if not any(_within(real, r) for r in roots):
            raise VvlogError("PATH_NOT_ALLOWED", "許可されていない場所です", {"path": raw})
        if forbid_root and any(real == r or _within(r, real) for r in roots):
            raise VvlogError("PATH_NOT_ALLOWED", "許可フォルダそのものは操作できません", {"path": raw})
        return Path(os.path.realpath(raw))


@contextlib.contextmanager
def os_errors(path: str = "") -> Iterator[None]:
    try:
        yield
    except (FileNotFoundError, NotADirectoryError) as e:
        raise VvlogError("NOT_FOUND", "ファイルが見つかりません", {"path": path}) from e
    except (FileExistsError, IsADirectoryError) as e:
        raise VvlogError("FS_CONFLICT", str(e.strerror or e), {"path": path}) from e
    except PermissionError as e:
        raise VvlogError("FS_FAILED", f"アクセスが拒否されました: {e.strerror or e}", {"path": path}) from e
    except OSError as e:
        raise VvlogError("FS_FAILED", f"{e.strerror or e}", {"path": path, "errno": e.errno}) from e


def _set_private(p: Path) -> None:
    if os.name != "nt":
        os.chmod(p, 0o600)


class AtomicWriter:
    """Writes to a temp file next to the target; commit() fsyncs and renames over it (ADR-0007). os.replace also
    replaces an existing file on Windows."""

    def __init__(self, target: Path, *, private: bool = False):
        target.parent.mkdir(parents=True, exist_ok=True)
        self.target, self.private = target, private
        self.tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}-{id(self):x}")
        self._f = open(self.tmp, "wb")

    def write(self, data: bytes) -> None:
        with os_errors(str(self.target)):
            self._f.write(data)

    def commit(self) -> None:
        with os_errors(str(self.target)):
            self._f.flush()
            os.fsync(self._f.fileno())
            self._f.close()
            if self.private:
                _set_private(self.tmp)
            os.replace(self.tmp, self.target)

    def abort(self) -> None:
        with contextlib.suppress(OSError):
            self._f.close()
            self.tmp.unlink()


class LocalFs:
    def __init__(self, roots: LocalRoots):
        self.roots = roots

    # --- reads --------------------------------------------------------------------------------------------------
    def exists(self, path: str) -> bool:
        return self.roots.resolve(path).exists()

    def stat(self, path: str) -> dict:
        p = self.roots.resolve(path)
        with os_errors(path):
            m = p.stat()
        return {"size": m.st_size, "mtimeMs": m.st_mtime * 1000, "isDir": stat_mod.S_ISDIR(m.st_mode)}

    def read_text(self, path: str) -> str:
        p = self.roots.resolve(path)
        with os_errors(path):
            return p.read_text(encoding="utf-8")

    def file_path(self, path: str) -> Path:
        """For streaming reads (Range). Must be an existing regular file."""
        p = self.roots.resolve(path)
        if not p.is_file():
            raise VvlogError("NOT_FOUND", "ファイルが見つかりません", {"path": path})
        return p

    def list_dir(self, path: str) -> list[str]:
        p = self.roots.resolve(path)
        if not p.exists():
            return []
        with os_errors(path):
            return [e.name for e in os.scandir(p)]

    # --- writes -------------------------------------------------------------------------------------------------
    def write_text(self, path: str, text: str, private: bool = False) -> None:
        w = self.begin_write(path, private=private)
        try:
            w.write(text.encode("utf-8"))
            w.commit()
        except BaseException:
            w.abort()
            raise

    def begin_write(self, path: str, *, private: bool = False) -> AtomicWriter:
        p = self.roots.resolve(path, forbid_root=True)
        with os_errors(path):
            return AtomicWriter(p, private=private)

    def append(self, path: str, data: bytes, expected_size: int | None = None) -> int:
        """Appends to a file (creating it). With expected_size the call fails with FS_CONFLICT unless the file is
        exactly that long, so a resumed or repeated chunk cannot corrupt a download. Returns the new size."""
        p = self.roots.resolve(path, forbid_root=True)
        with os_errors(path):
            p.parent.mkdir(parents=True, exist_ok=True)
            size = p.stat().st_size if p.exists() else 0
            if expected_size is not None and size != expected_size:
                raise VvlogError("FS_CONFLICT", "ファイルの大きさが想定と違います", {"path": path, "size": size})
            with open(p, "ab") as f:
                f.write(data)
            return size + len(data)

    def rename(self, src: str, dest: str) -> None:
        """Atomic move within the allowed roots; replaces an existing file (finishing a .part download)."""
        s, d = self.roots.resolve(src, forbid_root=True), self.roots.resolve(dest, forbid_root=True)
        with os_errors(src):
            d.parent.mkdir(parents=True, exist_ok=True)
            os.replace(s, d)

    def copy_file(self, src: str, dest: str) -> None:
        s, d = self.roots.resolve(src), self.roots.resolve(dest, forbid_root=True)
        with os_errors(src):
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(s, d)

    def ensure_dir(self, path: str) -> None:
        p = self.roots.resolve(path)
        with os_errors(path):
            p.mkdir(parents=True, exist_ok=True)

    def remove(self, path: str) -> None:
        p = self.roots.resolve(path, forbid_root=True)
        with os_errors(path):
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            elif p.exists() or p.is_symlink():
                p.unlink()

    def move_to_trash(self, path: str) -> None:
        import send2trash
        p = self.roots.resolve(path, forbid_root=True)
        if not p.exists():
            raise VvlogError("NOT_FOUND", "ファイルが見つかりません", {"path": path})
        with os_errors(path):
            send2trash.send2trash(str(p))

    def unzip(self, zip_path: str, dest_dir: str, max_bytes: int = MAX_UNZIP_BYTES) -> None:
        """Extracts a zip (store or deflate) into dest_dir. Entries that would land outside it (zip-slip), absolute
        names and symlinks are refused before anything is written."""
        z, d = self.roots.resolve(zip_path), self.roots.resolve(dest_dir)
        dest = str(d)  # real path, original case (it is what we create files under)
        try:
            with os_errors(zip_path):
                zf = zipfile.ZipFile(z)
        except zipfile.BadZipFile as e:
            raise VvlogError("VALIDATION_FAILED", "zip が壊れています", {"path": zip_path}) from e
        with zf:
            plan: list[tuple[zipfile.ZipInfo, str]] = []
            total = 0
            for m in zf.infolist():
                name = m.filename.replace("\\", "/")
                if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
                    raise VvlogError("VALIDATION_FAILED", "zip に絶対パスの項目があります", {"entry": m.filename})
                if stat_mod.S_ISLNK(m.external_attr >> 16):
                    raise VvlogError("VALIDATION_FAILED", "zip にシンボリックリンクがあります", {"entry": m.filename})
                target = os.path.realpath(os.path.join(dest, *name.split("/")))
                if _norm(target) != _norm(dest) and not _within(_norm(target), _norm(dest)):
                    raise VvlogError("VALIDATION_FAILED", "zip の項目が展開先の外を指しています", {"entry": m.filename})
                total += m.file_size
                plan.append((m, target))
            if total > max_bytes:
                raise VvlogError("VALIDATION_FAILED", "zip の展開後サイズが大きすぎます", {"bytes": total})
            with os_errors(dest_dir):
                os.makedirs(dest, exist_ok=True)
                for m, target in plan:
                    if m.is_dir():
                        os.makedirs(target, exist_ok=True)
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(m) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out, 1 << 20)
