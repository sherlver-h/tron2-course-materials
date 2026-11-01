@{
    RootModule        = 'TronCourseTools.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = '63d4a0a9-f365-4f91-8ae2-a33a3fdff01c'
    Author            = 'TRON2 Course'
    PowerShellVersion = '5.1'
    FunctionsToExport = @(
        'Get-CourseConfig',
        'Import-StudentRoster',
        'Test-StudentRoster',
        'Test-GitHubUsernameFormat',
        'Invoke-GitHubCli',
        'Get-GitHubRepositoryState',
        'Get-GitHubCollaborators',
        'Get-GitHubPendingInvitations',
        'Test-GitHubUserExists',
        'New-InvitationPlan',
        'Invoke-InvitationPlan',
        'Write-DeploymentReports',
        'Write-DeploymentLog',
        'Get-RequiredCourseDirectories'
    )
}
