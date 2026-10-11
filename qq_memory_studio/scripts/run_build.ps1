# 构建包装：用固定的解释器 + 短路径输出目录调用 build_exe.ps1（本机 MAX_PATH 规避）。
$ErrorActionPreference = "Stop"
$Python = 'C:\Users\11\.astrbot_launcher\instances\a90cdc51-2c8c-4174-9047-d922df022578\venv\Scripts\python.exe'
$Script = 'C:\Users\11\Desktop\02_应用源码_QQMemoryStudio\qq_memory_studio\build_exe.ps1'
New-Item -ItemType Directory -Force -Path 'C:\QQMS_build' | Out-Null
& powershell -NoProfile -ExecutionPolicy Bypass -File $Script -Python $Python -OneFile -Clean -DistDir 'C:\QQMS_build\dist' -WorkDir 'C:\QQMS_build\build'
Write-Host "build_exe.ps1 exit=$LASTEXITCODE"
