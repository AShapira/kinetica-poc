[CmdletBinding()]
param(
    [ValidateSet("Doctor", "BuildImage", "InstallKinetica", "Run", "All")]
    [string] $Action = "All",
    [string] $ConfigPath = (Join-Path $PSScriptRoot "windows-docker.local.json")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-Native {
    param(
        [Parameter(Mandatory)] [string] $FilePath,
        [Parameter(Mandatory)] [string[]] $Arguments,
        [switch] $Capture
    )
    if ($Capture) {
        $output = & $FilePath @Arguments 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed ($LASTEXITCODE): $FilePath $($Arguments -join ' ')`n$($output -join [Environment]::NewLine)"
        }
        return ($output -join [Environment]::NewLine).Trim()
    }
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $FilePath $($Arguments -join ' ')"
    }
}

function Resolve-ConfiguredPath {
    param([string] $Value)
    if ([IO.Path]::IsPathRooted($Value)) {
        return [IO.Path]::GetFullPath($Value)
    }
    return [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $Value))
}

function Get-ContainerState {
    $existing = & $Docker ps -a --filter "name=^/$KineticaContainer$" --format "{{.Names}}"
    if ($LASTEXITCODE -ne 0) { throw "Unable to inspect Docker containers" }
    if (($existing | Where-Object { $_ -eq $KineticaContainer }).Count -eq 0) {
        return "missing"
    }
    return (Invoke-Native -FilePath $Docker -Arguments @(
        "inspect", "--format", "{{if .State.Running}}running{{else}}stopped{{end}}",
        $KineticaContainer
    ) -Capture)
}

function Get-RepositoryState {
    $commit = "unknown"
    $dirty = "true"
    try {
        $commit = Invoke-Native -FilePath "git" -Arguments @(
            "-C", $RepositoryRoot, "rev-parse", "HEAD"
        ) -Capture
        $status = Invoke-Native -FilePath "git" -Arguments @(
            "-C", $RepositoryRoot, "status", "--porcelain", "--", ".",
            ":(exclude)evidence/**"
        ) -Capture
        $dirty = if ([string]::IsNullOrWhiteSpace($status)) { "false" } else { "true" }
    } catch {
        Write-Warning "Git provenance could not be read; the run will be marked dirty."
    }
    return @{ Commit = $commit; Dirty = $dirty }
}

function Get-ClientArguments {
    param([string] $RunOutput)
    $repository = Get-RepositoryState
    $imageId = Invoke-Native -FilePath $Docker -Arguments @(
        "image", "inspect", "--format", "{{.Id}}", $BenchmarkImage
    ) -Capture
    $bundleHash = (Get-FileHash -Algorithm SHA256 $BundleManifest).Hash.ToLowerInvariant()
    $pullPolicy = if ($Offline) { "never" } else { "missing" }
    return @(
        "run", "--rm", "--pull", $pullPolicy,
        "--cpus", [string]$ContainerCpus,
        "--memory", $ContainerMemory,
        "--memory-swap", $ContainerMemory,
        "--mount", "type=bind,source=$BundleDirectory,target=/presentation-input,readonly",
        "--mount", "type=bind,source=$RunOutput,target=/presentation-output",
        "--env", "BENCHMARK_CONFIG=/workspace/config/presentation-windows.yaml",
        "--env", "PRESENTATION_BUNDLE_DIR=/presentation-input",
        "--env", "BENCHMARK_DATA_DIR=/presentation-output",
        "--env", "BENCHMARK_PROFILE=windows-docker-presentation",
        "--env", "BENCHMARK_OFFLINE=$($Offline.ToString().ToLowerInvariant())",
        "--env", "BENCHMARK_CONTAINER_CPUS=$ContainerCpus",
        "--env", "BENCHMARK_CONTAINER_MEMORY=$ContainerMemory",
        "--env", "SEDONADB_MEMORY_LIMIT=$SedonaMemory",
        "--env", "BENCHMARK_BUNDLE_SHA256=$bundleHash",
        "--env", "BENCHMARK_GIT_COMMIT=$($repository.Commit)",
        "--env", "BENCHMARK_GIT_DIRTY=$($repository.Dirty)",
        "--env", "BENCHMARK_IMAGE_ID=$imageId",
        "--env", "BENCHMARK_KINETICA_IMAGE=$KineticaImage",
        "--env", "BENCHMARK_KINETICA_IMAGE_DIGEST=$KineticaImageDigest",
        "--env", "BENCHMARK_KINETICA_GPU_MODE=docker-desktop-wsl2-gpu",
        "--env", "BENCHMARK_KINETICA_GPU_NAME=$script:GpuName",
        "--env", "MPLCONFIGDIR=/tmp/matplotlib",
        "--env", "KINETICA_HOST=host.docker.internal",
        "--env", "KINETICA_REST_PORT=$DatabasePort",
        "--env", "KINETICA_POSTGRES_PORT=$PostgresPort",
        "--env", "KINETICA_USER=$KineticaUser"
    )
}

function Invoke-Client {
    param(
        [string] $RunOutput,
        [string[]] $Command
    )
    $arguments = @(Get-ClientArguments -RunOutput $RunOutput) + @(
        $BenchmarkImage, "python", "-m", "sedona_benchmark"
    ) + $Command
    Invoke-Native -FilePath $Docker -Arguments $arguments
}

function Invoke-ClientWithSecret {
    param(
        [string] $RunOutput,
        [string[]] $Command,
        [System.Security.SecureString] $Password
    )
    $arguments = @(Get-ClientArguments -RunOutput $RunOutput)
    $arguments += @(
        "-i", "--tmpfs", "/run/secrets:rw,noexec,nosuid,size=65536",
        "--env", "KINETICA_PASSWORD_FILE=/run/secrets/kinetica_password",
        $BenchmarkImage,
        "/bin/sh", "-c",
        "umask 077; cat > /run/secrets/kinetica_password; exec python -m sedona_benchmark `"`$@`"",
        "presentation-client"
    ) + $Command
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password)
    $plain = $null
    try {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        $plain | & $Docker @arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Secret-bearing benchmark command failed with exit code $LASTEXITCODE"
        }
    } finally {
        if ($null -ne $plain) { $plain = $null }
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Invoke-KineticaInstaller {
    param([string] $Command)
    $resourceArgs = "--cpus $ContainerCpus --memory $ContainerMemory --memory-swap $ContainerMemory --gpus all"
    Invoke-Native -FilePath $InstallerPath -Arguments @(
        $Command,
        "--image", $KineticaImage,
        "--container", $KineticaContainer,
        "--persist", $KineticaPersistence,
        "--listen-address", "127.0.0.1",
        "--database-port", [string]$DatabasePort,
        "--postgres-port", [string]$PostgresPort,
        "--workbench-port", [string]$WorkbenchPort,
        "--gadmin-port", [string]$GadminPort,
        "--reveal-port", [string]$RevealPort,
        "--docker-run-args", $resourceArgs
    )
}

function Test-Configuration {
    if ($Config.schemaVersion -ne 1) { throw "Unsupported configuration schema" }
    if ($ContainerCpus -lt 1) { throw "docker.cpus must be at least 1" }
    if ($ContainerMemory -notmatch "^[1-9][0-9]*(g|gb)$") {
        throw "docker.memory must be a positive Docker memory value such as 16g"
    }
    if ($SedonaMemory -notmatch "^[1-9][0-9]*(g|gb)$") {
        throw "docker.sedonaMemory must be a positive value such as 12gb"
    }
    $containerGb = [double]($ContainerMemory -replace "(gb|g)$", "")
    $sedonaGb = [double]($SedonaMemory -replace "(gb|g)$", "")
    if ($sedonaGb -ge $containerGb) {
        throw "docker.sedonaMemory must be lower than docker.memory"
    }
    if (-not (Test-Path -LiteralPath $BundleManifest -PathType Leaf)) {
        throw "Presentation bundle is missing or incomplete: $BundleManifest"
    }
    if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) {
        throw "Kinetica installer is missing: $InstallerPath"
    }
    $installerHash = (Get-FileHash -Algorithm SHA256 $InstallerPath).Hash.ToLowerInvariant()
    if ($installerHash -ne $InstallerSha256) {
        throw "Kinetica installer checksum mismatch: expected $InstallerSha256, found $installerHash"
    }
    $manifest = Get-Content -Raw -LiteralPath $BundleManifest | ConvertFrom-Json
    if (-not $manifest.complete -or $manifest.profile -ne "windows-docker-presentation") {
        throw "Presentation bundle manifest is not complete or has the wrong profile"
    }
}

function Test-LocalImage {
    param([string] $Image)
    & $Docker image inspect $Image *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Required local Docker image is missing: $Image"
    }
}

function Invoke-Doctor {
    Test-Configuration
    $info = Invoke-Native -FilePath $Docker -Arguments @(
        "info", "--format", "{{.OSType}}|{{.NCPU}}|{{.MemTotal}}"
    ) -Capture
    $parts = $info.Split("|")
    if ($parts.Count -ne 3 -or $parts[0] -ne "linux") {
        throw "Docker Desktop must be using Linux containers"
    }
    if ([int]$parts[1] -lt $ContainerCpus) {
        throw "Docker exposes $($parts[1]) CPUs but the profile requests $ContainerCpus"
    }
    $requiredBytes = [int64](([double]($ContainerMemory -replace "(gb|g)$", "")) * 1GB)
    if ([int64]$parts[2] -lt $requiredBytes) {
        throw "Docker exposes fewer bytes of memory than the requested $ContainerMemory"
    }
    if ($Offline) {
        foreach ($image in @($BenchmarkImage, $GpuSmokeImage, $KineticaImage)) {
            Test-LocalImage -Image $image
        }
    }
    $pullPolicy = if ($Offline) { "never" } else { "missing" }
    $script:GpuName = Invoke-Native -FilePath $Docker -Arguments @(
        "run", "--rm", "--pull", $pullPolicy, "--gpus", "all", $GpuSmokeImage,
        "nvidia-smi", "--query-gpu=name", "--format=csv,noheader"
    ) -Capture
    if ((Get-ContainerState) -eq "missing") {
        foreach ($port in @(
            $DatabasePort, $PostgresPort, $WorkbenchPort, $GadminPort,
            $RevealPort, 9300
        )) {
            $listener = Get-NetTCPConnection -State Listen -LocalPort $port `
                -ErrorAction SilentlyContinue
            if ($null -ne $listener) {
                throw "Host port $port is already in use"
            }
        }
    }
    Write-Host "Doctor passed: Docker Linux engine, resources, GPU ($script:GpuName), bundle, and installer are valid."
}

function Invoke-BuildImage {
    if ($Offline) {
        throw "Offline mode requires the prebuilt benchmark image; BuildImage is disabled"
    }
    Invoke-Native -FilePath $Docker -Arguments @(
        "build", "--file", (Join-Path $RepositoryRoot "containers\Containerfile.sedonadb"),
        "--tag", $BenchmarkImage, $RepositoryRoot
    )
}

function Test-KineticaImageDigest {
    $digestsJson = Invoke-Native -FilePath $Docker -Arguments @(
        "image", "inspect", "--format", "{{json .RepoDigests}}", $KineticaImage
    ) -Capture
    $digests = $digestsJson | ConvertFrom-Json
    if (($digests | Where-Object { $_ -like "*@$KineticaImageDigest" }).Count -eq 0) {
        throw "Kinetica image digest mismatch; expected $KineticaImageDigest"
    }
}

function Test-KineticaContainerContract {
    $details = Invoke-Native -FilePath $Docker -Arguments @(
        "inspect", "--format",
        "{{.Config.Image}}|{{.HostConfig.NanoCpus}}|{{.HostConfig.Memory}}|{{json .HostConfig.DeviceRequests}}",
        $KineticaContainer
    ) -Capture
    $parts = $details -split '\|', 4
    $expectedNanos = [int64]$ContainerCpus * 1000000000
    $expectedMemory = [int64](([double]($ContainerMemory -replace "(gb|g)$", "")) * 1GB)
    if ($parts.Count -ne 4 -or $parts[0] -ne $KineticaImage) {
        throw "Existing Kinetica container uses an unexpected image"
    }
    if ([int64]$parts[1] -ne $expectedNanos -or [int64]$parts[2] -ne $expectedMemory) {
        throw "Existing Kinetica container does not match the configured CPU/memory limits"
    }
    if ($parts[3] -notmatch '"gpu"') {
        throw "Existing Kinetica container does not request a GPU"
    }
}

function Invoke-InstallKinetica {
    if ((Get-ContainerState) -ne "missing") {
        throw "Container $KineticaContainer already exists; installation will not replace it"
    }
    New-Item -ItemType Directory -Force -Path $KineticaPersistence | Out-Null
    if ($Offline) {
        Test-LocalImage -Image $KineticaImage
    } else {
        Invoke-Native -FilePath $Docker -Arguments @("pull", $KineticaImage)
    }
    Test-KineticaImageDigest
    Invoke-KineticaInstaller -Command "start"
    if ((Get-ContainerState) -ne "running") {
        throw "Kinetica installation did not leave $KineticaContainer running"
    }
    Test-KineticaContainerContract
    Invoke-Native -FilePath $Docker -Arguments @(
        "exec", $KineticaContainer, "nvidia-smi", "--query-gpu=name", "--format=csv,noheader"
    )
}

function Invoke-PresentationRun {
    if (-not (Invoke-Native -FilePath $Docker -Arguments @(
        "image", "inspect", "--format", "{{.Id}}", $BenchmarkImage
    ) -Capture)) { throw "Benchmark image is missing" }
    Test-KineticaImageDigest
    $initialState = Get-ContainerState
    if ($initialState -eq "missing") {
        throw "Install the configured Kinetica presentation container first"
    }
    Test-KineticaContainerContract
    $stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
    $runOutput = Join-Path $OutputRoot $stamp
    if (Test-Path -LiteralPath $runOutput) { throw "Run output already exists: $runOutput" }
    New-Item -ItemType Directory -Force -Path $runOutput | Out-Null
    $password = Read-Host "Kinetica admin password (kept in container tmpfs only)" -AsSecureString
    try {
        if ((Get-ContainerState) -eq "running") {
            Invoke-KineticaInstaller -Command "stop"
        }
        Invoke-Client -RunOutput $runOutput -Command @("prepare", "all")
        Invoke-Client -RunOutput $runOutput -Command @(
            "run-sedona", "--tier", "general_driving"
        )
        Invoke-KineticaInstaller -Command "start"
        Invoke-Native -FilePath $Docker -Arguments @(
            "exec", $KineticaContainer, "nvidia-smi", "--query-gpu=name", "--format=csv,noheader"
        )
        Invoke-ClientWithSecret -RunOutput $runOutput -Password $password -Command @(
            "load-kinetica"
        )
        Invoke-ClientWithSecret -RunOutput $runOutput -Password $password -Command @(
            "run-kinetica", "--tier", "general_driving"
        )
        Invoke-Client -RunOutput $runOutput -Command @("compare-presentation")
        Write-Host "Presentation complete: $runOutput"
    } finally {
        if ($initialState -eq "stopped" -and (Get-ContainerState) -eq "running") {
            Invoke-KineticaInstaller -Command "stop"
        }
    }
}

if ($env:OS -ne "Windows_NT") { throw "Run this script in native Windows PowerShell" }
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "Copy windows-docker.example.json to windows-docker.local.json and edit its paths"
}
$Config = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
$script:GpuName = "unverified"
$RepositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$Docker = [string]$Config.docker.executable
$BenchmarkImage = [string]$Config.docker.benchmarkImage
$GpuSmokeImage = [string]$Config.docker.gpuSmokeImage
$Offline = [bool]$Config.docker.offline
$ContainerCpus = [int]$Config.docker.cpus
$ContainerMemory = [string]$Config.docker.memory
$SedonaMemory = [string]$Config.docker.sedonaMemory
$BundleDirectory = Resolve-ConfiguredPath ([string]$Config.paths.bundle)
$BundleManifest = Join-Path $BundleDirectory "bundle-manifest.json"
$OutputRoot = Resolve-ConfiguredPath ([string]$Config.paths.output)
$KineticaPersistence = Resolve-ConfiguredPath ([string]$Config.paths.kineticaPersistence)
$InstallerPath = Resolve-ConfiguredPath ([string]$Config.paths.kineticaInstaller)
$KineticaContainer = [string]$Config.kinetica.container
$KineticaImage = [string]$Config.kinetica.image
$KineticaImageDigest = [string]$Config.kinetica.imageDigest
$InstallerSha256 = [string]$Config.kinetica.installerSha256
$KineticaUser = [string]$Config.kinetica.user
$DatabasePort = [int]$Config.kinetica.ports.database
$PostgresPort = [int]$Config.kinetica.ports.postgres
$WorkbenchPort = [int]$Config.kinetica.ports.workbench
$GadminPort = [int]$Config.kinetica.ports.gadmin
$RevealPort = [int]$Config.kinetica.ports.reveal

switch ($Action) {
    "Doctor" { Invoke-Doctor }
    "BuildImage" { Test-Configuration; Invoke-BuildImage }
    "InstallKinetica" { Invoke-Doctor; Invoke-InstallKinetica }
    "Run" { Invoke-Doctor; Invoke-PresentationRun }
    "All" {
        Invoke-Doctor
        if (-not $Offline) { Invoke-BuildImage }
        if ((Get-ContainerState) -eq "missing") { Invoke-InstallKinetica }
        Invoke-PresentationRun
    }
}
