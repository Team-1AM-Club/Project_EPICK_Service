param(
    [ValidateSet('collaboration', 'learning', 'diagnostic', 'semantic', 'tests')]
    [string]$Scenario = 'collaboration',
    [ValidateSet('rules', 'solar')]
    [string]$Engine = 'rules',
    [string]$PythonPath,
    [string]$OutputPath
)
$ErrorActionPreference = 'Stop'
$w4Repository = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) {
    $w4LocalPython = Join-Path $w4Repository '.venv\Scripts\python.exe'
    $w4BundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $w4LocalPython -PathType Leaf) {
        $PythonPath = $w4LocalPython
    } elseif (Test-Path -LiteralPath $w4BundledPython -PathType Leaf) {
        $PythonPath = $w4BundledPython
    } else {
        $w4PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($w4PythonCommand -and $w4PythonCommand.Source -notmatch '\\WindowsApps\\') {
            $PythonPath = $w4PythonCommand.Source
        }
    }
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'Python 3.10+ is required. Provide its executable with -PythonPath.'
}
Push-Location -LiteralPath $w4Repository
try {
    if ($Scenario -eq 'tests') {
        & $PythonPath -X utf8 -m unittest discover -s tests -v
    } else {
        $w4Sample = switch ($Scenario) {
            'learning' { 'samples/w4_learning.json' }
            'semantic' { 'samples/w4_semantic.json' }
            default { 'samples/w4_collaboration.json' }
        }
        if (-not $OutputPath) {
            $w4Stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ')
            $OutputPath = Join-Path $w4Repository ("output\$Scenario-$Engine-$w4Stamp.json")
        }
        $w4Arguments = @('-X', 'utf8', '-m', 'epick_w4', '--input', $w4Sample, '--user-id', 'user-demo', '--engine', $Engine, '--output', $OutputPath)
        if ($Scenario -eq 'diagnostic') {
            $w4Arguments += @('--knowledge-file', 'samples/upstream/skhynix_claim_handoff_sample.json')
        }
        & $PythonPath @w4Arguments
    }
    if ($LASTEXITCODE -ne 0) { throw "W4 execution failed with exit code $LASTEXITCODE." }
} finally {
    Pop-Location
}
