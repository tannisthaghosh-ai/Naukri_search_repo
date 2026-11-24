[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [Alias("Role")]
    [ValidateNotNullOrEmpty()]
    [ValidateLength(1, 200)]
    [string]$Keyword,

    [ValidateRange(1, 100)]
    [int]$MaxPages = 5,

    [ValidateLength(0, 100)]
    [string]$Location,

    [string]$ApiUrl,

    [string]$OutDir = ".\output",

    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$Keyword = $Keyword.Trim()
if ([string]::IsNullOrWhiteSpace($Keyword)) {
    Write-Host "Keyword must contain at least one non-space character." -ForegroundColor Red
    exit 2
}

function Get-ProjectSetting {
    param([string]$Name)

    $fromEnvironment = [Environment]::GetEnvironmentVariable($Name)
    if (-not [string]::IsNullOrWhiteSpace($fromEnvironment)) {
        return $fromEnvironment.Trim()
    }

    $envFile = Join-Path $PSScriptRoot ".env"
    if (Test-Path -LiteralPath $envFile) {
        foreach ($line in Get-Content -LiteralPath $envFile) {
            $settingPattern = '^\s*' + [regex]::Escape($Name) + '\s*=\s*(.*?)\s*$'
            if ($line -match $settingPattern) {
                $value = $Matches[1].Trim()
                if ($value.Length -ge 2) {
                    $first = $value.Substring(0, 1)
                    $last = $value.Substring($value.Length - 1, 1)
                    if (($first -eq '"' -and $last -eq '"') -or
                        ($first -eq "'" -and $last -eq "'")) {
                        $value = $value.Substring(1, $value.Length - 2)
                    }
                    elseif ($value -match '\s#') {
                        $value = ($value -split '\s#', 2)[0].TrimEnd()
                    }
                }
                if (-not [string]::IsNullOrWhiteSpace($value)) {
                    return $value
                }
            }
        }
    }

    return $null
}

function Get-StatusCode {
    param([System.Management.Automation.ErrorRecord]$ErrorRecord)
    $response = $ErrorRecord.Exception.Response
    if ($null -eq $response -or $null -eq $response.StatusCode) {
        return 0
    }
    return [int]$response.StatusCode
}

function ConvertTo-CandidateRows {
    param([object[]]$Candidates)

    foreach ($candidate in $Candidates) {
        if ($null -eq $candidate) {
            continue
        }
        $skillItems = @()
        if ($null -ne $candidate.skills) {
            $skillItems = @($candidate.skills)
        }
        $skills = ($skillItems | ForEach-Object { [string]$_ }) -join ", "
        if ($skills.Length -gt 50) {
            $skills = $skills.Substring(0, 47) + "..."
        }

        [PSCustomObject]@{
            Name           = $candidate.name
            Experience     = $candidate.experience
            CurrentCompany = $candidate.current_company
            Skills         = $skills
            ProfileUrl     = $candidate.profile_url
            Page           = $candidate.page
        }
    }
}

function Show-CandidatePreview {
    param([object[]]$Candidates)

    $rows = @(ConvertTo-CandidateRows -Candidates $Candidates)
    if ($rows.Count -gt 0) {
        $rows |
            Select-Object -First 10 Name, Experience, CurrentCompany, Skills, ProfileUrl, Page |
            Format-Table -AutoSize
    }
}

function Save-Workbook {
    param([string]$JobId)

    if ([IO.Path]::IsPathRooted($OutDir)) {
        $outputDirectory = $OutDir
    }
    else {
        $outputDirectory = Join-Path $PSScriptRoot $OutDir
    }
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

    $safeKeyword = $Keyword
    foreach ($character in [IO.Path]::GetInvalidFileNameChars()) {
        $safeKeyword = $safeKeyword.Replace([string]$character, "_")
    }
    $safeKeyword = ($safeKeyword -replace '\s+', '_').Trim("_")
    if ([string]::IsNullOrWhiteSpace($safeKeyword)) {
        $safeKeyword = "search"
    }
    $timestamp = Get-Date -Format "yyyyMMdd_HHmm"
    $xlsxPath = Join-Path $outputDirectory ($safeKeyword + "_" + $timestamp + ".xlsx")
    $exportUrl = $baseUrl + "/api/search/" +
        [Uri]::EscapeDataString($JobId) + "/export.xlsx"

    try {
        Invoke-WebRequest -Method Get `
            -Uri $exportUrl `
            -Headers $headers `
            -OutFile $xlsxPath `
            -UseBasicParsing `
            -ErrorAction Stop | Out-Null
    }
    catch {
        if (Test-Path -LiteralPath $xlsxPath) {
            Remove-Item -LiteralPath $xlsxPath
        }
        throw
    }

    if (-not (Test-Path -LiteralPath $xlsxPath) -or
        (Get-Item -LiteralPath $xlsxPath).Length -eq 0) {
        throw "The API returned an empty workbook."
    }

    Write-Host ("Excel file saved: " + $xlsxPath)
    if (-not $NoOpen) {
        try {
            Invoke-Item -LiteralPath $xlsxPath
        }
        catch {
            Write-Host ("Workbook saved, but it could not be opened automatically: " +
                $_.Exception.Message) -ForegroundColor Yellow
        }
    }
}

try {
    $apiKey = Get-ProjectSetting -Name "API_KEY"
    if ([string]::IsNullOrWhiteSpace($apiKey) -or
        $apiKey -in @("replace-with-a-private-random-secret", "your_super_secret_key_here", "changeme")) {
        throw "API_KEY is missing or still a placeholder. Set a private value in .env."
    }
    if (-not $PSBoundParameters.ContainsKey("ApiUrl")) {
        $ApiUrl = Get-ProjectSetting -Name "API_URL"
        if ([string]::IsNullOrWhiteSpace($ApiUrl)) {
            $ApiUrl = "http://127.0.0.1:8000"
        }
    }
    $apiUri = $null
    if (-not [Uri]::TryCreate($ApiUrl, [UriKind]::Absolute, [ref]$apiUri) -or
        $apiUri.Scheme -ne "http" -or
        $apiUri.Host -notin @("127.0.0.1", "localhost", "::1")) {
        throw "ApiUrl must be an HTTP URL pointing to localhost."
    }
}
catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

$headers = @{ "X-API-KEY" = $apiKey }
$requestBody = @{
    keyword   = $Keyword
    max_pages = $MaxPages
}
if (-not [string]::IsNullOrWhiteSpace($Location)) {
    $requestBody.location = $Location
}
$bodyJson = $requestBody | ConvertTo-Json -Compress
$baseUrl = $ApiUrl.TrimEnd("/")

try {
    $health = Invoke-RestMethod -Method Get `
        -Uri ($baseUrl + "/api/health") `
        -Headers $headers `
        -ErrorAction Stop
}
catch {
    $statusCode = Get-StatusCode -ErrorRecord $_
    if ($statusCode -eq 0) {
        Write-Host "API is not reachable. Start it with: uvicorn app.main:app --port 8000" -ForegroundColor Red
    }
    elseif ($statusCode -eq 401) {
        Write-Host "The API rejected API_KEY. Check the key in .env." -ForegroundColor Red
    }
    else {
        Write-Host ("API health check failed (HTTP " + $statusCode + ").") -ForegroundColor Red
    }
    exit 1
}
if ($health.status -ne "ok") {
    Write-Host "API health check returned an unexpected response." -ForegroundColor Red
    exit 1
}

try {
    $started = Invoke-RestMethod -Method Post `
        -Uri ($baseUrl + "/api/search") `
        -Headers $headers `
        -ContentType "application/json" `
        -Body $bodyJson `
        -ErrorAction Stop
}
catch {
    $statusCode = Get-StatusCode -ErrorRecord $_
    if ($statusCode -eq 409) {
        Write-Host "A search is already running" -ForegroundColor Yellow
        exit 1
    }
    if ($statusCode -eq 0) {
        Write-Host "API is not reachable. Start it with: uvicorn app.main:app --port 8000" -ForegroundColor Red
        exit 1
    }
    Write-Host ("API request failed (HTTP " + $statusCode + ").") -ForegroundColor Red
    exit 1
}

$jobId = [string]$started.job_id
if ([string]::IsNullOrWhiteSpace($jobId)) {
    Write-Host "The API did not return a job_id." -ForegroundColor Red
    exit 1
}

$pollUrl = $baseUrl + "/api/search/" + [Uri]::EscapeDataString($jobId)
while ($true) {
    try {
        $job = Invoke-RestMethod -Method Get -Uri $pollUrl -Headers $headers -ErrorAction Stop
    }
    catch {
        $statusCode = Get-StatusCode -ErrorRecord $_
        if ($statusCode -eq 0) {
            Write-Progress -Activity "Resdex search" -Completed
            Write-Host "Could not reach the API. Make sure uvicorn is running." -ForegroundColor Red
        }
        else {
            Write-Progress -Activity "Resdex search" -Completed
            Write-Host ("Polling failed (HTTP " + $statusCode + ").") -ForegroundColor Red
        }
        exit 1
    }

    $status = [string]$job.status
    $page = 0
    $found = 0
    if ($null -ne $job.progress) {
        $page = [int]$job.progress.page
        $found = [int]$job.progress.found
    }
    $percent = 0
    if ($MaxPages -gt 0) {
        $percent = [Math]::Min(99, [int](100 * $page / $MaxPages))
    }
    Write-Progress -Activity "Resdex search" `
        -Status ("Status: " + $status + " | Page: " + $page + " | Found: " + $found) `
        -PercentComplete $percent

    $candidates = @()
    if ($null -ne $job.results) {
        $candidates = @($job.results)
    }
    $candidateCount = [int]$job.count
    if ($null -eq $job.count) {
        $candidateCount = $candidates.Count
    }

    if ($status -eq "done") {
        Write-Progress -Activity "Resdex search" -Completed
        Show-CandidatePreview -Candidates $candidates
        Write-Host ("Candidates found: " + $candidateCount)
        try {
            Save-Workbook -JobId $jobId
        }
        catch {
            Write-Host $_.Exception.Message -ForegroundColor Red
            exit 1
        }
        exit 0
    }

    if ($status -eq "error") {
        Write-Progress -Activity "Resdex search" -Completed
        if ($candidates.Count -gt 0) {
            Write-Host ("Search failed after finding " + $candidateCount +
                " candidate(s); downloading partial results.")
            Show-CandidatePreview -Candidates $candidates
            try {
                Save-Workbook -JobId $jobId
            }
            catch {
                Write-Host $_.Exception.Message -ForegroundColor Red
            }
        }
        Write-Host ([string]$job.error) -ForegroundColor Red
        exit 1
    }

    if ($status -ne "pending" -and $status -ne "running") {
        Write-Progress -Activity "Resdex search" -Completed
        Write-Host ("Unexpected job status: " + $status) -ForegroundColor Red
        exit 1
    }

    Start-Sleep -Seconds 3
}
