[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path (Get-Location) ("t480s-evidence-" + (Get-Date -Format "yyyyMMdd-HHmmss"))),
    [string]$MacOSVersion = "Sequoia (exact version pending)",
    [string]$MacOSBuild = "pending"
)

# Read-only Windows-side evidence capture for G1. The output deliberately omits
# serial numbers, UUIDs, MAC addresses, PNP instance IDs and other identifiers
# that should remain private to the owner of the machine.
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null

function Get-SafeValue {
    param(
        [object]$InputObject,
        [string[]]$Properties
    )

    $result = [ordered]@{}
    foreach ($property in $Properties) {
        $result[$property] = $InputObject.$property
    }
    return $result
}

function Get-SafePnpDevice {
    param([object[]]$Devices)

    return @($Devices | ForEach-Object {
        [ordered]@{
            status = $_.Status
            class = $_.Class
            friendly_name = $_.FriendlyName
            manufacturer = $_.Manufacturer
            problem = $_.Problem
            present = $_.Present
        }
    })
}

function Get-CommandOutput {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )

    try {
        $output = & $FilePath @Arguments 2>&1
        return [ordered]@{
            available = $true
            exit_code = $LASTEXITCODE
            output = @($output | ForEach-Object { $_.ToString() })
        }
    }
    catch {
        return [ordered]@{
            available = $false
            error = $_.Exception.Message
        }
    }
}

$computer = Get-CimInstance Win32_ComputerSystem
$product = Get-CimInstance Win32_ComputerSystemProduct
$bios = Get-CimInstance Win32_BIOS
$os = Get-CimInstance Win32_OperatingSystem
$processors = @(Get-CimInstance Win32_Processor)
$graphics = @(Get-CimInstance Win32_VideoController)
$disks = @(Get-CimInstance Win32_DiskDrive)
$network = @(Get-CimInstance Win32_NetworkAdapter | Where-Object { $_.PhysicalAdapter -eq $true })
$monitors = @(Get-CimInstance Win32_DesktopMonitor)

try {
    $secureBoot = Confirm-SecureBootUEFI
}
catch {
    $secureBoot = "unavailable: $($_.Exception.Message)"
}

try {
    $tpm = Get-Tpm | Select-Object TpmPresent, TpmReady, ManufacturerIdTxt, ManufacturerVersion
}
catch {
    $tpm = [ordered]@{ available = $false; error = $_.Exception.Message }
}

$pnp = @(Get-PnpDevice -PresentOnly)
$usb = Get-SafePnpDevice @($pnp | Where-Object { $_.Class -eq "USB" })
$acpi = Get-SafePnpDevice @($pnp | Where-Object { $_.InstanceId -like "ACPI\*" })
$hid = Get-SafePnpDevice @($pnp | Where-Object { $_.Class -in @("HIDClass", "Keyboard", "Mouse") })

$evidence = [ordered]@{
    schema_version = "0.1"
    captured_utc = (Get-Date).ToUniversalTime().ToString("o")
    host_platform = "windows"
    target = [ordered]@{
        model = "Lenovo ThinkPad T480s"
        macos_name = $MacOSVersion
        macos_build = $MacOSBuild
    }
    computer = Get-SafeValue $computer @("Manufacturer", "Model", "SystemFamily", "SystemType", "NumberOfLogicalProcessors", "TotalPhysicalMemory")
    product = Get-SafeValue $product @("Vendor", "Name", "Version")
    bios = Get-SafeValue $bios @("Manufacturer", "SMBIOSBIOSVersion", "Version", "ReleaseDate")
    operating_system = Get-SafeValue $os @("Caption", "Version", "BuildNumber", "OSArchitecture")
    processors = @($processors | ForEach-Object { Get-SafeValue $_ @("Name", "Manufacturer", "NumberOfCores", "NumberOfLogicalProcessors", "MaxClockSpeed") })
    graphics = @($graphics | ForEach-Object { Get-SafeValue $_ @("Name", "AdapterCompatibility", "AdapterRAM", "DriverVersion", "VideoModeDescription") })
    storage = @($disks | ForEach-Object { Get-SafeValue $_ @("Index", "Model", "InterfaceType", "MediaType", "Size", "Partitions") })
    network_adapters = @($network | ForEach-Object { Get-SafeValue $_ @("Name", "Manufacturer", "ProductName", "NetConnectionStatus", "PhysicalAdapter", "Speed") })
    monitors = @($monitors | ForEach-Object { Get-SafeValue $_ @("Name", "MonitorManufacturer", "MonitorType", "ScreenHeight", "ScreenWidth") })
    secure_boot = $secureBoot
    tpm = $tpm
    usb_devices = $usb
    acpi_devices = $acpi
    input_devices = $hid
    optional_tools = [ordered]@{
        acpidump = Get-CommandOutput "acpidump.exe" @()
        iasl = Get-CommandOutput "iasl.exe" @("-v")
    }
}

$jsonPath = Join-Path $OutputDirectory "t480s-evidence.json"
$evidence | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $jsonPath -Encoding UTF8

$checklist = @"
# T480s evidence checklist

Capture UTC: $($evidence.captured_utc)
Windows-side evidence: t480s-evidence.json

Fill these items in manually from the T480s BIOS and physical machine. Do not
write serial numbers, UUIDs, MAC addresses, MLB, ROM or other private identity
values into this file.

- [ ] Confirm the chassis model and machine type shown by BIOS.
- [ ] Record BIOS version/date and the settings relevant to boot, virtualization,
      Secure Boot, storage mode, graphics switching and Thunderbolt/USB.
- [ ] Record whether the panel is internal-only, touchscreen, or externally
      docked, and note the panel resolution and refresh rate.
- [ ] Record the installed storage model and whether any second storage device
      is present.
- [ ] Record the WLAN and Bluetooth card models.
- [ ] Record the keyboard, trackpoint, touchpad and fingerprint-reader variants.
- [ ] Walk every USB-A, USB-C and dock port with a known device and record which
      physical port maps to each Windows USB entry. This is required for a USB
      map; the automatic list alone is insufficient.
- [ ] Attach the resulting JSON and this completed checklist to the G1 evidence
      review. Keep any raw vendor reports private.

Target macOS policy:
- Name: $MacOSVersion
- Build: $MacOSBuild
- [ ] Replace "pending" with the exact macOS version/build selected for the
      first qualification run. This is a future install target, not the current
      Windows version.
"@
$checklist | Set-Content -LiteralPath (Join-Path $OutputDirectory "MANUAL_CHECKLIST.md") -Encoding UTF8

Write-Output "Wrote sanitized evidence: $jsonPath"
Write-Output "Wrote manual checklist: $(Join-Path $OutputDirectory 'MANUAL_CHECKLIST.md')"
