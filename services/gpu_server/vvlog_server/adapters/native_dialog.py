"""Native file / folder / save dialogs shown on the machine that runs the local app server (#46).

The browser cannot hand a real path to the app, so the server asks the OS (ADR-0015, decision in #28). Only Windows is
implemented (PowerShell + System.Windows.Forms); the dialog is parented to an invisible top-most window so it does not
hide behind the browser. Arguments travel in an environment variable, never inside the script text.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import threading
from typing import Protocol

from ..domain.errors import VvlogError

TIMEOUT_S = 600
KINDS = ("open-files", "open-dir", "save")
_EXT = re.compile(r"^[A-Za-z0-9]{1,10}$")

_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$a = $env:VVLOG_DIALOG_ARGS | ConvertFrom-Json
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true; $owner.ShowInTaskbar = $false; $owner.Opacity = 0
$owner.StartPosition = 'CenterScreen'; $owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.Show(); $owner.Activate()
function Get-Filter($fs) {
  if (-not $fs) { return 'All files (*.*)|*.*' }
  (@($fs) | ForEach-Object { $e = (@($_.extensions) | ForEach-Object { '*.' + $_ }) -join ';'; "$($_.name) ($e)|$e" }) -join '|'
}
function Set-Start($d, $path) {
  if (-not $path) { return }
  if (Test-Path -LiteralPath $path -PathType Container) { $d.InitialDirectory = $path }
  else { $d.InitialDirectory = Split-Path -Parent $path; $d.FileName = Split-Path -Leaf $path }
}
$paths = @()
try {
  switch ($a.kind) {
    'open-files' {
      $d = New-Object System.Windows.Forms.OpenFileDialog
      $d.Multiselect = [bool]$a.multiple; $d.Filter = Get-Filter $a.filters; $d.CheckFileExists = $true
      Set-Start $d $a.defaultPath
      if ($a.title) { $d.Title = $a.title }
      if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { $paths = @($d.FileNames) }
    }
    'save' {
      $d = New-Object System.Windows.Forms.SaveFileDialog
      $d.Filter = Get-Filter $a.filters; $d.OverwritePrompt = $true
      Set-Start $d $a.defaultPath
      if ($a.title) { $d.Title = $a.title }
      if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { $paths = @($d.FileName) }
    }
    'open-dir' {
      $d = New-Object System.Windows.Forms.FolderBrowserDialog
      if ($a.defaultPath -and (Test-Path -LiteralPath $a.defaultPath -PathType Container)) { $d.SelectedPath = $a.defaultPath }
      if ($a.title) { $d.Description = $a.title }
      if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { $paths = @($d.SelectedPath) }
    }
  }
} finally { $owner.Close() }
ConvertTo-Json -Compress -InputObject @{ paths = [string[]]$paths }
"""


class DialogBackend(Protocol):
    def run(self, request: dict, timeout: float) -> list[str]:
        """The picked paths; an empty list when the user cancels (or the dialog times out)."""


def build_request(kind: str, *, filters=None, multiple: bool = False, default_path: str | None = None,
                  title: str | None = None) -> dict:
    """Validated, script-safe arguments. Extensions are plain alphanumerics; names lose the characters the Windows
    filter syntax uses."""
    if kind not in KINDS:
        raise VvlogError("VALIDATION_FAILED", "未知のダイアログです", {"kind": kind})
    fs = []
    for f in filters or []:
        exts = [e.lstrip(".") for e in f.get("extensions", [])]
        if not exts or not all(_EXT.match(e) for e in exts):
            raise VvlogError("VALIDATION_FAILED", "拡張子の指定が不正です", {"extensions": f.get("extensions")})
        fs.append({"name": re.sub(r"[|;()*]", " ", f.get("name", "")).strip() or "Files", "extensions": exts})
    return {"kind": kind, "filters": fs, "multiple": bool(multiple), "defaultPath": (default_path or "")[:1024],
            "title": re.sub(r"[\r\n]", " ", title or "")[:100]}


class WindowsDialogBackend:
    def __init__(self, powershell: str = "powershell.exe"):
        self.powershell = powershell

    def run(self, request: dict, timeout: float) -> list[str]:
        encoded = base64.b64encode(_PS_SCRIPT.encode("utf-16-le")).decode("ascii")
        cmd = [self.powershell, "-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy", "Bypass",
               "-EncodedCommand", encoded]
        env = {**os.environ, "VVLOG_DIALOG_ARGS": json.dumps(request, ensure_ascii=False)}
        try:
            r = subprocess.run(cmd, env=env, capture_output=True, timeout=timeout,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return []
        except OSError as e:
            raise VvlogError("DIALOG_FAILED", f"ダイアログを開けません: {e}") from e
        out = r.stdout.decode("utf-8", "replace").strip()
        if r.returncode:
            raise VvlogError("DIALOG_FAILED", "ダイアログを開けませんでした",
                             {"stderr": r.stderr.decode("utf-8", "replace")[-500:]})
        try:
            paths = json.loads(out.splitlines()[-1])["paths"] if out else []
        except (ValueError, KeyError, IndexError) as e:
            raise VvlogError("DIALOG_FAILED", "ダイアログの結果を読めません", {"output": out[-200:]}) from e
        return [paths] if isinstance(paths, str) else [p for p in paths if isinstance(p, str) and p]


class UnavailableBackend:
    def run(self, request: dict, timeout: float) -> list[str]:
        raise VvlogError("DIALOG_UNAVAILABLE", "ネイティブダイアログは Windows 版のローカルアプリでだけ使えます")


def default_backend() -> DialogBackend:
    return WindowsDialogBackend() if os.name == "nt" else UnavailableBackend()


class NativeDialog:
    """One dialog at a time: a second request while one is open is refused (DIALOG_BUSY), not queued behind it."""

    def __init__(self, backend: DialogBackend | None = None, timeout: float = TIMEOUT_S):
        self.backend = backend or default_backend()
        self.timeout = timeout
        self._lock = threading.Lock()

    def ask(self, request: dict) -> list[str]:
        if not self._lock.acquire(blocking=False):
            raise VvlogError("DIALOG_BUSY", "別のダイアログが開いています")
        try:
            return self.backend.run(request, self.timeout)
        finally:
            self._lock.release()
