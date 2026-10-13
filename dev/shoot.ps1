# Screenshots of the wall from Windows, with headless Edge (development aid).
#   powershell -File dev\shoot.ps1 -Name playing -Url http://localhost:8737/ -Width 1920 -Height 1080
param(
  [string]$Name = "wall",
  [string]$Url = "http://localhost:8737/",
  [int]$Width = 1920,
  [int]$Height = 1080,
  [int]$Wait = 8000,
  [string]$Out = "$env:TEMP"
)
$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Google\Chrome\Application\chrome.exe") |
  Where-Object { Test-Path $_ } | Select-Object -First 1
& $edge --headless=new --hide-scrollbars --autoplay-policy=no-user-gesture-required "--window-size=$Width,$Height" "--virtual-time-budget=$Wait" "--screenshot=$Out\$Name.png" $Url 2>$null
Start-Sleep -Seconds 2
Get-Item "$Out\$Name.png"
