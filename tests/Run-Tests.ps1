#requires -Version 5.1
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = Split-Path $PSScriptRoot -Parent
Import-Module (Join-Path $root 'modules/TronCourseTools/TronCourseTools.psd1') -Force
$config = Get-CourseConfig -Path (Join-Path $root 'CourseConfig.psd1')
$script:passed = 0
$script:failed = 0

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
function Assert-Equal($Expected, $Actual, [string]$Message) {
    if ($Expected -ne $Actual) { throw "$Message；期望 '$Expected'，实际 '$Actual'。" }
}
function Test-Case([string]$Name, [scriptblock]$Body) {
    try {
        & $Body
        $script:passed++
        Write-Host "[PASS] $Name" -ForegroundColor Green
    }
    catch {
        $script:failed++
        Write-Host "[FAIL] $Name - $($_.Exception.Message)" -ForegroundColor Red
    }
}
function New-TestRoster {
    $students = [System.Collections.Generic.List[object]]::new()
    for ($i = 1; $i -le 35; $i++) {
        $group = [math]::Min([math]::Floor(($i - 1) / 6) + 1, 6)
        $students.Add([pscustomobject]@{
            group_id = $group.ToString('00')
            student_name = "测试学生$i"
            github_username = "validuser$i"
        })
    }
    @($students)
}

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("tron2-tests-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempRoot | Out-Null
try {
    Test-Case 'CSV 以 UTF-8 正确读取中文姓名' {
        $csv = Join-Path $tempRoot 'students.csv'
        [System.IO.File]::WriteAllText($csv, "group_id,student_name,github_username`n01,张三,zhangsan-real", [System.Text.UTF8Encoding]::new($true))
        $rows = @(Import-StudentRoster -Path $csv)
        Assert-Equal 1 $rows.Count 'CSV 行数错误'
        Assert-Equal '张三' $rows[0].student_name '中文姓名损坏'
    }

    Test-Case '恰好 35 人名单通过人数检查' {
        $result = Test-StudentRoster -Students (New-TestRoster) -Config $config
        Assert-True $result.IsValid (($result.Errors) -join '; ')
    }

    Test-Case '交付的 students.csv 恰好预置 35 行和 6 个小组' {
        $delivered = @(Import-StudentRoster -Path (Join-Path $root 'students.csv'))
        Assert-Equal 35 $delivered.Count '交付名单行数错误'
        Assert-Equal '01,02,03,04,05,06' (($delivered.group_id | Sort-Object -Unique) -join ',') '交付名单分组错误'
    }

    Test-Case '少于 35 人被拒绝' {
        $students = @(New-TestRoster | Select-Object -First 34)
        $result = Test-StudentRoster -Students $students -Config $config
        Assert-True (-not $result.IsValid) '34 人名单不应通过'
        Assert-True (($result.Errors -join ' ') -match '35') '错误信息应说明 35 人'
    }

    Test-Case '小组编号只能是 01 至 06' {
        $students = @(New-TestRoster)
        $students[0].group_id = '07'
        $result = Test-StudentRoster -Students $students -Config $config
        Assert-True (-not $result.IsValid) '07 组不应通过'
    }

    Test-Case '重复 GitHub 账号（忽略大小写）被拒绝' {
        $students = @(New-TestRoster)
        $students[1].github_username = $students[0].github_username.ToUpperInvariant()
        $result = Test-StudentRoster -Students $students -Config $config
        Assert-True (-not $result.IsValid) '重复账号不应通过'
        Assert-True (($result.Errors -join ' ') -match '重复') '应报告重复账号'
    }

    Test-Case '占位账号被拒绝' {
        $students = @(New-TestRoster)
        $students[0].github_username = 'github_user_01'
        $result = Test-StudentRoster -Students $students -Config $config
        Assert-True (-not $result.IsValid) '占位账号不应通过'
    }

    Test-Case '模拟账号 API 可识别不存在的用户' {
        $students = @(New-TestRoster)
        $checker = { param($username) return ($username -ne 'validuser10') }
        $result = Test-StudentRoster -Students $students -Config $config -CheckAccounts -AccountChecker $checker
        Assert-True (-not $result.IsValid) '不存在账号不应通过'
        Assert-True (($result.Errors -join ' ') -match 'validuser10') '应指出不存在的账号'
    }

    Test-Case '已存在协作者被跳过并识别写权限' {
        $students = @(New-TestRoster | Select-Object -First 1)
        $collaborators = @([pscustomobject]@{ login = 'validuser1'; permissions = [pscustomobject]@{ push = $true; maintain = $false; admin = $false } })
        $plan = @(New-InvitationPlan -Students $students -Collaborators $collaborators)
        Assert-Equal 'Collaborator' $plan[0].Status '状态错误'
        Assert-True $plan[0].HasWrite '应识别 push 权限'
        Assert-True (-not $plan[0].IsAdmin) '普通协作者不应标记为 Admin'
    }

    Test-Case '现有 Admin 协作者会被明确识别' {
        $students = @(New-TestRoster | Select-Object -First 1)
        $collaborators = @([pscustomobject]@{ login = 'validuser1'; permissions = [pscustomobject]@{ push = $true; maintain = $true; admin = $true } })
        $plan = @(New-InvitationPlan -Students $students -Collaborators $collaborators)
        Assert-True $plan[0].IsAdmin '应识别不合规的 Admin 权限'
    }

    Test-Case '待接受邀请被跳过' {
        $students = @(New-TestRoster | Select-Object -First 1)
        $pending = @([pscustomobject]@{ invitee = [pscustomobject]@{ login = 'validuser1' } })
        $plan = @(New-InvitationPlan -Students $students -PendingInvitations $pending)
        Assert-Equal 'PendingInvitation' $plan[0].Status '状态错误'
    }

    Test-Case '邀请 API 错误被记录且不中断整批处理' {
        $students = @(New-TestRoster | Select-Object -First 2)
        $plan = @(New-InvitationPlan -Students $students)
        $action = { param($repo, $username) if ($username -eq 'validuser1') { throw '模拟 API 422' } }
        $results = @(Invoke-InvitationPlan -Plan $plan -FullName $config.FullName -Apply -InviteAction $action)
        Assert-Equal 'InvitationFailed' $results[0].Status '失败状态错误'
        Assert-Equal 'InvitationSent' $results[1].Status '后续邀请应继续'
        Assert-True ($results[0].Error -match '422') '应记录 API 错误'
    }

    Test-Case 'Dry-Run 不调用远程写操作' {
        $students = @(New-TestRoster | Select-Object -First 3)
        $plan = @(New-InvitationPlan -Students $students)
        $script:remoteWrites = 0
        $action = { param($repo, $username) $script:remoteWrites++ }
        $results = @(Invoke-InvitationPlan -Plan $plan -FullName $config.FullName -InviteAction $action)
        Assert-Equal 0 $script:remoteWrites 'Dry-Run 触发了远程写操作'
        Assert-Equal 3 @($results | Where-Object Status -eq 'DryRunWouldInvite').Count 'Dry-Run 计划数量错误'
    }

    Test-Case '部署报告生成 CSV、JSON 且包含 35 条记录' {
        $reportDir = Join-Path $tempRoot 'reports'
        $plan = @(New-InvitationPlan -Students (New-TestRoster))
        $results = @(Invoke-InvitationPlan -Plan $plan -FullName $config.FullName)
        $report = Write-DeploymentReports -Results $results -Config $config -ReportDirectory $reportDir
        Assert-True (Test-Path -LiteralPath $report.CsvPath) '缺少 CSV 报告'
        Assert-True (Test-Path -LiteralPath $report.JsonPath) '缺少 JSON 报告'
        Assert-Equal 35 @(Import-Csv -LiteralPath $report.CsvPath -Encoding UTF8).Count 'CSV 报告记录数错误'
        $jsonRecords = Get-Content -LiteralPath $report.JsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
        Assert-Equal 35 $jsonRecords.Count 'JSON 报告记录数错误'
    }

    Test-Case '所有 PowerShell 文件通过语法解析' {
        $files = @(Get-ChildItem -LiteralPath $root -Recurse -File | Where-Object Extension -in '.ps1', '.psm1', '.psd1')
        foreach ($file in $files) {
            $tokens = $null
            $parseErrors = $null
            [void][System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$parseErrors)
            Assert-Equal 0 @($parseErrors).Count "$($file.Name) 存在语法错误"
        }
    }

    Test-Case '六组标准目录完整' {
        foreach ($relative in Get-RequiredCourseDirectories) {
            if ($relative -eq 'reports') { continue }
            Assert-True (Test-Path -LiteralPath (Join-Path $root $relative) -PathType Container) "缺少目录 $relative"
        }
    }

    Test-Case '固定配置只定义一个公开个人仓库' {
        Assert-Equal 'sherlver-h' $config.Owner '所有者错误'
        Assert-Equal 'tron2-course-materials' $config.Repository '仓库名错误'
        Assert-Equal 'public' $config.Visibility '仓库必须公开'
        Assert-True ($config.FullName -eq 'sherlver-h/tron2-course-materials') '仓库全名错误'
    }
}
finally {
    if (Test-Path -LiteralPath $tempRoot) { Remove-Item -LiteralPath $tempRoot -Recurse -Force }
}

Write-Host "`n测试结果：通过 $script:passed，失败 $script:failed。"
if ($script:failed -gt 0) { exit 1 }
exit 0
