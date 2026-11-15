[CmdletBinding()]
param(
    [int]$CallbackPort = 18774,
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$StatePath = Join-Path $RepoRoot "build/ui-stand-state.json"
$EnvPath = Join-Path $RepoRoot "build/native-stand.env"
$RuntimeStatePath = Join-Path $RepoRoot "build/game-accounts-admin-demo-state.json"
$StdoutPath = Join-Path $RepoRoot "build/game-accounts-admin-demo.stdout.log"
$StderrPath = Join-Path $RepoRoot "build/game-accounts-admin-demo.stderr.log"
$ReportDir = Join-Path $RepoRoot "build/reports"
$ReportPath = Join-Path $ReportDir "game-accounts-admin-deploy.json"
$PluginArchive = Join-Path $RepoRoot "build/plugins/mattergram-menu-0.2.0.tar.gz"
$OverviewMessage = -join @(
    [char]0x041E, [char]0x0431, [char]0x0437, [char]0x043E, [char]0x0440
)

function Stop-OwnedRunner([int]$ProcessId) {
    if ($ProcessId -le 0) { return }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $process) { return }
    if ($process.CommandLine -notlike "*game_accounts_admin_bot.py*") {
        Write-Warning "Ignoring stale runner PID $ProcessId because it now belongs to another process."
        return
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Invoke-JsonRequest {
    param(
        [string]$Method,
        [string]$Uri,
        [hashtable]$Headers = @{},
        [object]$Body = $null
    )
    $arguments = @{
        Method = $Method
        Uri = $Uri
        Headers = $Headers
        UseBasicParsing = $true
    }
    if ($null -ne $Body) {
        $arguments.ContentType = "application/json; charset=utf-8"
        $json = ConvertTo-Json -InputObject $Body -Depth 10 -Compress
        $arguments.Body = [System.Text.Encoding]::UTF8.GetBytes($json)
    }
    return Invoke-WebRequest @arguments
}

Set-Location $RepoRoot
New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null
if (-not (Test-Path -LiteralPath $StatePath)) { throw "Missing $StatePath. Prepare the owned UI stand first." }
if (-not (Test-Path -LiteralPath $EnvPath)) { throw "Missing $EnvPath. Prepare the native stand first." }
if ($CallbackPort -lt 1024 -or $CallbackPort -gt 65535) { throw "CallbackPort must be between 1024 and 65535." }

if (Test-Path -LiteralPath $RuntimeStatePath) {
    $previous = Get-Content -LiteralPath $RuntimeStatePath -Raw | ConvertFrom-Json
    $ownedPids = @($previous.listener_pid, $previous.launcher_pid) | Where-Object { $_ } | Select-Object -Unique
    foreach ($ownedPid in $ownedPids) { Stop-OwnedRunner ([int]$ownedPid) }
}

$portOwner = Get-NetTCPConnection -State Listen -LocalPort $CallbackPort -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -ne $portOwner) {
    throw "Callback port $CallbackPort is already in use by PID $($portOwner.OwningProcess)."
}

docker info --format '{{.ServerVersion}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Docker engine is not available." }
$compose = @(
    "compose", "--env-file", $EnvPath,
    "-p", "mattergram-native-stand",
    "-f", (Join-Path $RepoRoot "infra/native-stand.compose.yml"),
    "-f", (Join-Path $RepoRoot "infra/ui-stand.compose.yml"),
    "-f", (Join-Path $RepoRoot "infra/ui-client-matrix.compose.yml")
)
& docker @compose --profile ui up -d --wait db_ui mattermost_ui
if ($LASTEXITCODE -ne 0) { throw "Owned Mattermost UI stand failed to start." }
$portOutput = (& docker @compose port mattermost_ui 8065 | Select-Object -First 1).Trim()
if ($portOutput -notmatch "127\.0\.0\.1:(?<port>\d+)$") {
    throw "Unexpected Mattermost port mapping: $portOutput"
}
$MattermostPort = [int]$Matches.port
$BaseUrl = "http://127.0.0.1:$MattermostPort"
$CallbackBase = "http://host.docker.internal:$CallbackPort"
$mattermostContainer = (& docker @compose ps -q mattermost_ui).Trim()
if (-not $mattermostContainer) { throw "Owned Mattermost UI container was not found." }

$pingReady = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    try {
        $ping = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/api/v4/system/ping" -TimeoutSec 2
        if ($ping.StatusCode -eq 200) { $pingReady = $true; break }
    } catch {}
    Start-Sleep -Milliseconds 500
}
if (-not $pingReady) { throw "Mattermost did not become ready at $BaseUrl." }

$fixture = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
$loginResponse = Invoke-JsonRequest -Method POST -Uri "$BaseUrl/api/v4/users/login" -Body @{
    login_id = $fixture.admin_username
    password = $fixture.admin_password
}
if ($loginResponse.StatusCode -ne 200) { throw "Administrator login smoke check failed." }
$adminToken = [string]$loginResponse.Headers["Token"]
if (-not $adminToken) { throw "Administrator login did not return a token." }
$headers = @{ Authorization = "Bearer $adminToken" }
& docker exec $mattermostContainer mmctl config set PluginSettings.Enable true --local | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not enable Mattermost plugins locally." }
& docker exec $mattermostContainer mmctl config set PluginSettings.EnableUploads true --local | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not enable local plugin uploads." }
& docker exec $mattermostContainer mmctl config set PluginSettings.RequirePluginSignature false --local | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not allow the unsigned owned development plugin." }
$configResponse = Invoke-JsonRequest -Method PUT -Uri "$BaseUrl/api/v4/config/patch" -Headers $headers -Body @{
    ServiceSettings = @{ SiteURL = $BaseUrl }
}
$updatedConfig = $configResponse.Content | ConvertFrom-Json
if ($configResponse.StatusCode -ne 200 -or $updatedConfig.ServiceSettings.SiteURL -ne $BaseUrl) {
    throw "Mattermost SiteURL does not match the current loopback URL."
}
if (-not (Test-Path -LiteralPath $PluginArchive)) {
    throw "Missing Mattergram Menu bundle: $PluginArchive"
}
$installedPluginsResponse = Invoke-JsonRequest -Method GET -Uri "$BaseUrl/api/v4/plugins" -Headers $headers
$installedPlugins = $installedPluginsResponse.Content | ConvertFrom-Json
$installedMenu = @(@($installedPlugins.active) + @($installedPlugins.inactive) | Where-Object { $_.id -eq "com.mattergram.menu" })
if ($installedMenu.Count -gt 1) { throw "Multiple Mattergram Menu plugin records were returned." }
if ($installedMenu.Count -eq 1 -and $installedMenu[0].version -ne "0.2.0") {
    Invoke-JsonRequest -Method DELETE -Uri "$BaseUrl/api/v4/plugins/com.mattergram.menu" -Headers $headers | Out-Null
    $installedMenu = @()
}
if ($installedMenu.Count -eq 0) {
    $pluginUpload = & curl.exe --silent --show-error --fail-with-body `
        -H "Authorization: Bearer $adminToken" `
        -F "plugin=@$PluginArchive" `
        "$BaseUrl/api/v4/plugins" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Mattergram Menu plugin upload failed: $pluginUpload" }
}
$enablePlugin = Invoke-JsonRequest -Method POST -Uri "$BaseUrl/api/v4/plugins/com.mattergram.menu/enable" -Headers $headers
if ($enablePlugin.StatusCode -ne 200) { throw "Mattergram Menu plugin enable failed." }
$pluginsResponse = Invoke-JsonRequest -Method GET -Uri "$BaseUrl/api/v4/plugins" -Headers $headers
$plugins = $pluginsResponse.Content | ConvertFrom-Json
$activeMenu = @($plugins.active | Where-Object { $_.id -eq "com.mattergram.menu" -and $_.version -eq "0.2.0" })
if ($activeMenu.Count -ne 1) { throw "Mattergram Menu 0.2.0 is not active." }
$directResponse = Invoke-JsonRequest -Method POST -Uri "$BaseUrl/api/v4/channels/direct" -Headers $headers -Body @(
    $fixture.admin_id,
    $fixture.bot_id
)
$directChannel = $directResponse.Content | ConvertFrom-Json
if ($directResponse.StatusCode -ne 201 -or $directChannel.type -ne "D" -or -not $directChannel.id) {
    throw "Could not create the administrator-to-bot direct channel."
}
$AdminChannelId = [string]$directChannel.id
$botUserResponse = Invoke-JsonRequest -Method GET -Uri "$BaseUrl/api/v4/users/$($fixture.bot_id)" -Headers $headers
$botUser = $botUserResponse.Content | ConvertFrom-Json
if (-not $botUser.username) { throw "Bot username is missing." }
$BotDirectUrl = "$BaseUrl/mattergram-ui/messages/@$($botUser.username)"

$uv = Get-Command uv -ErrorAction Stop
Remove-Item -LiteralPath $StdoutPath, $StderrPath -Force -ErrorAction SilentlyContinue
$runnerArguments = @(
    "run", "python", "examples/game_accounts_admin_bot.py",
    "--base-url", $BaseUrl,
    "--callback-base", $CallbackBase,
    "--port", "$CallbackPort"
)
$launcher = Start-Process -FilePath $uv.Source -ArgumentList $runnerArguments -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput $StdoutPath -RedirectStandardError $StderrPath -PassThru
$listenerPid = 0
try {
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($launcher.HasExited) {
            throw "Game admin runner exited during startup. See $StderrPath."
        }
        $listener = Get-NetTCPConnection -State Listen -LocalPort $CallbackPort -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $listener) { $listenerPid = [int]$listener.OwningProcess; break }
        Start-Sleep -Milliseconds 500
    }
    if ($listenerPid -le 0) { throw "Game admin callback did not listen on port $CallbackPort." }

    [ordered]@{
        launcher_pid = $launcher.Id
        listener_pid = $listenerPid
        callback_port = $CallbackPort
        base_url = $BaseUrl
        callback_base = $CallbackBase
        started_at = (Get-Date).ToString("o")
    } | ConvertTo-Json | Set-Content -LiteralPath $RuntimeStatePath -Encoding utf8

    $commandsResponse = Invoke-JsonRequest -Method GET -Uri "$BaseUrl/api/v4/commands?team_id=$($fixture.team_id)&custom_only=true" -Headers $headers
    $allCommands = $commandsResponse.Content | ConvertFrom-Json
    $commands = @($allCommands | Where-Object { $_.trigger -eq "game-admin" })
    if ($commands.Count -ne 1) { throw "Expected exactly one /game-admin command; found $($commands.Count)." }
    $expectedCommandUrl = "$CallbackBase/mattergram/slash"
    if ($commands[0].url -ne $expectedCommandUrl) { throw "Slash command URL does not match the current callback." }

    $dmKeyboardResponse = Invoke-JsonRequest -Method GET -Uri "$BaseUrl/plugins/com.mattergram.menu/api/v1/channels/$AdminChannelId/keyboard" -Headers $headers
    $dmKeyboard = $dmKeyboardResponse.Content | ConvertFrom-Json
    if ($dmKeyboard.channel_id -ne $AdminChannelId -or $null -eq $dmKeyboard.keyboard) {
        throw "Game-admin keyboard is not installed in the bot DM."
    }
    $publicKeyboardResponse = Invoke-JsonRequest -Method GET -Uri "$BaseUrl/plugins/com.mattergram.menu/api/v1/channels/$($fixture.channel_id)/keyboard" -Headers $headers
    $publicKeyboard = $publicKeyboardResponse.Content | ConvertFrom-Json
    if ($null -ne $publicKeyboard.keyboard) {
        throw "Game-admin keyboard is still installed in the shared fixture channel."
    }

    $slashPostsBeforeResponse = Invoke-JsonRequest -Method GET -Uri "$BaseUrl/api/v4/channels/$($fixture.channel_id)/posts?page=0&per_page=30" -Headers $headers
    $slashPostsBefore = $slashPostsBeforeResponse.Content | ConvertFrom-Json
    $knownSlashPostIds = @{}
    foreach ($knownPostId in $slashPostsBefore.order) { $knownSlashPostIds[$knownPostId] = $true }
    $slashExecuteResponse = Invoke-JsonRequest -Method POST -Uri "$BaseUrl/api/v4/commands/execute" -Headers $headers -Body @{
        channel_id = $fixture.channel_id
        command = "/game-admin"
    }
    if ($slashExecuteResponse.StatusCode -ne 200) { throw "Slash command execution smoke check failed." }
    $slashPostId = $null
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        $slashPostsResponse = Invoke-JsonRequest -Method GET -Uri "$BaseUrl/api/v4/channels/$($fixture.channel_id)/posts?page=0&per_page=30" -Headers $headers
        $slashPosts = $slashPostsResponse.Content | ConvertFrom-Json
        foreach ($postId in $slashPosts.order) {
            $post = $slashPosts.posts.$postId
            if (-not $knownSlashPostIds.ContainsKey($postId) -and $post.user_id -eq $fixture.bot_id -and $null -ne $post.props.mm_blocks) {
                $slashPostId = $postId
                break
            }
        }
        if ($slashPostId) { break }
        Start-Sleep -Milliseconds 250
    }
    if (-not $slashPostId) { throw "No structured bot post was observed after the team slash command." }
    Invoke-JsonRequest -Method DELETE -Uri "$BaseUrl/api/v4/posts/$slashPostId" -Headers $headers | Out-Null

    $postsBeforeResponse = Invoke-JsonRequest -Method GET -Uri "$BaseUrl/api/v4/channels/$AdminChannelId/posts?page=0&per_page=30" -Headers $headers
    $postsBefore = $postsBeforeResponse.Content | ConvertFrom-Json
    $knownPostIds = @{}
    foreach ($knownPostId in $postsBefore.order) { $knownPostIds[$knownPostId] = $true }
    $executeResponse = Invoke-JsonRequest -Method POST -Uri "$BaseUrl/api/v4/posts" -Headers $headers -Body @{
        channel_id = $AdminChannelId
        message = $OverviewMessage
    }
    $smokeTrigger = $executeResponse.Content | ConvertFrom-Json
    if ($executeResponse.StatusCode -ne 201 -or -not $smokeTrigger.id) {
        throw "Keyboard-message execution smoke check failed."
    }

    $smokePostId = $null
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        $postsResponse = Invoke-JsonRequest -Method GET -Uri "$BaseUrl/api/v4/channels/$AdminChannelId/posts?page=0&per_page=30" -Headers $headers
        $posts = $postsResponse.Content | ConvertFrom-Json
        foreach ($postId in $posts.order) {
            $post = $posts.posts.$postId
            if (-not $knownPostIds.ContainsKey($postId) -and $post.user_id -eq $fixture.bot_id -and $null -ne $post.props.mm_blocks) {
                $smokePostId = $postId
                break
            }
        }
        if ($smokePostId) { break }
        Start-Sleep -Milliseconds 250
    }
    if (-not $smokePostId) { throw "No new structured bot post was observed after the DM keyboard message." }
    Invoke-JsonRequest -Method DELETE -Uri "$BaseUrl/api/v4/posts/$smokePostId" -Headers $headers | Out-Null
    Invoke-JsonRequest -Method DELETE -Uri "$BaseUrl/api/v4/posts/$($smokeTrigger.id)" -Headers $headers | Out-Null

    $browserResult = $null
    if (-not $SkipBrowser) {
        $env:MM_ADMIN_BASE_URL = $BaseUrl
        $env:MM_ADMIN_CHANNEL_ID = $AdminChannelId
        $env:MM_ADMIN_CHANNEL_URL = $BotDirectUrl
        & node (Join-Path $RepoRoot "tools/verify_game_accounts_admin_browser.cjs")
        if ($LASTEXITCODE -ne 0) { throw "Browser smoke check failed." }
        $browserResult = Get-Content -LiteralPath (Join-Path $RepoRoot "output/playwright/game-accounts-admin/result.json") -Raw | ConvertFrom-Json
        if (
            -not $browserResult.home_visible -or
            -not $browserResult.keyboard_visible -or
            -not $browserResult.keyboard_navigation -or
            -not $browserResult.keyboard_new_posts -or
            -not $browserResult.bot_dm -or
            -not $browserResult.accounts_visible -or
            -not $browserResult.form_cancel_restored_source
        ) {
            throw "Browser smoke check did not complete navigation and form-cancel recovery."
        }
    }

    $finalBeforeResponse = Invoke-JsonRequest -Method GET -Uri "$BaseUrl/api/v4/channels/$AdminChannelId/posts?page=0&per_page=30" -Headers $headers
    $finalBefore = $finalBeforeResponse.Content | ConvertFrom-Json
    $knownFinalPostIds = @{}
    foreach ($knownPostId in $finalBefore.order) { $knownFinalPostIds[$knownPostId] = $true }
    $finalExecute = Invoke-JsonRequest -Method POST -Uri "$BaseUrl/api/v4/posts" -Headers $headers -Body @{
        channel_id = $AdminChannelId
        message = $OverviewMessage
    }
    $finalTrigger = $finalExecute.Content | ConvertFrom-Json
    if ($finalExecute.StatusCode -ne 201 -or -not $finalTrigger.id) {
        throw "Final bot-DM keyboard message failed."
    }
    $finalPostId = $null
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        $finalPostsResponse = Invoke-JsonRequest -Method GET -Uri "$BaseUrl/api/v4/channels/$AdminChannelId/posts?page=0&per_page=30" -Headers $headers
        $finalPosts = $finalPostsResponse.Content | ConvertFrom-Json
        foreach ($postId in $finalPosts.order) {
            $post = $finalPosts.posts.$postId
            if (-not $knownFinalPostIds.ContainsKey($postId) -and $post.user_id -eq $fixture.bot_id -and $null -ne $post.props.mm_blocks) {
                $finalPostId = $postId
                break
            }
        }
        if ($finalPostId) { break }
        Start-Sleep -Milliseconds 250
    }
    if (-not $finalPostId) { throw "Final user-facing admin post was not created." }
    Invoke-JsonRequest -Method DELETE -Uri "$BaseUrl/api/v4/posts/$($finalTrigger.id)" -Headers $headers | Out-Null

    $channelUrl = $BotDirectUrl
    $finalPostUrl = "$BaseUrl/mattergram-ui/pl/$finalPostId"
    $report = [ordered]@{
        status = "passed"
        checked_at = (Get-Date).ToString("o")
        base_url = $BaseUrl
        channel_url = $channelUrl
        final_post_url = $finalPostUrl
        final_post_id = $finalPostId
        command = "/game-admin"
        callback_url = $expectedCommandUrl
        admin_login = $fixture.admin_username
        checks = [ordered]@{
            docker = $true
            mattermost_ping = $true
            site_url = $true
            admin_login = $true
            command_registration = $true
            composer_keyboard_plugin = $true
            composer_keyboard_bot_dm = $true
            composer_keyboard_absent_from_shared_channel = $true
            slash_execution = $true
            keyboard_message_execution = $true
            structured_post = $true
            browser_home = if ($SkipBrowser) { $null } else { [bool]$browserResult.home_visible }
            browser_keyboard = if ($SkipBrowser) { $null } else { [bool]$browserResult.keyboard_visible }
            browser_keyboard_navigation = if ($SkipBrowser) { $null } else { [bool]$browserResult.keyboard_navigation }
            browser_keyboard_new_posts = if ($SkipBrowser) { $null } else { [bool]$browserResult.keyboard_new_posts }
            browser_bot_dm = if ($SkipBrowser) { $null } else { [bool]$browserResult.bot_dm }
            browser_accounts = if ($SkipBrowser) { $null } else { [bool]$browserResult.accounts_visible }
            browser_form_cancel = if ($SkipBrowser) { $null } else { [bool]$browserResult.form_cancel_restored_source }
        }
        runtime_state = $RuntimeStatePath
        stdout_log = $StdoutPath
        stderr_log = $StderrPath
    }
    $report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $ReportPath -Encoding utf8
    Write-Output "READY $channelUrl"
    Write-Output "POST $finalPostUrl"
    Write-Output "LOGIN $($fixture.admin_username)"
    Write-Output "PASSWORD $($fixture.admin_password)"
    Write-Output "COMMAND /game-admin"
    Write-Output "REPORT $ReportPath"
} catch {
    Stop-OwnedRunner $listenerPid
    if ($launcher.Id -ne $listenerPid) { Stop-OwnedRunner $launcher.Id }
    [ordered]@{
        status = "failed"
        checked_at = (Get-Date).ToString("o")
        error = $_.Exception.Message
        stdout_log = $StdoutPath
        stderr_log = $StderrPath
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ReportPath -Encoding utf8
    throw
}
