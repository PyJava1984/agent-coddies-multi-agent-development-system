<#
.SYNOPSIS
    Install agent-coddies into Claude Code.

.DESCRIPTION
    Copies (or links) the skill into ~/.claude/skills/agent-coddies and the three
    subagents into ~/.claude/agents. Use -Project to install into the current
    repository's .claude directory instead, so the whole team gets it via git.

.EXAMPLE
    .\install.ps1
    .\install.ps1 -Project
    .\install.ps1 -Link          # symlink instead of copy (needs Developer Mode or admin)
    .\install.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [switch]$Project,
    [switch]$Link,
    [switch]$Uninstall,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$Source = $PSScriptRoot
$Name = 'agent-coddies'

$Root = if ($Project) { Join-Path (Get-Location) '.claude' } else { Join-Path $HOME '.claude' }
$SkillDir = Join-Path $Root "skills\$Name"
$AgentDir = Join-Path $Root 'agents'
$CommandDir = Join-Path $Root 'commands'

function Write-Step($message) { Write-Host "  $message" }

if ($Uninstall) {
    Write-Host "Removing $Name from $Root" -ForegroundColor Yellow
    if (Test-Path $SkillDir) { Remove-Item $SkillDir -Recurse -Force; Write-Step "removed $SkillDir" }
    foreach ($agent in 'coddie-dev', 'coddie-qa', 'coddie-ship') {
        $path = Join-Path $AgentDir "$agent.md"
        if (Test-Path $path) { Remove-Item $path -Force; Write-Step "removed $path" }
    }
    foreach ($cmd in 'coddies.md', 'coddies-status.md') {
        $path = Join-Path $CommandDir $cmd
        if (Test-Path $path) { Remove-Item $path -Force; Write-Step "removed $path" }
    }
    Write-Host "Done. Your credentials.yaml in $Source\config was left untouched." -ForegroundColor Green
    exit 0
}

Write-Host "Installing $Name" -ForegroundColor Cyan
Write-Step "source: $Source"
Write-Step "target: $Root"

New-Item -ItemType Directory -Force -Path $AgentDir, $CommandDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $SkillDir -Parent) | Out-Null

# --- skill -----------------------------------------------------------------
if (Test-Path $SkillDir) {
    if (-not $Force) {
        Write-Host "  $SkillDir already exists. Re-run with -Force to replace it." -ForegroundColor Yellow
        exit 1
    }
    Remove-Item $SkillDir -Recurse -Force
}

if ($Link) {
    New-Item -ItemType SymbolicLink -Path $SkillDir -Target $Source | Out-Null
    Write-Step "linked  $SkillDir -> $Source"
} else {
    Copy-Item $Source -Destination $SkillDir -Recurse -Force
    # Never copy a filled-in credentials file or local run state into the install.
    foreach ($leak in 'config\credentials.yaml', '.coddies') {
        $path = Join-Path $SkillDir $leak
        if (Test-Path $path) { Remove-Item $path -Recurse -Force }
    }
    Write-Step "copied  $SkillDir"
}

# --- agents ----------------------------------------------------------------
foreach ($agent in 'coddie-dev', 'coddie-qa', 'coddie-ship') {
    $src = Join-Path $Source "agents\$agent.md"
    $dst = Join-Path $AgentDir "$agent.md"
    Copy-Item $src $dst -Force
    Write-Step "agent   $dst"
}

# --- slash commands --------------------------------------------------------
Get-ChildItem (Join-Path $Source 'commands') -Filter *.md -ErrorAction SilentlyContinue | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $CommandDir $_.Name) -Force
    Write-Step "command /$($_.BaseName)"
}

# --- credentials -----------------------------------------------------------
$creds = Join-Path $Source 'config\credentials.yaml'
$example = Join-Path $Source 'config\credentials.example.yaml'
if (-not (Test-Path $creds)) {
    Copy-Item $example $creds
    Write-Host ""
    Write-Host "Created $creds from the example." -ForegroundColor Yellow
    Write-Host "Fill in jira.*, gitlab.* and (optionally) database.* before first use." -ForegroundColor Yellow
}

# --- verify ----------------------------------------------------------------
Write-Host ""
$python = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
if ($null -eq $python) {
    Write-Host "Python was not found on PATH. Install Python 3.9+ to use the toolkit." -ForegroundColor Yellow
} else {
    & $python.Source (Join-Path $Source 'scripts\coddie_cli.py') config check --offline
}

Write-Host ""
Write-Host "Installed. In Claude Code:" -ForegroundColor Green
Write-Host "    /coddies HM-1234        run the pipeline on a ticket"
Write-Host "    /coddies-status HM-1234 show the run ledger"
Write-Host "Or just say: work on HM-1234"
