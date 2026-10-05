<#
Configura el conector de Alpha ERP en Windows (correr desde la carpeta del repo):

    powershell -ExecutionPolicy Bypass -File scripts\setup-alpha-windows.ps1
    powershell -ExecutionPolicy Bypass -File scripts\setup-alpha-windows.ps1 -DataDir "Y:\OTRA"

Hace lo siguiente:
  1. Instala uv si no está.
  2. Convierte la unidad de red (Y:) a su ruta \\SERVIDOR\... para que funcione
     aunque Claude Desktop no vea la unidad mapeada.
  3. Guarda ALPHA_DATA_DIR en .env y corre alpha-erp-check.
  4. Registra el servidor "alpha-erp" en Claude Desktop.
#>
param(
    [string]$DataDir = "\\endocat2.dyndns.org\vsai\Empresas\ENDOCAT"
)

$ErrorActionPreference = "Stop"
# UTF-8 sin BOM: Windows PowerShell 5 agrega BOM con -Encoding UTF8 y rompe el JSON de Claude Desktop.
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Write-Utf8File([string]$Path, [string]$Text) {
    # .NET rechaza sobrescribir archivos ocultos o de solo lectura ("Acceso denegado"):
    # se quitan esos atributos antes de escribir.
    if (Test-Path -LiteralPath $Path) {
        $item = Get-Item -LiteralPath $Path -Force
        $attrs = $item.Attributes -band -bnot ([IO.FileAttributes]::Hidden -bor [IO.FileAttributes]::ReadOnly)
        $item.Attributes = if ($attrs -eq 0) { [IO.FileAttributes]::Normal } else { $attrs }
    }
    [IO.File]::WriteAllText($Path, $Text, $utf8)
}
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

# 1. uv
Write-Host "[1/4] Revisando uv..."
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Instalando uv..."
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
$uv = (Get-Command uv).Source

# 2. Unidad mapeada -> ruta UNC (las unidades mapeadas son por usuario/sesión
#    y a veces no existen para procesos lanzados por otras apps).
Write-Host "[2/4] Revisando acceso a $DataDir ..."
$resolved = $DataDir
if ($DataDir -match '^([A-Za-z]):\\?(.*)$') {
    $drive = Get-PSDrive -Name $Matches[1] -ErrorAction SilentlyContinue
    if ($drive -and $drive.DisplayRoot -like '\\*') {
        $resolved = Join-Path $drive.DisplayRoot $Matches[2]
        Write-Host "$DataDir es la carpeta de red $resolved"
    }
}
if (-not (Test-Path $resolved)) {
    throw "No encuentro $resolved. Abre esa carpeta en el Explorador para confirmar que tienes acceso."
}

# 3. .env + prueba
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
$lines = @(Get-Content .env | Where-Object { $_ -notmatch '^ALPHA_DATA_DIR=' })
$lines += "ALPHA_DATA_DIR=$resolved"
Write-Utf8File (Join-Path $repo ".env") (($lines -join "`r`n") + "`r`n")

Write-Host "[3/4] Instalando dependencias (uv sync)..."
& $uv sync
Write-Host "Buscando tablas .DBF en $resolved (por la VPN puede tardar un poco)..."
& $uv run alpha-erp-check
if ($LASTEXITCODE -ne 0) { throw "alpha-erp-check falló; revisa la salida de arriba." }

# 4. Claude Desktop. La versión de Microsoft Store lee su config de una carpeta
#    virtualizada en LocalAppData\Packages, no de %APPDATA%\Claude: escribimos en ambas.
$configDirs = @(Join-Path $env:APPDATA "Claude")
Get-ChildItem (Join-Path $env:LOCALAPPDATA "Packages") -Directory -Filter "Claude_*" -ErrorAction SilentlyContinue |
    ForEach-Object { $configDirs += Join-Path $_.FullName "LocalCache\Roaming\Claude" }

$server = [pscustomobject]@{
    command = $uv
    args    = @("--directory", $repo, "run", "alpha-erp-mcp")
    env     = [pscustomobject]@{ ALPHA_DATA_DIR = $resolved }
}
Write-Host "[4/4] Registrando alpha-erp en Claude Desktop..."
foreach ($dir in $configDirs) {
    $configPath = Join-Path $dir "claude_desktop_config.json"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $config = if (Test-Path $configPath) { Get-Content $configPath -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
    if (-not $config.PSObject.Properties["mcpServers"]) {
        $config | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{})
    }
    $config.mcpServers | Add-Member -NotePropertyName "alpha-erp" -NotePropertyValue $server -Force
    Write-Utf8File $configPath ($config | ConvertTo-Json -Depth 10)
    Write-Host "Registrado alpha-erp en $configPath"
}

Write-Host ""
Write-Host "Listo. Cierra Claude Desktop por completo (también desde la bandeja) y ábrelo de nuevo."
Write-Host "Si usas Claude Code en lugar de Desktop:"
Write-Host "  claude mcp add alpha-erp -e ALPHA_DATA_DIR=`"$resolved`" -- `"$uv`" --directory `"$repo`" run alpha-erp-mcp"
