# QQ Memory Studio 一键打包脚本（PyInstaller）
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
#   powershell -ExecutionPolicy Bypass -File .\build_exe.ps1 -Python "C:\path\to\python.exe"
#   powershell -ExecutionPolicy Bypass -File .\build_exe.ps1 -OneFile      # 打单文件（启动更慢）
#
# 产物：<studio>\dist\QQMemoryStudio\QQMemoryStudio.exe（-OneFile 时为 dist\QQMemoryStudio.exe）

param(
    [string]$Python = "",
    [switch]$OneFile,
    [switch]$Clean,
    # 输出目录（默认 <studio>\dist 与 <studio>\build）。
    # 若项目路径过深导致 Windows MAX_PATH(260) 超限（onedir 的 PySide6 插件子路径很深），
    # 可把产物指到短路径，例如 -DistDir C:\QQMS_build\dist -WorkDir C:\QQMS_build\build
    [string]$DistDir = "",
    [string]$WorkDir = ""
)

$ErrorActionPreference = "Stop"
# 关键：禁止 Python 生成 .pyc。否则 PyInstaller 分析期 import pipeline 会在源码树里
# 写出 pipeline\__pycache__\*.pyc（内嵌开发机绝对路径），随后被 `--add-data pipeline;pipeline`
# 一并打进 exe —— 泄露构建机路径。
$env:PYTHONDONTWRITEBYTECODE = "1"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
if (-not $DistDir) { $DistDir = Join-Path $Root "dist" }
if (-not $WorkDir) { $WorkDir = Join-Path $Root "build" }

# ---- 选择解释器 ----
if (-not $Python) {
    if (Get-Command python -ErrorAction SilentlyContinue) { $Python = "python" }
    elseif (Get-Command py -ErrorAction SilentlyContinue) { $Python = "py" }
    else { throw "找不到 python，请用 -Python 指定解释器路径" }
}
Write-Host "[build] 使用解释器: $Python"
& $Python -c "import sys; print(sys.version)"

# ---- 确保依赖 ----
Write-Host "[build] 安装/确认依赖 ..."
& $Python -m pip install --disable-pip-version-check -q -r "$Root\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }
& $Python -m pip install --disable-pip-version-check -q pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 安装失败" }

# ---- 组装 PyInstaller 参数 ----
$Mode = if ($OneFile) { "--onefile" } else { "--onedir" }

# ---- 清理源码里的 __pycache__ ----
# 否则 `--add-data pipeline;pipeline` 会把开发机生成的 .pyc 一起打进产物，
# 而这些 .pyc 内嵌了开发机源码绝对路径（co_filename），既不干净也无必要。
Get-ChildItem -Path $Root -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$pyargs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    $Mode,
    "--noconsole",
    "--name", "QQMemoryStudio",
    "--distpath", "$DistDir",
    "--workpath", "$WorkDir",
    "--paths", "$Root",
    "--paths", "$Root\pipeline",
    "--paths", "$Root\pipeline\tools\util",
    # 随包提供流水线（同步动作在子进程里以 runpy 方式调用，需作为数据一起打包）
    "--add-data", "pipeline;pipeline",
    # 本项目模块
    "--hidden-import", "core", "--hidden-import", "core.config", "--hidden-import", "core.memory",
    "--hidden-import", "core.semantic", "--hidden-import", "core.sync", "--hidden-import", "core.ai",
    "--hidden-import", "core.qq",
    "--hidden-import", "ui", "--hidden-import", "ui.main_window", "--hidden-import", "ui.search_window",
    "--hidden-import", "ui.ai_window", "--hidden-import", "ui.workers", "--hidden-import", "ui.guide_window",
    # 流水线（被 runpy/动态导入）
    "--hidden-import", "pipeline", "--hidden-import", "pipeline.sync",
    "--hidden-import", "pipeline.refresh_memory", "--hidden-import", "pipeline.build_memory",
    "--hidden-import", "pipeline.build_cjk_fts", "--hidden-import", "pipeline.dbinit",
    "--hidden-import", "pipeline.paths", "--hidden-import", "pipeline.config_util",
    "--hidden-import", "pipeline.export_embed_jsonl", "--hidden-import", "pipeline.embed_vectors",
    "--hidden-import", "pipeline.export_text", "--hidden-import", "pipeline.search_semantic",
    "--hidden-import", "pipeline.search_scope", "--hidden-import", "pipeline.extract_key", "--hidden-import", "pipeline.decrypt",
    # 消息解析子包
    "--hidden-import", "msgdb", "--hidden-import", "msgdb.c2c.parser",
    "--hidden-import", "msgdb.group.exporter", "--hidden-import", "msgdb.proto",
    # 第三方
    "--hidden-import", "sqlcipher3", "--hidden-import", "sqlcipher3.dbapi2",
    "--hidden-import", "numpy", "--hidden-import", "requests",
    "--collect-submodules", "pipeline"
    # 图标（存在才加）
)
if (Test-Path "$Root\assets\app.ico") { $pyargs += @("--icon", "$Root\assets\app.ico") }
if ($Clean) { $pyargs += "--clean" }

$pyargs += "$Root\app.py"

# 再次清理（防止前面步骤又生成），随后立即构建。
Get-ChildItem -Path $Root -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "[build] 执行: $Python $($pyargs -join ' ')"
& $Python -B @pyargs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败（退出码 $LASTEXITCODE）" }

# ---- 清理产物里的 __pycache__ ----
# 构建期 PyInstaller 会重新导入 pipeline 并生成 .pyc（内嵌开发机源码路径），
# 这些 .pyc 会随后被 COLLECT 一起复制进 _internal；删掉即可（运行时由 .py 现场编译）。
$outRoot = if ($OneFile) { $DistDir } else { Join-Path $DistDir "QQMemoryStudio" }
Get-ChildItem -Path $outRoot -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "[build] 完成。产物目录：$DistDir"
if ($OneFile) { Write-Host "        -> $DistDir\QQMemoryStudio.exe" }
else          { Write-Host "        -> $DistDir\QQMemoryStudio\QQMemoryStudio.exe" }
Write-Host "[build] 首次运行会在 exe 同目录生成 config.json（记忆库目录可自行修改）。"
