#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$StudentsCsv = (Join-Path $PSScriptRoot 'students.csv')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'modules/TronCourseTools/TronCourseTools.psd1') -Force
$config = Get-CourseConfig -Path (Join-Path $PSScriptRoot 'CourseConfig.psd1')
$reportDirectory = Join-Path $PSScriptRoot 'reports'

try {
    $students = @(Import-StudentRoster -Path $StudentsCsv)
    $localValidation = Test-StudentRoster -Students $students -Config $config

    Write-Host "学生总数：$($students.Count)"
    $localValidation.GroupSummary | Format-Table -AutoSize
    $students | Sort-Object group_id, student_name | Format-Table group_id, student_name, github_username -AutoSize

    if (-not $localValidation.IsValid) {
        foreach ($message in $localValidation.Errors) { Write-Host "[名单错误] $message" -ForegroundColor Red }
        throw 'students.csv 未通过本地验证；未访问 GitHub，也未发送任何邀请。'
    }

    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw '未安装 GitHub CLI（gh）。请先执行 winget install --id GitHub.cli。'
    }
    $currentUser = (Invoke-GitHubCli -Arguments @('api', 'user', '--jq', '.login')).Output.Trim()
    if ($currentUser -ne $config.Owner) { throw "当前登录账号 '$currentUser' 不是仓库所有者 '$($config.Owner)'。" }

    $repository = Get-GitHubRepositoryState -FullName $config.FullName
    if (-not $repository.Exists) { throw "仓库 $($config.FullName) 不存在。请先运行 Initialize-Repository.ps1。" }
    if ($repository.Visibility -ne 'public') { throw "仓库可见性为 '$($repository.Visibility)'，必须为 public。" }

    Write-Host '正在逐一验证 GitHub 账号是否真实存在（只读）...'
    $accountValidation = Test-StudentRoster -Students $students -Config $config -CheckAccounts
    if (-not $accountValidation.IsValid) {
        foreach ($message in $accountValidation.Errors) { Write-Host "[账号错误] $message" -ForegroundColor Red }
        throw '存在无效或无法确认的 GitHub 账号；未发送邀请。'
    }

    $collaborators = @(Get-GitHubCollaborators -FullName $config.FullName)
    $pendingInvitations = @(Get-GitHubPendingInvitations -FullName $config.FullName)
    $plan = @(New-InvitationPlan -Students $students -Collaborators $collaborators -PendingInvitations $pendingInvitations)
    $results = @(Invoke-InvitationPlan -Plan $plan -FullName $config.FullName -Apply:$Apply)

    $results | Format-Table GroupId, StudentName, GitHubUsername, Status, Accepted, HasWrite -AutoSize
    $report = Write-DeploymentReports -Results $results -Config $config -ReportDirectory $reportDirectory
    $sent = @($results | Where-Object Status -eq 'InvitationSent').Count
    $failed = @($results | Where-Object Status -eq 'InvitationFailed').Count
    $wouldInvite = @($results | Where-Object Status -eq 'DryRunWouldInvite').Count
    Write-DeploymentLog -ReportDirectory $reportDirectory -Message "邀请检查完成：已发送 $sent，失败 $failed，Dry-Run 待发送 $wouldInvite。" | Out-Null
    Write-Host "报告：$($report.CsvPath)；$($report.JsonPath)"

    if (-not $Apply) {
        Write-Host "Dry-Run 完成：将邀请 $wouldInvite 人；未发送任何邀请。正式执行请添加 -Apply。" -ForegroundColor Yellow
    }
    elseif ($failed -gt 0) {
        throw "$failed 名学生邀请失败。可修正问题后重新运行，已存在的协作者或邀请会自动跳过。"
    }
}
catch {
    Write-DeploymentLog -ReportDirectory $reportDirectory -Message $_.Exception.Message -Level ERROR | Out-Null
    Write-Error $_.Exception.Message
    exit 1
}
