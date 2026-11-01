#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$StudentsCsv = (Join-Path $PSScriptRoot 'students.csv')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'modules/TronCourseTools/TronCourseTools.psd1') -Force
$config = Get-CourseConfig -Path (Join-Path $PSScriptRoot 'CourseConfig.psd1')
$reportDirectory = Join-Path $PSScriptRoot 'reports'
$errors = [System.Collections.Generic.List[string]]::new()

try {
    $students = @(Import-StudentRoster -Path $StudentsCsv)
    $validation = Test-StudentRoster -Students $students -Config $config
    foreach ($message in $validation.Errors) { $errors.Add($message) }

    foreach ($relative in Get-RequiredCourseDirectories) {
        if ($relative -eq 'reports') { continue }
        if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $relative) -PathType Container)) {
            $errors.Add("缺少目录：$relative")
        }
    }

    $repository = Get-GitHubRepositoryState -FullName $config.FullName
    if (-not $repository.Exists) {
        $errors.Add("仓库不存在：$($config.FullName)")
        $results = @(New-InvitationPlan -Students $students)
    }
    else {
        if ($repository.FullName -ne $config.FullName) { $errors.Add("仓库所有者或名称不正确：$($repository.FullName)") }
        if ($repository.Visibility -ne 'public') { $errors.Add("仓库不是 Public：$($repository.Visibility)") }
        $collaborators = @(Get-GitHubCollaborators -FullName $config.FullName)
        $pending = @(Get-GitHubPendingInvitations -FullName $config.FullName)
        $results = @(New-InvitationPlan -Students $students -Collaborators $collaborators -PendingInvitations $pending)
    }

    foreach ($row in $results) {
        if ($row.Status -eq 'PendingInvitation') {
            $errors.Add("$($row.GitHubUsername) 尚未接受邀请。")
        }
        elseif ($row.Status -eq 'NeedsInvitation') {
            $errors.Add("$($row.GitHubUsername) 尚未邀请。")
        }
        elseif ($row.Status -eq 'Collaborator' -and -not $row.HasWrite) {
            $errors.Add("$($row.GitHubUsername) 已加入但没有仓库级写入权限。")
        }
        elseif ($row.Status -eq 'Collaborator' -and $row.IsAdmin) {
            $errors.Add("$($row.GitHubUsername) 被授予了 Admin 权限；学生只应拥有直接提交所需的写入权限。")
        }
    }

    $report = Write-DeploymentReports -Results $results -Config $config -ReportDirectory $reportDirectory
    $results | Format-Table GroupId, StudentName, GitHubUsername, Status, Accepted, HasWrite -AutoSize
    if ($errors.Count -gt 0) {
        foreach ($message in $errors) { Write-Host "[未通过] $message" -ForegroundColor Red }
        Write-DeploymentLog -ReportDirectory $reportDirectory -Message "权限验证未通过，共 $($errors.Count) 项。" -Level WARN | Out-Null
        exit 1
    }

    Write-DeploymentLog -ReportDirectory $reportDirectory -Message '权限验证通过：35 名学生均已接受邀请并拥有仓库级写入权限。' | Out-Null
    Write-Host '验证通过：唯一公开仓库、目录结构和 35 名学生仓库级写权限均正确。' -ForegroundColor Green
}
catch {
    Write-DeploymentLog -ReportDirectory $reportDirectory -Message $_.Exception.Message -Level ERROR | Out-Null
    Write-Error $_.Exception.Message
    exit 1
}
