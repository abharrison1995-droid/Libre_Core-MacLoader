[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path (Get-Location) ("t480s-usb-audio-" + (Get-Date -Format "yyyyMMdd-HHmmss")))
)

# Read-only targeted follow-up. No firmware, driver, disk, partition, or device
# setting is changed. Raw PNP instance identifiers are never written to output.
$ErrorActionPreference = "Stop"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-StringHash {
    param([string]$Value)
    $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Get-PropertyData {
    param([object]$Device, [string]$KeyName)
    try {
        return @((Get-PnpDeviceProperty -InstanceId $Device.InstanceId -KeyName $KeyName -ErrorAction Stop).Data)
    }
    catch { return @() }
}

function Get-SafeHardwareIds {
    param([object]$Device)
    $safe = @()
    foreach ($rawValue in @(Get-PropertyData $Device "DEVPKEY_Device_HardwareIds")) {
        $value = [string]$rawValue
        if ($value -match '^(HDAUDIO\\FUNC_[0-9A-F]{2}&VEN_[0-9A-F]{4}&DEV_[0-9A-F]{4}(?:&SUBSYS_[0-9A-F]{8})?(?:&REV_[0-9A-F]{4})?)') {
            $safe += $Matches[1]
        }
        elseif ($value -match '^(PCI\\VEN_[0-9A-F]{4}&DEV_[0-9A-F]{4}(?:&SUBSYS_[0-9A-F]{8})?(?:&REV_[0-9A-F]{2})?)') {
            $safe += $Matches[1]
        }
        elseif ($value -match '^(USB\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4}(?:&REV_[0-9A-F]{4})?)') {
            $safe += $Matches[1]
        }
        elseif ($value -match '^(USBSTOR\\DISK&VEN_[^&\\]{1,32}&PROD_[^&\\]{1,48}(?:&REV_[^&\\]{1,16})?)') {
            $safe += $Matches[1]
        }
    }
    return @($safe | Sort-Object -Unique)
}

function Get-SafeLocationPaths {
    param([object]$Device)
    return @(Get-PropertyData $Device "DEVPKEY_Device_LocationPaths" |
        ForEach-Object { [string]$_ } |
        Where-Object { $_ -match '^(PCIROOT|ACPI|USBROOT|USB)\(' } |
        Sort-Object -Unique)
}

function Get-SafeUsbDevice {
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

function Get-UsbState {
    $state = [ordered]@{}
    foreach ($device in @(Get-PnpDevice -PresentOnly | Where-Object {
        $_.InstanceId -like "USB\*" -or $_.InstanceId -like "USBSTOR\*"
    })) {
        $hash = Get-StringHash ([string]$device.InstanceId)
        $state[$hash] = Get-SafeUsbDevice $device
    }
    return $state
}

function Get-StateDifference {
    param([object]$Before, [object]$After)
    $result = @()
    foreach ($key in @($After.Keys)) {
        if (-not $Before.Contains($key)) {
            $result += $After[$key]
            continue
        }
        $beforeLocations = @($Before[$key].location_paths) -join "|"
        $afterLocations = @($After[$key].location_paths) -join "|"
        if ($beforeLocations -ne $afterLocations) { $result += $After[$key] }
    }
    return @($result)
}

function Get-LikelyExternalDevices {
    param([object]$State)
    return @($State.Values | Where-Object {
        $_.class -in @("DiskDrive", "USB", "WPD", "SCSIAdapter") -and
        $_.friendly_name -notmatch "Root Hub|Host Controller|Bluetooth|Camera|Card Reader|Fibocom"
    })
}

function Invoke-PortTest {
    param([string]$PhysicalLabel, [string]$ConnectorType, [string]$Orientation)

    Write-Host ""
    Write-Host "TEST: $PhysicalLabel ($Orientation)" -ForegroundColor Cyan
    Write-Host "Unplug the separate TEST device. Do not unplug the computer's charger."
    [void](Read-Host "Press Enter after the test device is unplugged")
    Write-Host "Waiting for Windows to register removal..."
    Start-Sleep -Seconds 4
    $before = Get-UsbState

    Write-Host "Insert the TEST DATA device into $PhysicalLabel ($Orientation)."
    [void](Read-Host "Wait until Windows detects it, then press Enter")
    Start-Sleep -Seconds 4
    $after = Get-UsbState
    $changed = @(Get-StateDifference $before $after)
    $detected = (Read-Host "Type YES if the device is visible/working; otherwise type NO").Trim().ToUpperInvariant() -eq "YES"

    return [ordered]@{
        physical_label = $PhysicalLabel
        connector_type = $ConnectorType
        orientation = $Orientation
        user_confirmed_detected = $detected
        changed_devices = $changed
        external_candidates_after_insertion = @(Get-LikelyExternalDevices $after)
        observed_utc = (Get-Date).ToUniversalTime().ToString("o")
    }
}

if (-not (Test-Administrator)) {
    throw "Run Windows PowerShell as Administrator, then run this script again."
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$OutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).Path

$present = @(Get-PnpDevice -PresentOnly)
$audio = @($present | Where-Object {
    $_.Class -in @("MEDIA", "AudioEndpoint") -or $_.InstanceId -like "HDAUDIO\*"
} | ForEach-Object {
    [ordered]@{
        class = [string]$_.Class
        friendly_name = [string]$_.FriendlyName
        manufacturer = [string]$_.Manufacturer
        hardware_ids = @(Get-SafeHardwareIds $_)
    }
})

Write-Host "T480s final USB/audio capture" -ForegroundColor Green
Write-Host "Use one separate USB data device. Never format or initialise it."

$observations = @()
$observations += Invoke-PortTest "LEFT USB-A (beside HDMI)" "USB-A" "not-applicable"
$observations += Invoke-PortTest "RIGHT USB-A (beside cooling vent)" "USB-A" "not-applicable"
$observations += Invoke-PortTest "LEFT USB-C charging/data (standalone rearmost socket)" "USB-C" "side-A"
$observations += Invoke-PortTest "LEFT USB-C charging/data (standalone rearmost socket)" "USB-C" "side-B"
$observations += Invoke-PortTest "LEFT Thunderbolt USB-C (inside docking connector)" "USB-C/Thunderbolt" "side-A"
$observations += Invoke-PortTest "LEFT Thunderbolt USB-C (inside docking connector)" "USB-C/Thunderbolt" "side-B"

$report = [ordered]@{
    schema_version = "0.3"
    capture_type = "t480s-usb-audio-final"
    collector_version = "2"
    captured_utc = (Get-Date).ToUniversalTime().ToString("o")
    audio_devices = $audio
    usb_observations = $observations
    privacy = [ordered]@{
        raw_instance_ids_written = $false
        identities_replaced_with_sha256 = $true
    }
}

$jsonPath = Join-Path $OutputDirectory "t480s-usb-audio-final.json"
$report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $jsonPath -Encoding UTF8

Write-Host ""
Write-Host "Finished: $OutputDirectory" -ForegroundColor Green
Write-Host "Copy that complete folder back to the transport USB."
