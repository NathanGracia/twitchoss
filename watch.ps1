param(
    [string]$Channel = "",
    [switch]$NoChat
)

$STREAMLINK    = "C:\Users\nathan\AppData\Local\Programs\Python\Python311\Scripts\streamlink.exe"
$VLC           = "C:\Program Files\VideoLAN\VLC\vlc.exe"
$QUALITY       = "best"
$CHANNELS_FILE = Join-Path $PSScriptRoot "channels.txt"

$script:ChatEnabled = -not $NoChat.IsPresent

function Show-Menu {
    $channels = Get-Content $CHANNELS_FILE | Where-Object { $_ -notmatch "^\s*#" -and $_ -match "\S" }

    Write-Host ""
    Write-Host "==============================" -ForegroundColor Cyan
    Write-Host "   Twitch -> VLC launcher" -ForegroundColor Cyan
    Write-Host "==============================" -ForegroundColor Cyan
    Write-Host ""

    $i = 1
    foreach ($ch in $channels) {
        Write-Host "  [$i] $ch" -ForegroundColor White
        $i++
    }

    Write-Host ""
    Write-Host "  [0] Taper un nom de chaîne manuellement" -ForegroundColor Yellow
    if ($script:ChatEnabled) {
        Write-Host "  [c] Chat : ON  (clic pour désactiver)" -ForegroundColor Green
    } else {
        Write-Host "  [c] Chat : OFF (clic pour activer)" -ForegroundColor DarkGray
    }
    Write-Host "  [q] Quitter" -ForegroundColor DarkGray
    Write-Host ""

    $choice = Read-Host "Choix"

    if ($choice -eq "q") { exit }

    if ($choice -eq "c") {
        $script:ChatEnabled = -not $script:ChatEnabled
        return $null
    }

    if ($choice -eq "0") {
        $custom = Read-Host "Nom de la chaîne"
        return $custom.Trim()
    }

    $idx = [int]$choice - 1
    if ($idx -ge 0 -and $idx -lt $channels.Count) {
        return $channels[$idx]
    }

    Write-Host "Choix invalide." -ForegroundColor Red
    return $null
}

function Start-Stream {
    param([string]$ch)

    $url = "twitch.tv/$ch"
    Write-Host ""
    Write-Host "Lancement de $url en $QUALITY..." -ForegroundColor Green

    if ($script:ChatEnabled) {
        $chatUrl = "https://www.twitch.tv/popout/$ch/chat?popout="
        Start-Process $chatUrl
        Write-Host "Chat ouvert dans le navigateur." -ForegroundColor DarkCyan
    }

    & $STREAMLINK `
        --player $VLC `
        --twitch-low-latency `
        $url `
        $QUALITY

    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "Erreur ou chaîne hors ligne." -ForegroundColor Red
    }
}

if ($Channel -ne "") {
    Start-Stream -ch $Channel
    exit
}

while ($true) {
    $selected = Show-Menu
    if ($selected) {
        Start-Stream -ch $selected
        Write-Host ""
        Write-Host "Appuie sur Entrée pour revenir au menu..." -ForegroundColor DarkGray
        Read-Host | Out-Null
    }
}
