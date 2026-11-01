#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$CourseRoot = $PSScriptRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'modules/TronCourseTools/TronCourseTools.psd1') -Force
$config = Get-CourseConfig -Path (Join-Path $PSScriptRoot 'CourseConfig.psd1')
$reportDirectory = Join-Path $CourseRoot 'reports'

function Show-Step([string]$Message) { Write-Host "[计划] $Message" -ForegroundColor Cyan }
function Require-Command([string]$Name, [string]$InstallHint) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) { throw "未安装 $Name。$InstallHint" }
    Write-Host "[通过] 已找到 $Name：$($command.Source)" -ForegroundColor Green
}

try {
    Require-Command -Name 'git' -InstallHint '请执行 winget install --id Git.Git。'
    Require-Command -Name 'gh' -InstallHint '请执行 winget install --id GitHub.cli。'

    $login = Invoke-GitHubCli -Arguments @('api', 'user', '--jq', '.login')
    $currentUser = $login.Output.Trim()
    if ($currentUser -ne $config.Owner) {
        throw "当前 GitHub 登录账号为 '$currentUser'，必须登录 '$($config.Owner)'。请执行 gh auth logout 后重新登录。"
    }
    Write-Host "[通过] GitHub 登录账号：$currentUser" -ForegroundColor Green

    $repositoryState = Get-GitHubRepositoryState -FullName $config.FullName
    if ($repositoryState.Exists) {
        throw "远程仓库 $($config.FullName) 已存在。为防止覆盖原有代码，本脚本拒绝继续。"
    }

    $requiredDirectories = Get-RequiredCourseDirectories
    foreach ($relative in $requiredDirectories) {
        $path = Join-Path $CourseRoot $relative
        if (-not (Test-Path -LiteralPath $path -PathType Container)) {
            Show-Step "创建目录 $relative"
            if ($Apply) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
        }
    }

    Show-Step "创建唯一公开仓库 $($config.FullName)"
    Show-Step "将本地内容提交到 main 并推送至 $($config.RepositoryUrl)"
    Show-Step '将 GitHub 默认分支设置为 main'

    if (-not $Apply) {
        Write-Host 'Dry-Run 完成：未创建目录、未创建远程仓库、未提交或推送代码。使用 -Apply 才会执行。' -ForegroundColor Yellow
        exit 0
    }

    if (-not (Test-Path -LiteralPath $CourseRoot -PathType Container)) {
        New-Item -ItemType Directory -Path $CourseRoot -Force | Out-Null
    }
    if (-not (Test-Path -LiteralPath (Join-Path $CourseRoot '.git'))) {
        & git -C $CourseRoot init
        if ($LASTEXITCODE -ne 0) { throw 'git init 失败。' }
    }

    $remoteNames = @(& git -C $CourseRoot remote)
    if ($LASTEXITCODE -ne 0) { throw '读取本地 Git 远程配置失败。' }
    if ($remoteNames -contains 'origin') {
        $origin = @(& git -C $CourseRoot remote get-url origin)
        if ($LASTEXITCODE -ne 0) { throw '读取本地 origin 地址失败。' }
        if ($origin.Count -gt 0 -and $origin[0] -notmatch [regex]::Escape($config.FullName)) {
            throw "本地 origin 指向其他仓库：$($origin[0])。拒绝修改。"
        }
    }

    & git -C $CourseRoot add --all
    if ($LASTEXITCODE -ne 0) { throw 'git add 失败。' }
    & git -C $CourseRoot diff --cached --quiet
    if ($LASTEXITCODE -ne 0) {
        & git -C $CourseRoot commit -m 'Initialize TRON2 course repository'
        if ($LASTEXITCODE -ne 0) { throw 'git commit 失败，请检查 git user.name 与 user.email 配置。' }
    }
    & git -C $CourseRoot branch -M $config.DefaultBranch
    if ($LASTEXITCODE -ne 0) { throw '设置 main 分支失败。' }

    Invoke-GitHubCli -Arguments @('repo', 'create', $config.FullName, '--public', '--source', $CourseRoot, '--remote', 'origin') | Out-Null
    & git -C $CourseRoot push -u origin $config.DefaultBranch
    if ($LASTEXITCODE -ne 0) { throw '首次推送失败。仓库可能已创建，请人工检查后再处理，脚本不会覆盖重试。' }
    Invoke-GitHubCli -Arguments @('repo', 'edit', $config.FullName, '--default-branch', $config.DefaultBranch) | Out-Null

    Write-DeploymentLog -ReportDirectory $reportDirectory -Message "已创建公开仓库 $($config.FullName)，默认分支 $($config.DefaultBranch)。" | Out-Null
    Write-Host "部署完成：$($config.RepositoryUrl)" -ForegroundColor Green
}
catch {
    if (Test-Path -LiteralPath $CourseRoot) {
        Write-DeploymentLog -ReportDirectory $reportDirectory -Message $_.Exception.Message -Level ERROR | Out-Null
    }
    Write-Error $_.Exception.Message
    exit 1
}
