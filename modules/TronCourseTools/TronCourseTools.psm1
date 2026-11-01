#requires -Version 5.1
Set-StrictMode -Version Latest

function Get-CourseConfig {
    [CmdletBinding()]
    param(
        [string]$Path = (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'CourseConfig.psd1')
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "找不到配置文件：$Path"
    }

    $config = Import-PowerShellDataFile -LiteralPath $Path
    [pscustomobject]@{
        Owner            = [string]$config.Owner
        Repository       = [string]$config.Repository
        RepositoryUrl    = [string]$config.RepositoryUrl
        Visibility       = [string]$config.Visibility
        DefaultBranch    = [string]$config.DefaultBranch
        ExpectedStudents = [int]$config.ExpectedStudents
        AllowedGroups    = @($config.AllowedGroups)
        FullName         = '{0}/{1}' -f $config.Owner, $config.Repository
    }
}

function Import-StudentRoster {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "找不到学生名单：$Path"
    }

    $rows = @(Import-Csv -LiteralPath $Path -Encoding UTF8)
    $required = @('group_id', 'student_name', 'github_username')
    if ($rows.Count -eq 0) {
        throw '学生名单为空。'
    }

    $headers = @($rows[0].PSObject.Properties.Name)
    foreach ($header in $required) {
        if ($header -notin $headers) {
            throw "students.csv 缺少必需列：$header"
        }
    }

    @($rows | ForEach-Object {
        [pscustomobject]@{
            group_id       = ([string]$_.group_id).Trim().PadLeft(2, '0')
            student_name   = ([string]$_.student_name).Trim()
            github_username = ([string]$_.github_username).Trim()
        }
    })
}

function Test-GitHubUsernameFormat {
    [CmdletBinding()]
    param([AllowEmptyString()][string]$Username)

    if ([string]::IsNullOrWhiteSpace($Username)) { return $false }
    if ($Username.Length -gt 39) { return $false }
    if ($Username -notmatch '^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$') { return $false }
    if ($Username -match '--') { return $false }
    return $true
}

function Test-StudentRoster {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object[]]$Students,
        [Parameter(Mandatory)]$Config,
        [switch]$CheckAccounts,
        [scriptblock]$AccountChecker
    )

    $errors = [System.Collections.Generic.List[string]]::new()
    $accountResults = [System.Collections.Generic.List[object]]::new()

    if ($Students.Count -ne $Config.ExpectedStudents) {
        $errors.Add("学生数量必须恰好为 $($Config.ExpectedStudents)，当前为 $($Students.Count)。")
    }

    $seen = @{}
    for ($index = 0; $index -lt $Students.Count; $index++) {
        $student = $Students[$index]
        $row = $index + 2
        $group = [string]$student.group_id
        $name = [string]$student.student_name
        $username = [string]$student.github_username

        if ($group -notin $Config.AllowedGroups) {
            $errors.Add("第 $row 行小组编号 '$group' 无效，只允许 01 至 06。")
        }
        if ([string]::IsNullOrWhiteSpace($name)) {
            $errors.Add("第 $row 行学生姓名为空。")
        }
        if (-not (Test-GitHubUsernameFormat -Username $username)) {
            $errors.Add("第 $row 行 GitHub 用户名 '$username' 为空或格式无效。")
            continue
        }
        if ($username -match '(?i)^(github[_-]?user|user[_-]?\d+|student[_-]?\d+|username|todo|待填写)') {
            $errors.Add("第 $row 行 GitHub 用户名 '$username' 是占位值，禁止用于正式邀请。")
        }

        $key = $username.ToLowerInvariant()
        if ($seen.ContainsKey($key)) {
            $errors.Add("GitHub 用户名 '$username' 重复（第 $($seen[$key]) 行与第 $row 行）。")
        }
        else {
            $seen[$key] = $row
        }
    }

    if ($CheckAccounts -and $errors.Count -eq 0) {
        if (-not $AccountChecker) {
            $AccountChecker = { param($name) Test-GitHubUserExists -Username $name }
        }
        foreach ($student in $Students) {
            try {
                $exists = [bool](& $AccountChecker $student.github_username)
                $accountResults.Add([pscustomobject]@{ Username = $student.github_username; Exists = $exists; Error = '' })
                if (-not $exists) {
                    $errors.Add("GitHub 用户不存在或不可访问：$($student.github_username)")
                }
            }
            catch {
                $message = $_.Exception.Message
                $accountResults.Add([pscustomobject]@{ Username = $student.github_username; Exists = $false; Error = $message })
                $errors.Add("检查 GitHub 用户 '$($student.github_username)' 失败：$message")
            }
        }
    }

    $summary = @($Config.AllowedGroups | ForEach-Object {
        $groupId = $_
        [pscustomobject]@{
            GroupId = $groupId
            Count   = @($Students | Where-Object { $_.group_id -eq $groupId }).Count
        }
    })

    [pscustomobject]@{
        IsValid        = ($errors.Count -eq 0)
        Errors         = @($errors)
        Students       = @($Students)
        GroupSummary   = $summary
        AccountResults = @($accountResults)
    }
}

function Invoke-GitHubCli {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [switch]$AllowFailure
    )

    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw '未安装 GitHub CLI（gh）。请先执行 winget install --id GitHub.cli。'
    }

    $output = @(& gh @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    $text = ($output | ForEach-Object { [string]$_ }) -join [Environment]::NewLine
    $result = [pscustomobject]@{ ExitCode = $exitCode; Output = $text }
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw "GitHub CLI 命令失败（退出码 $exitCode）：$text"
    }
    return $result
}

function Get-GitHubRepositoryState {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$FullName)

    $result = Invoke-GitHubCli -Arguments @('repo', 'view', $FullName, '--json', 'nameWithOwner,visibility,defaultBranchRef,url') -AllowFailure
    if ($result.ExitCode -ne 0) {
        return [pscustomobject]@{ Exists = $false; FullName = $FullName; Visibility = ''; DefaultBranch = ''; Url = ''; Error = $result.Output }
    }
    $data = $result.Output | ConvertFrom-Json
    $branch = ''
    if ($null -ne $data.defaultBranchRef) { $branch = [string]$data.defaultBranchRef.name }
    [pscustomobject]@{
        Exists        = $true
        FullName      = [string]$data.nameWithOwner
        Visibility    = ([string]$data.visibility).ToLowerInvariant()
        DefaultBranch = $branch
        Url           = [string]$data.url
        Error         = ''
    }
}

function Invoke-PagedGitHubApi {
    param([Parameter(Mandatory)][string]$Endpoint)
    $all = [System.Collections.Generic.List[object]]::new()
    for ($page = 1; $page -le 100; $page++) {
        $separator = '?'
        if ($Endpoint.Contains('?')) { $separator = '&' }
        $result = Invoke-GitHubCli -Arguments @('api', "$Endpoint${separator}per_page=100&page=$page")
        $items = @($result.Output | ConvertFrom-Json)
        foreach ($item in $items) { $all.Add($item) }
        if ($items.Count -lt 100) { break }
    }
    @($all)
}

function Get-GitHubCollaborators {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$FullName)
    @(Invoke-PagedGitHubApi -Endpoint "repos/$FullName/collaborators?affiliation=all")
}

function Get-GitHubPendingInvitations {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$FullName)
    @(Invoke-PagedGitHubApi -Endpoint "repos/$FullName/invitations")
}

function Test-GitHubUserExists {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Username)
    $result = Invoke-GitHubCli -Arguments @('api', "users/$Username", '--silent') -AllowFailure
    return ($result.ExitCode -eq 0)
}

function New-InvitationPlan {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object[]]$Students,
        [object[]]$Collaborators = @(),
        [object[]]$PendingInvitations = @()
    )

    $collaboratorMap = @{}
    foreach ($item in $Collaborators) {
        if ($item.login) { $collaboratorMap[([string]$item.login).ToLowerInvariant()] = $item }
    }
    $invitationMap = @{}
    foreach ($item in $PendingInvitations) {
        if ($item.invitee -and $item.invitee.login) {
            $invitationMap[([string]$item.invitee.login).ToLowerInvariant()] = $item
        }
    }

    @($Students | ForEach-Object {
        $student = $_
        $key = ([string]$student.github_username).ToLowerInvariant()
        $status = 'NeedsInvitation'
        $accepted = $false
        $hasWrite = $false
        $isAdmin = $false
        $detail = ''
        if ($collaboratorMap.ContainsKey($key)) {
            $status = 'Collaborator'
            $accepted = $true
            $permissions = $collaboratorMap[$key].permissions
            if ($permissions) {
                $hasWrite = [bool]($permissions.push -or $permissions.maintain -or $permissions.admin)
                $isAdmin = [bool]$permissions.admin
            }
            $detail = '已是仓库协作者，跳过邀请。'
        }
        elseif ($invitationMap.ContainsKey($key)) {
            $status = 'PendingInvitation'
            $detail = '已有待接受邀请，跳过重复邀请。'
        }
        [pscustomobject]@{
            GroupId       = $student.group_id
            StudentName   = $student.student_name
            GitHubUsername = $student.github_username
            Status        = $status
            Accepted      = $accepted
            HasWrite      = $hasWrite
            IsAdmin       = $isAdmin
            Error         = ''
            Detail        = $detail
        }
    })
}

function Invoke-InvitationPlan {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object[]]$Plan,
        [Parameter(Mandatory)][string]$FullName,
        [switch]$Apply,
        [scriptblock]$InviteAction
    )

    if (-not $InviteAction) {
        $InviteAction = {
            param($repository, $username)
            Invoke-GitHubCli -Arguments @('api', '--method', 'PUT', "repos/$repository/collaborators/$username", '-f', 'permission=push', '--silent') | Out-Null
        }
    }

    @($Plan | ForEach-Object {
        $row = $_.PSObject.Copy()
        if ($row.Status -eq 'NeedsInvitation') {
            if (-not $Apply) {
                $row.Status = 'DryRunWouldInvite'
                $row.Detail = 'Dry-Run：将授予 push（写入）权限。'
            }
            else {
                try {
                    & $InviteAction $FullName $row.GitHubUsername
                    $row.Status = 'InvitationSent'
                    $row.Detail = '邀请已发送，等待学生接受。'
                }
                catch {
                    $row.Status = 'InvitationFailed'
                    $row.Error = $_.Exception.Message
                    $row.Detail = '邀请失败。'
                }
            }
        }
        $row
    })
}

function Write-Utf8File {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Content)
    $encoding = [System.Text.UTF8Encoding]::new($true)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Write-DeploymentReports {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object[]]$Results,
        [Parameter(Mandatory)]$Config,
        [Parameter(Mandatory)][string]$ReportDirectory,
        [datetime]$DeploymentTime = (Get-Date)
    )

    if (-not (Test-Path -LiteralPath $ReportDirectory)) {
        New-Item -ItemType Directory -Path $ReportDirectory -Force | Out-Null
    }
    $records = @($Results | ForEach-Object {
        [pscustomobject][ordered]@{
            github_owner       = $Config.Owner
            repository_url     = $Config.RepositoryUrl
            visibility         = $Config.Visibility
            student_name       = $_.StudentName
            github_username    = $_.GitHubUsername
            group_id           = $_.GroupId
            invitation_status  = $_.Status
            invitation_accepted = [bool]$_.Accepted
            has_write_access   = [bool]$_.HasWrite
            is_admin           = if ($_.PSObject.Properties.Name -contains 'IsAdmin') { [bool]$_.IsAdmin } else { $false }
            error_message      = $_.Error
            deployment_time    = $DeploymentTime.ToString('yyyy-MM-ddTHH:mm:sszzz')
        }
    })

    $csvPath = Join-Path $ReportDirectory 'deployment-report.csv'
    $jsonPath = Join-Path $ReportDirectory 'deployment-report.json'
    $csvText = ($records | ConvertTo-Csv -NoTypeInformation) -join [Environment]::NewLine
    $jsonText = $records | ConvertTo-Json -Depth 6
    Write-Utf8File -Path $csvPath -Content ($csvText + [Environment]::NewLine)
    Write-Utf8File -Path $jsonPath -Content ($jsonText + [Environment]::NewLine)
    [pscustomobject]@{ CsvPath = $csvPath; JsonPath = $jsonPath; Records = $records }
}

function Write-DeploymentLog {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$ReportDirectory,
        [Parameter(Mandatory)][string]$Message,
        [ValidateSet('INFO', 'WARN', 'ERROR')][string]$Level = 'INFO'
    )
    if (-not (Test-Path -LiteralPath $ReportDirectory)) {
        New-Item -ItemType Directory -Path $ReportDirectory -Force | Out-Null
    }
    $path = Join-Path $ReportDirectory 'deployment.log'
    $line = '[{0}] [{1}] {2}{3}' -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz'), $Level, $Message, [Environment]::NewLine
    [System.IO.File]::AppendAllText($path, $line, [System.Text.UTF8Encoding]::new($true))
    return $path
}

function Get-RequiredCourseDirectories {
    [CmdletBinding()]
    param()
    $paths = [System.Collections.Generic.List[string]]::new()
    foreach ($group in '01', '02', '03', '04', '05', '06') {
        foreach ($child in '', 'src', 'src/python', 'src/cpp', 'include', 'tests', 'examples', 'docs', 'data') {
            if ($child) { $paths.Add("group-$group/$child") } else { $paths.Add("group-$group") }
        }
    }
    $paths.Add('common/examples')
    $paths.Add('common/templates')
    $paths.Add('docs')
    $paths.Add('reports')
    @($paths)
}

Export-ModuleMember -Function @(
    'Get-CourseConfig', 'Import-StudentRoster', 'Test-StudentRoster', 'Test-GitHubUsernameFormat',
    'Invoke-GitHubCli', 'Get-GitHubRepositoryState', 'Get-GitHubCollaborators',
    'Get-GitHubPendingInvitations', 'Test-GitHubUserExists', 'New-InvitationPlan',
    'Invoke-InvitationPlan', 'Write-DeploymentReports', 'Write-DeploymentLog',
    'Get-RequiredCourseDirectories'
)
