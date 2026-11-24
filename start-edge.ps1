$ErrorActionPreference = "Stop"

$cdpUrl = [Environment]::GetEnvironmentVariable("NAUKRI_CDP_URL")
$envFile = Join-Path $PSScriptRoot ".env"
if ([string]::IsNullOrWhiteSpace($cdpUrl) -and (Test-Path -LiteralPath $envFile)) {
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^\s*NAUKRI_CDP_URL\s*=\s*(.*?)\s*$') {
            $cdpUrl = $Matches[1].Trim()
            if ($cdpUrl.Length -ge 2) {
                $first = $cdpUrl.Substring(0, 1)
                $last = $cdpUrl.Substring($cdpUrl.Length - 1, 1)
                if (($first -eq '"' -and $last -eq '"') -or
                    ($first -eq "'" -and $last -eq "'")) {
                    $cdpUrl = $cdpUrl.Substring(1, $cdpUrl.Length - 2)
                }
            }
            break
        }
    }
}
if ([string]::IsNullOrWhiteSpace($cdpUrl)) {
    $cdpUrl = "http://127.0.0.1:9222"
}
$cdpUri = $null
if (-not [Uri]::TryCreate($cdpUrl, [UriKind]::Absolute, [ref]$cdpUri) -or
    $cdpUri.Scheme -ne "http" -or
    $cdpUri.IsDefaultPort -or
    $cdpUri.Host -notin @("127.0.0.1", "localhost", "::1")) {
    Write-Host "NAUKRI_CDP_URL must be an HTTP URL pointing to localhost." -ForegroundColor Red
    exit 1
}
$port = $cdpUri.Port
$profile = Join-Path $PSScriptRoot "data\edge_profile"
$resdexUrl = "https://resdex.naukri.com/v3?activeTab=advSrch"

function Test-CdpPort {
    $client = New-Object System.Net.Sockets.TcpClient
    $connect = $null
    try {
        $connect = $client.BeginConnect("127.0.0.1", $port, $null, $null)
        if (-not $connect.AsyncWaitHandle.WaitOne(500)) {
            return $false
        }
        $client.EndConnect($connect)
        return $client.Connected
    }
    catch {
        return $false
    }
    finally {
        if ($null -ne $connect) {
            $connect.AsyncWaitHandle.Close()
        }
        $client.Close()
    }
}

if (Test-CdpPort) {
    Write-Host "Edge/Chrome is already answering on CDP port $port."
    exit 0
}

if (Get-Process -Name "msedge" -ErrorAction SilentlyContinue) {
    Write-Host "Edge is already running without the debug port. Close all Edge windows first, then start it with:" -ForegroundColor Yellow
    Write-Host ('msedge.exe --remote-debugging-port=' + $port + ' --user-data-dir="' + $profile + '" "' + $resdexUrl + '"')
    exit 1
}

$edgePath = $null
$edgeCommand = Get-Command "msedge.exe" -ErrorAction SilentlyContinue
if ($null -ne $edgeCommand) {
    $edgePath = $edgeCommand.Source
}
if ([string]::IsNullOrWhiteSpace($edgePath)) {
    $programFiles = [Environment]::GetEnvironmentVariable("ProgramFiles")
    $programFilesX86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    $candidates = @(
        (Join-Path $programFiles "Microsoft\Edge\Application\msedge.exe"),
        (Join-Path $programFilesX86 "Microsoft\Edge\Application\msedge.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            $edgePath = $candidate
            break
        }
    }
}
if ([string]::IsNullOrWhiteSpace($edgePath)) {
    Write-Host "Could not find msedge.exe. Install Microsoft Edge or start it manually with the documented CDP arguments." -ForegroundColor Red
    exit 1
}

$arguments = @(
    "--remote-debugging-port=$port",
    "--user-data-dir=`"$profile`"",
    $resdexUrl
)
Start-Process -FilePath $edgePath -ArgumentList $arguments

$deadline = (Get-Date).AddSeconds(10)
while ((Get-Date) -lt $deadline) {
    if (Test-CdpPort) {
        Write-Host "Edge is answering on CDP port $port. Log in to Resdex in that browser if needed."
        exit 0
    }
    Start-Sleep -Seconds 1
}

Write-Host "Edge did not open CDP port $port within 10 seconds." -ForegroundColor Red
exit 1
