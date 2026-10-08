# burrowl 安装与版本切换（Windows PowerShell 5.1 可用）。
#
#   <Root>\versions\<版本>\     每个版本一个完整目录，互不覆盖
#   <Root>\current.txt          当前版本号
#   <Root>\bin\burrowl.cmd          指向当前版本的启动器（加入用户 PATH）
#   <Root>\models\              共享模型（安装包带模型且这里没有时复制）
#
# Root 默认 %LOCALAPPDATA%\burrowl；可用 -Root 指定（路径可含中文与空格）。
param(
    [string]$Root = (Join-Path $env:LOCALAPPDATA "burrowl"),
    [string]$Use,
    [switch]$List,
    [switch]$NoPath
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$versions = Join-Path $Root "versions"
New-Item -ItemType Directory -Force -Path $versions | Out-Null

function Write-Shim([string]$version) {
    $bin = Join-Path $Root "bin"
    New-Item -ItemType Directory -Force -Path $bin | Out-Null
    $target = Join-Path (Join-Path $versions $version) "burrowl.cmd"
    if (-not (Test-Path -LiteralPath $target)) { throw "版本 $version 未安装" }
    # current 是指向当前版本的目录联接：bin\burrowl.cmd 与 MCP 宿主配置都经它找到当前版本，切换版本无需改配置。
    $link = Join-Path $Root "current"
    if (Test-Path -LiteralPath $link) { cmd /c rmdir "$link" | Out-Null }  # rmdir 只删联接本身，不动目标内容
    New-Item -ItemType Junction -Path $link -Target (Join-Path $versions $version) | Out-Null
    # 只用相对的 ASCII 路径：cmd 按控制台代码页读取 .cmd，中文绝对路径会被读坏。
    $shim = "@echo off`r`ncall `"%~dp0..\current\burrowl.cmd`" %*`r`nexit /b %ERRORLEVEL%`r`n"
    [System.IO.File]::WriteAllText((Join-Path $bin "burrowl.cmd"), $shim, (New-Object System.Text.ASCIIEncoding))
    [System.IO.File]::WriteAllText((Join-Path $Root "current.txt"), $version)
    if (-not $NoPath) {
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if (-not (($userPath -split ";") -contains $bin)) {
            [Environment]::SetEnvironmentVariable("Path", (($userPath.TrimEnd(";") + ";" + $bin).TrimStart(";")), "User")
            Write-Host "已把 $bin 加入用户 PATH（新开的终端生效）"
        }
    }
    Write-Host "当前版本：$version"
}

if ($List) {
    $current = if (Test-Path (Join-Path $Root "current.txt")) { Get-Content (Join-Path $Root "current.txt") } else { "" }
    Get-ChildItem -LiteralPath $versions -Directory | ForEach-Object {
        $mark = if ($_.Name -eq $current) { "*" } else { " " }
        Write-Host "$mark $($_.Name)"
    }
    exit 0
}

if ($Use) {
    Write-Shim $Use
    exit 0
}

$version = (Get-Content -LiteralPath (Join-Path $here "VERSION") -Raw).Trim()
$dest = Join-Path $versions $version
if ((Resolve-Path -LiteralPath $here).Path -ne $dest) {
    if (Test-Path -LiteralPath $dest) {
        Write-Host "版本 $version 已存在，覆盖安装"
        Remove-Item -LiteralPath $dest -Recurse -Force
    }
    Write-Host "安装到 $dest"
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Get-ChildItem -LiteralPath $here -Force | Where-Object { $_.Name -ne "models" } |
        Copy-Item -Destination $dest -Recurse -Force
}

$bundledModels = Join-Path $here "models"
$sharedModels = Join-Path $Root "models"
if ((Test-Path -LiteralPath $bundledModels) -and -not (Test-Path -LiteralPath (Join-Path $sharedModels "multilingual-minilm"))) {
    Write-Host "复制模型到 $sharedModels"
    New-Item -ItemType Directory -Force -Path $sharedModels | Out-Null
    Copy-Item -Path (Join-Path $bundledModels "*") -Destination $sharedModels -Recurse -Force
}

Write-Shim $version
& (Join-Path $dest "burrowl.cmd") models status
if ($LASTEXITCODE -ne 0) { throw "安装后自检失败" }
Write-Host "安装完成。下一步：burrowl init <数据目录>，然后 burrowl serve"
