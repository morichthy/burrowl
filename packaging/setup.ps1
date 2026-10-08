# burrowl 安装与版本切换（Windows PowerShell 5.1 可用）。
#
#   <Root>\versions\<版本>\     每个版本一个完整目录，互不覆盖
#   <Root>\current.txt          当前版本号
#   <Root>\bin\burrowl.cmd          指向当前版本的启动器（加入用户 PATH）
#   <Root>\models\              共享模型（安装包带模型且这里没有时复制）
#
# Root（安装根目录）：-Root 指定 → 上次安装记下的用户环境变量 BURROWL_ROOT → %LOCALAPPDATA%\burrowl。
# 路径可含中文与空格。升级时不必再写 -Root，会装到上次的位置。
param(
    [string]$Root,
    [string]$Use,
    [switch]$List,
    [switch]$NoPath
)
$ErrorActionPreference = "Stop"
if (-not $Root) { $Root = [Environment]::GetEnvironmentVariable("BURROWL_ROOT", "User") }
if (-not $Root) { $Root = Join-Path $env:LOCALAPPDATA "burrowl" }
$Root = [System.IO.Path]::GetFullPath($Root)
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
    # 从商店/MSIX 打包应用（如 Claude 桌面版）里启动时，写入 AppData 的文件会被重定向到该应用的私有目录，
    # 联接指向的真实路径并不存在；这样装出来的程序其他软件也看不到。及早失败并给出办法。
    if (-not (Test-Path -LiteralPath (Join-Path $link "burrowl.cmd"))) {
        throw "安装目录 $Root 被系统重定向（常见于从商店版应用中运行安装）。请改用 -Root 指定 AppData 以外的目录，如 setup.cmd -Root D:\software\burrowl"
    }
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
    if (-not $NoPath) { [Environment]::SetEnvironmentVariable("BURROWL_ROOT", $Root, "User") }  # 下次升级的默认位置
    Write-Host "当前版本：$version"
    # 切换版本后刷新所有登记过的 Skill 目录（只替换用户没改过的文件）；MCP 配置经 current 联接自动生效
    & (Join-Path $link "burrowl.cmd") upgrade
    if ($LASTEXITCODE -ne 0) { throw "刷新 Skill 失败" }
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
    # 同版本重装时就地同步，不先删除：正在运行的 MCP 服务会锁住 python 的 DLL，先删会删到一半失败。
    # robocopy /MIR 跳过未变化的文件（锁住的运行时文件内容相同，不需要复制），删除包里已没有的文件。
    if (Test-Path -LiteralPath $dest) { Write-Host "版本 $version 已存在，同步更新文件" } else { Write-Host "安装到 $dest" }
    robocopy $here $dest /MIR /XD (Join-Path $here "models") /R:0 /W:0 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "复制到 $dest 失败：有文件正被占用。关闭正在使用 burrowl 的 AI 客户端（其 MCP 服务）后重试"
    }
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
