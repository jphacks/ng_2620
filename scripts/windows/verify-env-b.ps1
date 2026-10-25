<#
.SYNOPSIS
  環境 B(pipeline の torch/CUDA 環境)が、ドライバだけの Windows PC で手作業ゼロで作れるかを確かめる(#30)。

.DESCRIPTION
  RTX 機で実行し、できた verify-env-b-report.md を ISSUE #30 に貼る。
  MSVC・CUDA Toolkit は入れない。使うのは uv と、この PC の NVIDIA ドライバだけ。
  毎回 -WorkDir 配下に新しい venv を作る(既存の .venv は触らない)。
  所要時間・ダウンロード/ディスク量・VRAM を記録する(合否ではなく #32/#34 の設計入力)。

.EXAMPLE
  # リポジトリのルートで。detectron2 の wheel(#51)がまだ無いときは -Detectron2Wheel を省く(その項目は NG になる)
  powershell -ExecutionPolicy Bypass -File scripts\windows\verify-env-b.ps1 -Python 3.12 -TorchIndex cu128

.EXAMPLE
  .\scripts\windows\verify-env-b.ps1 -Python 3.13 -TorchIndex cu128 -Detectron2Wheel C:\wheels\detectron2-0.6-cp313-cp313-win_amd64.whl
#>
param(
  [string]$Python = "3.12",                 # uv が取ってくる Python の版(3.12 / 3.13 を比べる)
  [string]$TorchIndex = "cu128",            # https://download.pytorch.org/whl/<これ>。Blackwell(sm_120)は cu128 以上
  [string]$Detectron2Wheel = "",            # ビルド済み wheel のパスか URL(#51)。空なら入れない
  [string]$WorkDir = "$env:TEMP\vvlog-verify-env-b",
  [switch]$KeepVenv                         # 終わっても venv を消さない
)
$ErrorActionPreference = "Continue"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Venv = Join-Path $WorkDir ".venv"
$VPy = Join-Path $Venv "Scripts\python.exe"
$Steps = New-Object System.Collections.Generic.List[object]
$Notes = New-Object System.Collections.Generic.List[string]

function Get-DirMB($p) {
  if (-not (Test-Path $p)) { return 0 }
  [math]::Round(((Get-ChildItem $p -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum) / 1MB, 0)
}
function Invoke-Step([string]$Name, [scriptblock]$Body) {
  Write-Host "`n==> $Name" -ForegroundColor Cyan
  $sw = [Diagnostics.Stopwatch]::StartNew()
  $out = ""
  try { $out = (& $Body 2>&1 | Out-String); $ok = ($LASTEXITCODE -eq 0 -or $null -eq $LASTEXITCODE) }
  catch { $out = $_.ToString(); $ok = $false }
  $sw.Stop()
  $tail = (($out -split "`r?`n") | Select-Object -Last 12) -join "`n"
  Write-Host $tail
  $Steps.Add([pscustomobject]@{ name = $Name; ok = $ok; sec = [math]::Round($sw.Elapsed.TotalSeconds, 0); tail = $tail; diskMB = (Get-DirMB $Venv) })
  return $ok
}

New-Item -ItemType Directory -Force $WorkDir | Out-Null
if (Test-Path $Venv) { Remove-Item $Venv -Recurse -Force }

# ---- 0. 前提(ドライバと uv だけ)
$gpu = $null
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
  $gpu = (nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>&1 | Out-String).Trim()
} else { $Notes.Add("nvidia-smi が無い: NVIDIA ドライバ未導入(非対応環境の判定の確認材料)") }
$hasUv = [bool](Get-Command uv -ErrorAction SilentlyContinue)
if (-not $hasUv) { Write-Host "uv が見つからない。https://docs.astral.sh/uv/ で入れてから再実行(同梱予定のものと同じ版を使う)" -ForegroundColor Red; exit 2 }
$hasCl = [bool](Get-Command cl.exe -ErrorAction SilentlyContinue); $hasNvcc = [bool](Get-Command nvcc -ErrorAction SilentlyContinue)
if ($hasCl -or $hasNvcc) { $Notes.Add("この PC には MSVC(cl)=$hasCl / nvcc=$hasNvcc がある。『ドライバのみ』の検証にならないので、無い PC でも確認する") }

# ---- 1. venv と torch
$null = Invoke-Step "uv venv (Python $Python)" { uv venv $Venv -p $Python }
$null = Invoke-Step "torch/torchvision ($TorchIndex)" { uv pip install -p $VPy torch torchvision --index-url "https://download.pytorch.org/whl/$TorchIndex" }

# ---- 2. detectron2(ビルド済み wheel のみ。ソースビルドはしない)
if ($Detectron2Wheel) {
  $null = Invoke-Step "detectron2 wheel" { uv pip install -p $VPy --no-deps $Detectron2Wheel }
} else { $Notes.Add("detectron2 の wheel 未指定(#51 待ち)。import detectron2 は NG になる") }

# ---- 3. そのほかの依存(sdist しか無くビルドが要るものは --only-binary で NG にして洗い出す)
$deps = @("opencv-python~=4.12.0.88","einops~=0.8.1","pycocotools~=2.0.10","roma~=1.5.4","omegaconf~=2.3.0","pytorch_lightning",
  "yacs~=0.1.8","fvcore","pyrender~=0.1.45","termcolor~=3.2.0","diffusers==0.29.1","transformers~=4.57.3","accelerate~=1.12.0",
  "imageio[ffmpeg]","timm","ftfy","regex","iopath","trimesh","braceexpand~=0.1.7","cloudpickle~=3.1.2","psutil","torchmetrics",
  "webdataset","scipy","moderngl","ai-edge-litert","huggingface-hub<1.0","eva-decord","triton-windows")
foreach ($d in $deps) {
  $ok = Invoke-Step "wheel があるか: $d" { uv pip install -p $VPy --only-binary :all: $d }
  if (-not $ok) { $Notes.Add("バイナリ wheel だけでは入らない: $d(ソースビルドが要る可能性)") }
}
$null = Invoke-Step "MoGe / utils3d (git)" {
  uv pip install -p $VPy "git+https://github.com/microsoft/MoGe.git" "huggingface-hub<1.0"
  uv pip uninstall -p $VPy utils3d_moge
  uv pip install -p $VPy --no-deps "git+https://github.com/EasternJournalist/utils3d.git"
}
$null = Invoke-Step "sam3 (editable)" { Push-Location (Join-Path $Root "pipeline"); try { uv pip install -p $VPy --no-deps -e models/sam3 } finally { Pop-Location } }

# ---- 4. import と GPU の通し
$probe = $null
$sw = [Diagnostics.Stopwatch]::StartNew()
$json = & $VPy (Join-Path $PSScriptRoot "probe_env_b.py") --pipeline-dir (Join-Path $Root "pipeline") 2>&1 | Select-Object -Last 1
$sw.Stop()
try { $probe = $json | ConvertFrom-Json } catch { $Notes.Add("probe の出力が JSON でない: $json") }

# ---- 5. 重みと通しは時間がかかるので別の手順(README に書く)。ここでは記録だけ
$peakMB = if ($gpu) { (nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>&1 | Out-String).Trim() } else { "" }
$summary = [pscustomobject]@{
  when = (Get-Date).ToString("s"); os = (Get-CimInstance Win32_OperatingSystem).Caption; build = [Environment]::OSVersion.Version.ToString()
  gpu = $gpu; python = $Python; torchIndex = $TorchIndex; steps = $Steps; probe = $probe; notes = $Notes
  totalSec = ($Steps | Measure-Object sec -Sum).Sum; venvMB = (Get-DirMB $Venv)
}
$summary | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $WorkDir "verify-env-b-report.json") -Encoding UTF8

# ---- レポート(ISSUE に貼る)
$md = New-Object System.Collections.Generic.List[string]
$md.Add("## 環境 B 検証結果 ($($summary.when))")
$md.Add("- OS: $($summary.os) ($($summary.build))"); $md.Add("- GPU/ドライバ/VRAM: ``$gpu``")
$md.Add("- Python: $Python / torch index: $TorchIndex / detectron2 wheel: $(if ($Detectron2Wheel) { $Detectron2Wheel } else { 'なし' })")
$md.Add("- 合計所要: $($summary.totalSec) 秒 / venv のディスク: $($summary.venvMB) MB"); $md.Add("")
$md.Add("| 手順 | 結果 | 秒 | venv MB |"); $md.Add("|---|---|---|---|")
foreach ($s in $Steps) { $md.Add("| $($s.name) | $(if ($s.ok) { 'OK' } else { 'NG' }) | $($s.sec) | $($s.diskMB) |") }
if ($probe) {
  $md.Add(""); $md.Add("### import")
  foreach ($p in $probe.imports.PSObject.Properties) { $v = $p.Value; $md.Add("- $($p.Name): $(if ($v.ok) { 'OK ' + $v.version } else { 'NG ' + $v.error })") }
  $md.Add(""); $md.Add("### CUDA"); $md.Add('```'); $md.Add(($probe.cuda | ConvertTo-Json -Depth 4)); $md.Add('```')
}
if ($Notes.Count) { $md.Add(""); $md.Add("### 気づき"); foreach ($n in $Notes) { $md.Add("- $n") } }
$md.Add(""); $md.Add("### NG の手順の出力(末尾)")
foreach ($s in ($Steps | Where-Object { -not $_.ok })) { $md.Add("<details><summary>$($s.name)</summary>`n`n``````n$($s.tail)`n```````n`n</details>") }
$report = Join-Path $WorkDir "verify-env-b-report.md"
$md | Set-Content $report -Encoding UTF8
Write-Host "`nレポート: $report (JSON も同じ場所)。内容を ISSUE #30 に貼る" -ForegroundColor Green
if (-not $KeepVenv) { Write-Host "venv は残してある($Venv)。重みの通しを続けるなら README を参照。消すなら Remove-Item -Recurse" }
exit $(if ($probe -and $probe.ok -and -not ($Steps | Where-Object { -not $_.ok })) { 0 } else { 1 })
