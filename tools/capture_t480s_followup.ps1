[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path (Get-Location) ("t480s-followup-" + (Get-Date -Format "yyyyMMdd-HHmmss"))),
    [string]$AcpiDumpPath = (Join-Path $PSScriptRoot "bin\acpidump.exe"),
    [switch]$SkipUsbWalk
)

# Read-only follow-up evidence capture for a physical ThinkPad T480s.
# This script never changes firmware, drivers, disks, partitions, or devices.
$ErrorActionPreference = "Stop"
$AcpiDumpSha256 = "a0095a57521378c290d030db7ad196a27de2fcc770dd0347104ff49d19d792e0"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-StringHash {
    param([string]$Value)
    $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Get-SafeHardwareIds {
    param([object]$Device)
    $values = @()
    try {
        $property = Get-PnpDeviceProperty -InstanceId $Device.InstanceId -KeyName "DEVPKEY_Device_HardwareIds" -ErrorAction Stop
        foreach ($value in @($property.Data)) {
            $text = [string]$value
            if ($text -match '^(?<bus>[A-Z]+)\\VEN_(?<ven>[0-9A-F]{4})&DEV_(?<dev>[0-9A-F]{4})(?:&SUBSYS_(?<sub>[0-9A-F]{8}))?(?:&REV_(?<rev>[0-9A-F]{2}))?') {
                $safe = "$($Matches.bus)\VEN_$($Matches.ven)&DEV_$($Matches.dev)"
                if ($Matches.sub) { $safe += "&SUBSYS_$($Matches.sub)" }
                if ($Matches.rev) { $safe += "&REV_$($Matches.rev)" }
                $values += $safe
            }
            elseif ($text -match '^USB\\(?:VID_[0-9A-F]{4}&PID_[0-9A-F]{4})(?:&REV_[0-9A-F]{4})?') {
                $values += $Matches[0]
            }
        }
    }
    catch { }
    return @($values | Sort-Object -Unique)
}

function Get-SafeLocationPaths {
    param([object]$Device)
    try {
        $property = Get-PnpDeviceProperty -InstanceId $Device.InstanceId -KeyName "DEVPKEY_Device_LocationPaths" -ErrorAction Stop
        return @($property.Data | ForEach-Object { [string]$_ } | Where-Object { $_ -match '^(PCIROOT|ACPI|USBROOT|USB)\(' })
    }
    catch {
        return @()
    }
}

function Get-SafeDevice {
    param([object]$Device)
    return [ordered]@{
        identity_hash = Get-StringHash ([string]$Device.InstanceId)
        class = [string]$Device.Class
        friendly_name = [string]$Device.FriendlyName
        manufacturer = [string]$Device.Manufacturer
        status = [string]$Device.Status
        hardware_ids = @(Get-SafeHardwareIds $Device)
        location_paths = @(Get-SafeLocationPaths $Device)
    }
}

function Get-PresentUsbState {
    $state = [ordered]@{}
    foreach ($device in @(Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like "USB\*" })) {
        $hash = Get-StringHash ([string]$device.InstanceId)
        $state[$hash] = Get-SafeDevice $device
    }
    return $state
}

function Invoke-UsbObservation {
    param(
        [string]$PhysicalLabel,
        [string]$ConnectorType,
        [string]$Orientation
    )

    Write-Host ""
    Write-Host "Unplug the TEST device from $PhysicalLabel. Leave the collector folder where it is."
    [void](Read-Host "Press Enter when the test device is unplugged")
    $before = Get-PresentUsbState

    Write-Host "Insert the test data device into $PhysicalLabel ($Orientation)."
    Write-Host "Wait for Windows to recognise it and confirm it appears in File Explorer."
    $detectedAnswer = Read-Host "Type YES if detected, otherwise type NO, then press Enter"
    $after = Get-PresentUsbState
    $newHashes = @($after.Keys | Where-Object { -not $before.Contains($_) })

    return [ordered]@{
        physical_label = $PhysicalLabel
        connector_type = $ConnectorType
        orientation = $Orientation
        user_confirmed_detected = ($detectedAnswer.Trim().ToUpperInvariant() -eq "YES")
        newly_present_devices = @($newHashes | ForEach-Object { $after[$_] })
        observed_utc = (Get-Date).ToUniversalTime().ToString("o")
    }
}

if (-not (Test-Administrator)) {
    throw "Run Windows PowerShell as Administrator, then run this script again."
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$OutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).Path

$evidence = [ordered]@{
    schema_version = "0.2"
    capture_type = "t480s-followup"
    captured_utc = (Get-Date).ToUniversalTime().ToString("o")
    host_platform = "windows"
    collector_version = "1"
    audio_devices = @()
    smart_card_readers = @()
    wwan_devices = @()
    internal_usb_devices = @()
    usb_observations = @()
    acpi = [ordered]@{ status = "not_attempted"; tool_sha256 = $null; command_exit_codes = @(); tables = @(); warning = $null; error = $null }
}

$allPresent = @(Get-PnpDevice -PresentOnly)
$evidence.audio_devices = @($allPresent | Where-Object { $_.Class -in @("MEDIA", "AudioEndpoint") } | ForEach-Object { Get-SafeDevice $_ })
$evidence.smart_card_readers = @($allPresent | Where-Object { $_.Class -match "SmartCard" -or $_.FriendlyName -match "Smart.?Card" } | ForEach-Object { Get-SafeDevice $_ })
$evidence.wwan_devices = @($allPresent | Where-Object { $_.FriendlyName -match "Fibocom|Mobile Broadband|WWAN" } | ForEach-Object { Get-SafeDevice $_ })
$evidence.internal_usb_devices = @($allPresent | Where-Object {
    $_.InstanceId -like "USB\*" -and $_.FriendlyName -match "Bluetooth|Camera|Webcam|Card Reader|Mobile Broadband|Fibocom"
} | ForEach-Object { Get-SafeDevice $_ })

$privateAcpi = Join-Path $OutputDirectory "PRIVATE-ACPI"
try {
    if (-not (Test-Path -LiteralPath $AcpiDumpPath -PathType Leaf)) {
        throw "Verified acpidump.exe is missing from the collector bin folder."
    }
    $actualHash = (Get-FileHash -LiteralPath $AcpiDumpPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $AcpiDumpSha256) {
        throw "acpidump.exe SHA-256 does not match the approved ACPICA 20260408 binary."
    }
    New-Item -ItemType Directory -Force -Path $privateAcpi | Out-Null
    Push-Location $privateAcpi
    try {
        & $AcpiDumpPath -b -n DSDT | Out-Null
        $dsdtExit = $LASTEXITCODE
        & $AcpiDumpPath -b -n SSDT | Out-Null
        $ssdtExit = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    $tables = @(Get-ChildItem -LiteralPath $privateAcpi -File | Where-Object { $_.Extension -in @(".dat", ".bin", ".aml") })
    if ($tables.Count -eq 0) { throw "ACPICA reported success but produced no DSDT/SSDT table files." }
    $evidence.acpi.command_exit_codes = @($dsdtExit, $ssdtExit)
    $evidence.acpi.status = if ($dsdtExit -eq 0 -and $ssdtExit -eq 0) { "captured_private" } else { "captured_private_with_tool_warning" }
    if ($evidence.acpi.status -eq "captured_private_with_tool_warning") {
        $evidence.acpi.warning = "ACPICA returned a nonzero exit after producing tables; validate every ACPI header length and checksum before accepting the capture."
    }
    $evidence.acpi.tool_sha256 = $actualHash
    $evidence.acpi.tables = @($tables | ForEach-Object {
        [ordered]@{ file = $_.Name; size = $_.Length; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
    })
}
catch {
    $evidence.acpi.status = "failed"
    $evidence.acpi.error = $_.Exception.Message
}

if (-not $SkipUsbWalk) {
    Write-Host ""
    Write-Host "USB WALK: use one ordinary USB data stick or data-capable USB-C adapter."
    Write-Host "Do not use a charger as the test device. Do not format any device."
    $observations = @()
    $observations += Invoke-UsbObservation "LEFT USB-A (beside HDMI)" "USB-A" "not-applicable"
    $observations += Invoke-UsbObservation "RIGHT USB-A (beside cooling vent)" "USB-A" "not-applicable"
    $observations += Invoke-UsbObservation "LEFT USB-C charging/data (rearmost standalone USB-C)" "USB-C" "side-A"
    $observations += Invoke-UsbObservation "LEFT USB-C charging/data (rearmost standalone USB-C)" "USB-C" "side-B"
    $observations += Invoke-UsbObservation "LEFT Thunderbolt USB-C (within docking connector)" "USB-C/Thunderbolt" "side-A"
    $observations += Invoke-UsbObservation "LEFT Thunderbolt USB-C (within docking connector)" "USB-C/Thunderbolt" "side-B"
    $evidence.usb_observations = $observations
}

$jsonPath = Join-Path $OutputDirectory "t480s-followup.json"
$evidence | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $jsonPath -Encoding UTF8

$readme = @"
# T480s follow-up evidence

- Main report: t480s-followup.json
- Raw DSDT/SSDT firmware tables: PRIVATE-ACPI/

The JSON report omits raw PNP instance IDs and replaces them with SHA-256 hashes.
PRIVATE-ACPI contains executable firmware description tables and is deliberately
separate. Keep this folder within the evidence package supplied privately to the
MacLoader project; do not post it publicly.

No disks, partitions, firmware settings, drivers, or device settings were changed.
ACPI capture status: $($evidence.acpi.status)
"@
$readme | Set-Content -LiteralPath (Join-Path $OutputDirectory "README.txt") -Encoding UTF8

Write-Host ""
Write-Host "Capture complete: $OutputDirectory"
Write-Host "Copy the entire t480s-followup folder back to the project USB."
Write-Host "Do not publish the PRIVATE-ACPI folder on the internet."
