# Ensure user is logged into Azure
$accountInfo = az account show --only-show-errors 2>$null
if (-not $accountInfo) {
    Write-Host "You are not logged in to Azure. Opening login prompt..."
    az login
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Azure login failed. Exiting."
        exit 1
    }
    $accountInfo = az account show --only-show-errors
}

$account = $accountInfo | ConvertFrom-Json
Write-Host "You are already logged in as $($account.user.name)."


# Set up local sync folder
Write-Host "`n> Checking local app directory ..."
$LOCAL_DIR = ".."
$LOCAL_FULL_PATH = Resolve-Path -Path $LOCAL_DIR
Write-Host "The app will sync to: $LOCAL_FULL_PATH"

# Create folder if it doesn't exist
if (-not (Test-Path -Path $LOCAL_DIR)) {
    New-Item -ItemType Directory -Path $LOCAL_DIR | Out-Null
}

# Confirm overwrite if directory is not empty
if ((Get-ChildItem -Path $LOCAL_DIR -Recurse -File | Measure-Object).Count -gt 0) {
    $confirm = Read-Host "Warning: directory is not empty. Overwrite existing files? [y/n]"
    if ($confirm.ToLower() -ne "y") {
        Write-Host "Aborted by user."
        exit 0
    }
}

# Set Azure context
$SUBSCRIPTION_ID = "lake-npd-subs"
$RESOURCE_GROUP = "lake-cac-npd-rg-devoedataservices"
$LOGIC_APP_NAME = "lake-cac-npd-logic-devoedataservices01"

Write-Host "`n> Setting subscription to '$SUBSCRIPTION_ID' ..."
az account set --subscription $SUBSCRIPTION_ID
if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to set subscription. Please check the ID."
    exit 1
}

# Attempt to retrieve publishing credentials
Write-Host "`n> Retrieving publishing credentials ..."

$publishProfiles = az webapp deployment list-publishing-profiles `
    --name $LOGIC_APP_NAME `
    --resource-group $RESOURCE_GROUP `
    --query "[?publishMethod=='MSDeploy']" `
    --output json 2>$null | ConvertFrom-Json

# If credentials cannot be retrieved, attempt extended scope login
if (-not $publishProfiles) {
    Write-Host "Publishing credentials require an elevated login scope."
    Write-Host "Attempting login with extended scope ..."

    az login --scope https://management.core.windows.net//.default

    if ($LASTEXITCODE -ne 0) {
        Write-Host "Extended scope login failed. Cannot continue."
        exit 1
    }

    # Retry after extended scope login
    $publishProfiles = az webapp deployment list-publishing-profiles `
        --name $LOGIC_APP_NAME `
        --resource-group $RESOURCE_GROUP `
        --query "[?publishMethod=='MSDeploy']" `
        --output json | ConvertFrom-Json
}

# Final validation
if (-not $publishProfiles) {
    Write-Host "Failed to retrieve publishing credentials even after extended scope login."
    Write-Host "Confirm that the Logic App is Standard and that you have Site Contributor or higher."
    exit 1
}

Write-Host "Publishing credentials retrieved."

# Extract credentials
$PUBLISH_URL = $publishProfiles[0].publishUrl.Split(":")[0]
$USER_NAME = $publishProfiles[0].userName
$PASSWORD = $publishProfiles[0].userPWD


# Build Kudu ZIP URL
$KUDU_ZIP_URL = "https://$PUBLISH_URL/api/zip/site/wwwroot/"
$TEMP_ZIP = Join-Path $env:TEMP "$LOGIC_APP_NAME.zip"

Write-Host "`n> Downloading zip package ..."
Invoke-WebRequest -Uri $KUDU_ZIP_URL -OutFile $TEMP_ZIP -UseBasicParsing -Headers @{
    Authorization = ("Basic " + [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("$USER_NAME`:$PASSWORD")))
}

if (-not (Test-Path -Path $TEMP_ZIP) -or ((Get-Item $TEMP_ZIP).Length -eq 0)) {
    Write-Host "Download failed or ZIP file is empty."
    exit 1
}
Write-Host "ZIP downloaded to $TEMP_ZIP"

# Extract ZIP to temp folder
Write-Host "`n> Extracting files to temp directory ..."
$TEMP_DIR = Join-Path $env:TEMP ([System.IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Path $TEMP_DIR -Force | Out-Null

Expand-Archive -Path $TEMP_ZIP -DestinationPath $TEMP_DIR -Force
Write-Host "The package was extracted at $TEMP_DIR"

# Sync files
Write-Host "`n> Syncing files to local repo ..."
Get-ChildItem -Path $TEMP_DIR -Recurse -File | ForEach-Object {
    $relPath = $_.FullName.Substring($TEMP_DIR.Length).TrimStart('\')
    $target = Join-Path $LOCAL_DIR $relPath

    if (Test-Path $target) {
        $diff = -not (Compare-Object -ReferenceObject (Get-Content $_.FullName -Raw) -DifferenceObject (Get-Content $target -Raw))
        if (-not $diff) {
            $overwrite = Read-Host "Conflict: $relPath exists. Overwrite? [y/n]"
            if ($overwrite.ToLower() -eq "y") {
                Copy-Item $_.FullName -Destination $target -Force
                Write-Host "Overwritten: $relPath"
            } else {
                Write-Host "Skipped: $relPath"
            }
        }
    } else {
        New-Item -ItemType Directory -Path (Split-Path $target) -Force | Out-Null
        Copy-Item $_.FullName -Destination $target
        Write-Host "Added: $relPath"
    }
}

# Cleanup
Write-Host "`n> Cleaning up ..."
Remove-Item -Path $TEMP_DIR -Recurse -Force
Write-Host "Temp files deleted."

Write-Host "`n* Sync complete *"