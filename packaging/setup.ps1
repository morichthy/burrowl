# burrowl 安装与版本切换（Windows PowerShell 5.1 可用）。
#
#   <Root>\versions\<版本>\     每个版本一个完整目录，互不覆盖
#   <Root>\current.txt          当前版本号
#   <Root>\bin\burrowl.cmd          指向当前版本的启动器（加入用户 PATH）
#   <Root>\models\              共享模型（安装包带模型且这里没有时复制）
#   开始菜单\burrowl 工作台.lnk  打开工作台（burrowl open：没在运行就先在后台启动）；-NoShortcut 不建。
#                                快捷方式只是一个文件，不写注册表
#
# Root（安装根目录）：-Root 指定 → 上次安装记下的用户环境变量 BURROWL_ROOT → %LOCALAPPDATA%\burrowl。
# 路径可含中文与空格。升级时不必再写 -Root，会装到上次的位置。
param(
    [string]$Root,
    [string]$Use,
    [switch]$List,
    [switch]$NoPath,
    [switch]$NoShortcut
)
$ErrorActionPreference = "Stop"
# 构建脚本通过管道读取输出；明确 UTF-8，避免 PowerShell 5.1 默认代码页把中文读坏。
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
if (-not $Root) { $Root = [Environment]::GetEnvironmentVariable("BURROWL_ROOT", "User") }
if (-not $Root) { $Root = Join-Path $env:LOCALAPPDATA "burrowl" }
$Root = [System.IO.Path]::GetFullPath($Root)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$versions = Join-Path $Root "versions"
New-Item -ItemType Directory -Force -Path $versions | Out-Null

function Get-ContentHash([string]$Path) {
    # 使用 .NET，避免精简或宿主启动的 PowerShell 环境缺失 Get-FileHash 模块。
    # 以只读方式打开，允许其他进程共享读取；校验过程中绝不修改文件内容。
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try { return [System.Convert]::ToBase64String($hasher.ComputeHash($stream)) }
    finally { $hasher.Dispose(); $stream.Dispose() }
}

function Write-Shim([string]$version) {
    $bin = Join-Path $Root "bin"
    New-Item -ItemType Directory -Force -Path $bin | Out-Null
    $target = Join-Path (Join-Path $versions $version) "burrowl.cmd"
    if (-not (Test-Path -LiteralPath $target)) { throw "版本 $version 未安装" }
    # current 是指向当前版本的目录联接：bin\burrowl.cmd 与 MCP 宿主配置都经它找到当前版本，切换版本无需改配置。
    $link = Join-Path $Root "current"
    $existingLink = Get-Item -LiteralPath $link -Force -ErrorAction SilentlyContinue
    if ($null -ne $existingLink) {
        # current 必须是目录联接，真实目录不能当联接删除。非递归 Delete 只删除联接本身，
        # 全程使用同一套路径 API，不把计算出的路径交给另一种 shell 重新解释。
        if (-not ($existingLink.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw "current 路径不是目录联接，拒绝删除：$link"
        }
        [System.IO.Directory]::Delete($link)
    }
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
    Write-Host "安装根目录：$Root"
    Write-Host "当前版本：$version"
    # 同一版本号可以重复构建；提交和时间用于区分“旧安装包”和“新源码生成的包”。
    # 从 current 读取，显示实际切换到的内容，而不是安装脚本所在目录的内容。
    $buildInfoFile = Join-Path $link "BUILD_INFO.json"
    if (Test-Path -LiteralPath $buildInfoFile) {
        $buildInfo = Get-Content -LiteralPath $buildInfoFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $commit = if ($buildInfo.git_commit) { $buildInfo.git_commit } else { "未知（构建时无 Git 信息）" }
        $dirty = if ($null -eq $buildInfo.git_dirty) { "未知" } elseif ($buildInfo.git_dirty) { "有未提交修改" } else { "无未提交修改" }
        Write-Host "构建提交：$commit；$dirty"
        Write-Host "构建时间（UTC）：$($buildInfo.built_at)"
    } else {
        Write-Host "此安装包未提供 BUILD_INFO.json，无法判断同版本的构建来源。"
    }
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
    Write-Host "版本切换完成。请重启 burrowl 工作台和 AI 客户端的 MCP 服务，再在浏览器按 Ctrl+F5 刷新。"
    exit 0
}

Write-Host "安装来源：$here"
$version = (Get-Content -LiteralPath (Join-Path $here "VERSION") -Raw).Trim()
$dest = Join-Path $versions $version
if ((Resolve-Path -LiteralPath $here).Path -ne $dest) {
    # 同版本重装时就地同步，不先删除：正在运行的 MCP 服务会锁住 python 的 DLL，先删会删到一半失败。
    # robocopy 按大小和时间判断是否复制，不比较内容。两次完整构建的运行时字节相同，
    # 时间却可能不同；直接 /MIR 会尝试覆盖被占用的 DLL，导致更新失败。
    # 只对“大小相同、时间不同”的已有文件核对 SHA256；相同才对齐目标时间，让 /MIR 跳过。
    # 不改源包或目标内容；真正变化的占用文件仍由复制步骤报错，要求关闭相关服务。
    if (Test-Path -LiteralPath $dest) { Write-Host "版本 $version 已存在，同步更新文件" } else { Write-Host "安装到 $dest" }
    $identicalLockedFiles = @()
    if (Test-Path -LiteralPath $dest) {
        $sourcePrefix = $here.TrimEnd([char]'\') + '\'
        $alignedCount = 0
        # models 从复制中排除，也从核对中排除；避免遍历和哈希大型共享模型。
        $sourceFiles = foreach ($item in Get-ChildItem -LiteralPath $here -Force) {
            if ($item.PSIsContainer) {
                if ($item.Name -ne "models") { Get-ChildItem -LiteralPath $item.FullName -Recurse -File -Force }
            } else { $item }
        }
        foreach ($sourceFile in $sourceFiles) {
            $relative = $sourceFile.FullName.Substring($sourcePrefix.Length)
            $targetPath = Join-Path $dest $relative
            $targetFile = Get-Item -LiteralPath $targetPath -Force -ErrorAction SilentlyContinue
            if (($null -eq $targetFile) -or $targetFile.PSIsContainer -or
                ($sourceFile.Length -ne $targetFile.Length) -or
                ($sourceFile.LastWriteTimeUtc -eq $targetFile.LastWriteTimeUtc)) { continue }
            $sourceHash = Get-ContentHash $sourceFile.FullName
            $targetHash = Get-ContentHash $targetPath
            if ($sourceHash -eq $targetHash) {
                try { [System.IO.File]::SetLastWriteTimeUtc($targetPath, $sourceFile.LastWriteTimeUtc) }
                catch {
                    # 严格的跨进程只读占用也可能禁止改时间；只有哈希完全相同的文件才精确排除复制。
                    # 用完整源路径，避免按文件名排除另一个目录里真正变化的同名文件。
                    $identicalLockedFiles += $sourceFile.FullName
                    continue
                }
                $alignedCount += 1
            }
        }
        Write-Host "已按 SHA256 核对并对齐 $alignedCount 个相同内容文件的时间。"
    }
    $excludeArgs = @()
    if ($identicalLockedFiles.Count -gt 0) {
        $excludeArgs = @("/XF") + $identicalLockedFiles
        # 正常同内容文件已对齐时间；仅极少数严格占用者进入命令行，避免超出 Windows 参数上限。
        if (($excludeArgs -join ' ').Length -gt 25000) {
            throw "相同内容但严格占用的文件过多。请关闭使用 burrowl 的工作台或 AI 客户端后重试。"
        }
        Write-Host "已按 SHA256 确认并跳过 $($identicalLockedFiles.Count) 个相同内容的占用文件。"
    }
    # /MIR 保留原先的更新与清理语义；内容变化时正常复制，包中已删除的文件正常清理。
    robocopy $here $dest /MIR /XD (Join-Path $here "models") /R:0 /W:0 /NFL /NDL /NJH /NJS /NP @excludeArgs | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "复制到 $dest 失败：有文件正被占用。关闭正在使用 burrowl 的 AI 客户端（其 MCP 服务）后重试"
    }
}

$bundledModels = Join-Path $here "models"
$sharedModels = Join-Path $Root "models"
if (Test-Path -LiteralPath $bundledModels) {
    # 逐个模型目录补齐：升级时只复制共享目录里还没有的模型（如 0.6 新增的默认嵌入模型），已有的不覆盖
    foreach ($model in Get-ChildItem -LiteralPath $bundledModels -Directory) {
        $target = Join-Path $sharedModels $model.Name
        if (-not (Test-Path -LiteralPath $target)) {
            Write-Host "复制模型 $($model.Name) 到 $sharedModels"
            New-Item -ItemType Directory -Force -Path $sharedModels | Out-Null
            Copy-Item -LiteralPath $model.FullName -Destination $target -Recurse -Force
        }
    }
}

Write-Shim $version
if (-not $NoShortcut) {
    # 开始菜单快捷方式（R21 D-15）：指向 bin\burrowl.cmd open，最小化运行，命令行窗口一闪即关；工作台在后台运行
    $programs = [Environment]::GetFolderPath("Programs")
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut((Join-Path $programs "burrowl 工作台.lnk"))
    $link.TargetPath = Join-Path (Join-Path $Root "bin") "burrowl.cmd"
    $link.Arguments = "open"
    $link.WorkingDirectory = $Root
    $link.WindowStyle = 7
    $icon = Join-Path $dest "burrowl.ico"
    if (Test-Path -LiteralPath $icon) { $link.IconLocation = "$icon,0" }
    $link.Description = "打开 burrowl 工作台（没在运行时先在后台启动）"
    $link.Save()
    Write-Host "已在开始菜单建立快捷方式“burrowl 工作台”（-NoShortcut 可不建）"
}
& (Join-Path $dest "burrowl.cmd") models status
if ($LASTEXITCODE -ne 0) { throw "安装后自检失败" }
Write-Host "安装完成。首次使用：burrowl init <数据目录>，然后 burrowl start（后台运行）或从开始菜单打开“burrowl 工作台”。"
# 文件同步不会替换已被 Python 进程加载的模块，也不会自动重启服务。
# 只明确告知用户后续步骤，不终止用户正在进行的阅读或其他工作。
Write-Host "更新已有安装：在工作台首页点“重启服务”（或运行 burrowl restart），并重启 AI 客户端的 MCP 服务，再在浏览器按 Ctrl+F5 刷新。"
