#Requires -Version 7.4

<#
.SYNOPSIS
Measures Elysia Desktop startup, memory use, shutdown, and orphan cleanup.

.DESCRIPTION
Launches an unpacked Electron build repeatedly with an isolated profile. Process
identity is tracked by PID plus creation time so benchmark cleanup cannot target
an unrelated process after Windows reuses a PID.
#>

param(
    [Parameter(Mandatory = $true)]
    [string] $Executable,

    [ValidateRange(2, 50)]
    [int] $Runs = 10,

    [ValidateRange(0, 30)]
    [int] $IdleSeconds = 3,

    [string] $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

/// <summary>Provides the minimal User32 calls needed by the shell benchmark.</summary>
public static class ElysiaBenchmarkNative
{
    /// <summary>Reports whether a native window is visible.</summary>
    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool IsWindowVisible(IntPtr window);

    /// <summary>Reports whether a native window is minimized.</summary>
    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool IsIconic(IntPtr window);

    /// <summary>Posts a non-blocking native message to a window.</summary>
    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool PostMessage(
        IntPtr window,
        uint message,
        IntPtr wParam,
        IntPtr lParam
    );
}
'@

$wmClose = [uint32] 0x0010

<#
.SYNOPSIS
Captures a consistent CIM snapshot of every current Windows process.
#>
function Get-ProcessSnapshot {
    return @(Get-CimInstance Win32_Process)
}

<#
.SYNOPSIS
Checks that a candidate child was created no earlier than its exact parent.

.DESCRIPTION
Creation ordering prevents PID reuse from attaching an older, unrelated process
to the benchmark's process tree.
#>
function Test-ChildCreatedAfterParent {
    param(
        [object] $Child,
        [object] $Parent
    )

    try {
        $childCreation = ([datetime] $Child.CreationDate).ToUniversalTime()
        $parentCreation = ([datetime] $Parent.CreationDate).ToUniversalTime()
        return ($childCreation -ge $parentCreation)
    } catch {
        return $false
    }
}

<#
.SYNOPSIS
Builds the process tree rooted at one PID-and-creation-time identity.

.DESCRIPTION
The tree expands to a fixed point because the flat CIM snapshot is not ordered
by ancestry and a grandchild may appear before its parent.
#>
function Get-ProcessTree {
    param(
        [int] $RootProcessId,
        [string] $RootCreationDate,
        [object[]] $Snapshot
    )

    $root = @(
        $Snapshot | Where-Object {
            [int] $_.ProcessId -eq $RootProcessId -and
            [string] $_.CreationDate -eq $RootCreationDate
        } | Select-Object -First 1
    )
    if ($root.Count -ne 1) {
        return @()
    }

    $nodes = [System.Collections.Generic.Dictionary[int, object]]::new()
    $nodes.Add($RootProcessId, $root[0])

    do {
        $added = $false
        foreach ($process in $Snapshot) {
            $processId = [int] $process.ProcessId
            $parentId = [int] $process.ParentProcessId
            if (
                -not $nodes.ContainsKey($processId) -and
                $nodes.ContainsKey($parentId) -and
                (Test-ChildCreatedAfterParent `
                    -Child $process `
                    -Parent $nodes[$parentId])
            ) {
                $nodes.Add($processId, $process)
                $added = $true
            }
        }
    } while ($added)

    return @($nodes.Values)
}

<#
.SYNOPSIS
Adds newly discovered descendants to the identities tracked across snapshots.

.DESCRIPTION
Tracking survives parent exit between samples, while creation-time comparisons
ensure that later PID reuse does not turn another program into a cleanup target.
#>
function Expand-TrackedProcesses {
    param(
        [System.Collections.Generic.Dictionary[int, string]] $Tracked,
        [object[]] $Snapshot
    )

    $liveTracked = [System.Collections.Generic.Dictionary[int, object]]::new()
    foreach ($process in $Snapshot) {
        $processId = [int] $process.ProcessId
        if (
            $Tracked.ContainsKey($processId) -and
            $Tracked[$processId] -eq [string] $process.CreationDate
        ) {
            $liveTracked.Add($processId, $process)
        }
    }

    do {
        $added = $false
        foreach ($process in $Snapshot) {
            $processId = [int] $process.ProcessId
            $parentId = [int] $process.ParentProcessId
            if (
                -not $Tracked.ContainsKey($processId) -and
                $liveTracked.ContainsKey($parentId) -and
                (Test-ChildCreatedAfterParent `
                    -Child $process `
                    -Parent $liveTracked[$parentId])
            ) {
                $Tracked.Add($processId, [string] $process.CreationDate)
                $liveTracked.Add($processId, $process)
                $added = $true
            }
        }
    } while ($added)
}

<#
.SYNOPSIS
Returns processes whose PID and creation time still match tracked identities.
#>
function Get-LiveTrackedProcesses {
    param(
        [System.Collections.Generic.Dictionary[int, string]] $Tracked,
        [object[]] $Snapshot
    )

    return @(
        $Snapshot | Where-Object {
            $processId = [int] $_.ProcessId
            $Tracked.ContainsKey($processId) -and
            $Tracked[$processId] -eq [string] $_.CreationDate
        }
    )
}

<#
.SYNOPSIS
Finds Python backend processes and their descendants in a benchmark tree.

.DESCRIPTION
Descendants are included because a Python launcher may delegate work to another
process whose executable name no longer identifies it directly as the backend.
#>
function Get-BackendProcessIds {
    param([object[]] $Tree)

    $ids = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($process in $Tree) {
        if ($process.Name -match '^python(w)?\.exe$') {
            [void] $ids.Add([int] $process.ProcessId)
        }
    }

    do {
        $added = $false
        foreach ($process in $Tree) {
            if (
                $ids.Contains([int] $process.ParentProcessId) -and
                -not $ids.Contains([int] $process.ProcessId)
            ) {
                [void] $ids.Add([int] $process.ProcessId)
                $added = $true
            }
        }
    } while ($added)

    Write-Output -NoEnumerate $ids
}

<#
.SYNOPSIS
Sums one numeric CIM process property and returns zero for an empty set.
#>
function Get-Sum {
    param(
        [object[]] $Processes,
        [string] $Property
    )

    if ($Processes.Count -eq 0) {
        return 0.0
    }
    return [double] (($Processes | Measure-Object $Property -Sum).Sum)
}

<#
.SYNOPSIS
Calculates a percentile with the R-7 linear interpolation method.

.DESCRIPTION
R-7 matches the common default used by statistical tools and keeps reported
benchmark percentiles reproducible when the requested rank falls between runs.
#>
function Get-Percentile {
    param(
        [double[]] $Values,
        [ValidateRange(0.0, 1.0)]
        [double] $Probability
    )

    $ordered = @($Values | Sort-Object)
    if ($ordered.Count -eq 1) {
        return $ordered[0]
    }

    # R-7 linear interpolation: rank = (n - 1) * p.
    $rank = ($ordered.Count - 1) * $Probability
    $lower = [math]::Floor($rank)
    $upper = [math]::Ceiling($rank)
    if ($lower -eq $upper) {
        return $ordered[$lower]
    }
    $weight = $rank - $lower
    return $ordered[$lower] + (($ordered[$upper] - $ordered[$lower]) * $weight)
}

<#
.SYNOPSIS
Stops the exact benchmark process tree without affecting PID-reuse successors.

.DESCRIPTION
The owned root process is terminated directly, then descendants are stopped only
after a fresh PID-and-creation-time identity check.
#>
function Stop-BenchmarkProcessTree {
    param(
        [System.Diagnostics.Process] $RootProcess,
        [System.Collections.Generic.Dictionary[int, string]] $Tracked
    )

    # Capture descendants while the exact root identity is still alive. Child
    # creation times must follow their exact parent, preventing PID reuse from
    # turning an unrelated older process into a cleanup target.
    $snapshot = Get-ProcessSnapshot
    Expand-TrackedProcesses -Tracked $Tracked -Snapshot $snapshot

    # The root Process object belongs to this benchmark invocation. Kill only
    # that process here; descendants are handled by the identity-checked pass.
    try {
        if (-not $RootProcess.HasExited) {
            $RootProcess.Kill()
            [void] $RootProcess.WaitForExit(3000)
        }
    } catch {
        # The identity-checked descendant pass below is the final fallback.
    }

    $snapshot = Get-ProcessSnapshot
    $live = @(Get-LiveTrackedProcesses -Tracked $Tracked -Snapshot $snapshot)
    foreach ($process in $live) {
        $identity = Get-CimInstance Win32_Process -Filter (
            'ProcessId = {0}' -f $process.ProcessId
        )
        if (
            $null -ne $identity -and
            [string] $identity.CreationDate -eq $Tracked[[int] $process.ProcessId]
        ) {
            Stop-Process -Id ([int] $process.ProcessId) -Force -ErrorAction SilentlyContinue
        }
    }
}

$resolvedExecutable = (Resolve-Path -LiteralPath $Executable).Path
$resolvedProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$pythonExecutable = Join-Path $resolvedProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    throw 'The benchmark requires the repository Python virtual environment.'
}
$samples = [System.Collections.Generic.List[object]]::new()
$tempRoot = [System.IO.Path]::GetFullPath(
    [System.IO.Path]::GetTempPath()
).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
$profileDirectory = Join-Path $tempRoot (
    'elysia-electron-benchmark-{0}' -f [guid]::NewGuid().ToString('N')
)
$profileDirectory = [System.IO.Path]::GetFullPath($profileDirectory)
if ([System.IO.Path]::GetDirectoryName($profileDirectory) -ne $tempRoot) {
    throw 'Benchmark profile path escaped the system temporary directory.'
}
[void] (New-Item -ItemType Directory -Path $profileDirectory)

try {
    foreach ($run in 1..$Runs) {
        $rootProcess = $null
        $tracked = [System.Collections.Generic.Dictionary[int, string]]::new()
        try {
        $environment = @{
            ELYSIA_PROJECT_ROOT = $resolvedProjectRoot
            ELYSIA_PYTHON = $pythonExecutable
        }
        $arguments = @("--user-data-dir=$profileDirectory")

        $startParameters = @{
            FilePath = $resolvedExecutable
            PassThru = $true
            WindowStyle = 'Normal'
            Environment = $environment
        }
        $startParameters.ArgumentList = $arguments

        $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
        $rootProcess = Start-Process @startParameters
        $rootCim = Get-CimInstance Win32_Process -Filter (
            'ProcessId = {0}' -f $rootProcess.Id
        )
        if ($null -eq $rootCim) {
            throw "Run $run exited before its process identity was captured."
        }
        $tracked.Add($rootProcess.Id, [string] $rootCim.CreationDate)

        $startupMilliseconds = $null
        $deadline = [DateTimeOffset]::UtcNow.AddSeconds(20)
        while ([DateTimeOffset]::UtcNow -lt $deadline) {
            if ($rootProcess.HasExited) {
                break
            }
            $rootProcess.Refresh()
            $window = $rootProcess.MainWindowHandle
            if (
                $window -ne [IntPtr]::Zero -and
                [ElysiaBenchmarkNative]::IsWindowVisible($window) -and
                -not [ElysiaBenchmarkNative]::IsIconic($window)
            ) {
                $startupMilliseconds = $stopwatch.Elapsed.TotalMilliseconds
                break
            }
            Start-Sleep -Milliseconds 20
        }

        if ($null -eq $startupMilliseconds) {
            throw "Run $run did not create a visible window within 20 seconds."
        }

        Start-Sleep -Seconds $IdleSeconds
        $snapshot = Get-ProcessSnapshot
        Expand-TrackedProcesses -Tracked $tracked -Snapshot $snapshot
        $tree = @(Get-ProcessTree `
            -RootProcessId $rootProcess.Id `
            -RootCreationDate $tracked[$rootProcess.Id] `
            -Snapshot $snapshot)
        $backendIds = Get-BackendProcessIds -Tree $tree
        $shellTree = @(
            $tree | Where-Object {
                -not $backendIds.Contains([int] $_.ProcessId)
            }
        )
        $backendTree = @(
            $tree | Where-Object {
                $backendIds.Contains([int] $_.ProcessId)
            }
        )
        if ($backendTree.Count -eq 0) {
            throw "Run $run did not start the Python Backend."
        }

        $shellWorkingSet = Get-Sum $shellTree 'WorkingSetSize'
        $shellPrivate = Get-Sum $shellTree 'PrivatePageCount'
        $backendWorkingSet = Get-Sum $backendTree 'WorkingSetSize'
        $backendPrivate = Get-Sum $backendTree 'PrivatePageCount'

        $rootProcess.Refresh()
        $closeWindow = $rootProcess.MainWindowHandle
        $closeRequested = (
            $closeWindow -ne [IntPtr]::Zero -and
            [ElysiaBenchmarkNative]::PostMessage(
                $closeWindow,
                $wmClose,
                [IntPtr]::Zero,
                [IntPtr]::Zero
            )
        )
        $closeDeadline = [DateTimeOffset]::UtcNow.AddSeconds(5)
        while (
            -not $rootProcess.HasExited -and
            [DateTimeOffset]::UtcNow -lt $closeDeadline
        ) {
            $snapshot = Get-ProcessSnapshot
            Expand-TrackedProcesses -Tracked $tracked -Snapshot $snapshot
            Start-Sleep -Milliseconds 50
            $rootProcess.Refresh()
        }

        $forcedTermination = -not $rootProcess.HasExited
        if ($forcedTermination) {
            try {
                $rootProcess.Kill()
            } catch {
                # The orphan check and per-run finally verify the outcome.
            }
            [void] $rootProcess.WaitForExit(3000)
        }
        $rootExitCode = if ($rootProcess.HasExited) {
            $rootProcess.ExitCode
        } else {
            $null
        }
        $unforcedZeroExit = (
            $closeRequested -and
            -not $forcedTermination -and
            $rootExitCode -eq 0
        )

        $remaining = @()
        $exitDeadline = [DateTimeOffset]::UtcNow.AddSeconds(3)
        do {
            $rootProcess.Refresh()
            $rootStillRunning = -not $rootProcess.HasExited
            $snapshot = Get-ProcessSnapshot
            Expand-TrackedProcesses -Tracked $tracked -Snapshot $snapshot
            $remaining = @(
                Get-LiveTrackedProcesses `
                    -Tracked $tracked `
                    -Snapshot $snapshot |
                    Where-Object {
                        $_.ProcessId -ne $rootProcess.Id -or $rootStillRunning
                    }
            )
            if ($remaining.Count -gt 0) {
                Start-Sleep -Milliseconds 50
            }
        } while (
            $remaining.Count -gt 0 -and
            [DateTimeOffset]::UtcNow -lt $exitDeadline
        )

        $samples.Add([PSCustomObject]@{
            run = $run
            startupMilliseconds = [math]::Round($startupMilliseconds, 2)
            shellProcessCount = $shellTree.Count
            shellWorkingSetMiB = [math]::Round($shellWorkingSet / 1MB, 2)
            shellPrivateMiB = [math]::Round($shellPrivate / 1MB, 2)
            backendProcessCount = $backendTree.Count
            backendWorkingSetMiB = [math]::Round($backendWorkingSet / 1MB, 2)
            backendPrivateMiB = [math]::Round($backendPrivate / 1MB, 2)
            rootExitCode = $rootExitCode
            unforcedZeroExit = [bool] $unforcedZeroExit
            forcedTermination = [bool] $forcedTermination
            orphanProcessCount = $remaining.Count
            orphanProcesses = @(
                $remaining | ForEach-Object {
                    [PSCustomObject]@{
                        processId = [int] $_.ProcessId
                        name = [string] $_.Name
                        creationDate = [string] $_.CreationDate
                    }
                }
            )
        })

        if ($remaining.Count -gt 0) {
            throw "Run $run left $($remaining.Count) tracked orphan process(es)."
        }
        Start-Sleep -Milliseconds 500
        } finally {
            if ($null -ne $rootProcess) {
                Stop-BenchmarkProcessTree `
                    -RootProcess $rootProcess `
                    -Tracked $tracked
                $rootProcess.Dispose()
            }
        }
    }

    $profilePopulated = @(
        Get-ChildItem -LiteralPath $profileDirectory -Force -ErrorAction SilentlyContinue |
            Select-Object -First 1
    ).Count -gt 0
    $firstRun = $samples[0]
    $warmSamples = @($samples | Select-Object -Skip 1)

    <#
    .SYNOPSIS
    Computes one rounded percentile from warm runs for the requested metric.
    #>
    function Get-WarmMetric {
        param(
            [string] $Property,
            [double] $Probability
        )
        $values = @(
            $warmSamples | ForEach-Object { [double] $_.$Property }
        )
        return [math]::Round((Get-Percentile $values $Probability), 2)
    }

    $result = [PSCustomObject]@{
        shell = 'Electron'
        executable = $resolvedExecutable
        runs = $Runs
        idleSeconds = $IdleSeconds
        buildMode = 'release'
        windowReadyCriterion = 'renderer-ready-gated, visible, non-minimized native main window'
        profilePolicy = 'isolated empty profile; run 1 initializes; later runs reuse it'
        profilePopulated = $profilePopulated
        percentileMethod = 'R-7 linear interpolation over runs 2..N'
        firstRun = $firstRun
        warmRunCount = $warmSamples.Count
        warmP50StartupMilliseconds = Get-WarmMetric 'startupMilliseconds' 0.50
        warmP90StartupMilliseconds = Get-WarmMetric 'startupMilliseconds' 0.90
        warmP50ShellWorkingSetMiB = Get-WarmMetric 'shellWorkingSetMiB' 0.50
        warmP90ShellWorkingSetMiB = Get-WarmMetric 'shellWorkingSetMiB' 0.90
        warmP50ShellPrivateMiB = Get-WarmMetric 'shellPrivateMiB' 0.50
        warmP90ShellPrivateMiB = Get-WarmMetric 'shellPrivateMiB' 0.90
        warmP50BackendWorkingSetMiB = Get-WarmMetric 'backendWorkingSetMiB' 0.50
        unforcedZeroExitRuns = @($samples | Where-Object unforcedZeroExit).Count
        orphanFreeRuns = @(
            $samples | Where-Object { $_.orphanProcessCount -eq 0 }
        ).Count
        samples = $samples
    }
} finally {
    if (Test-Path -LiteralPath $profileDirectory) {
        Remove-Item -LiteralPath $profileDirectory -Recurse -Force
    }
}

$result | ConvertTo-Json -Depth 6
