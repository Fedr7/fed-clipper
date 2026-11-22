param([string]$Target, [string]$Script, [string]$Dir, [string]$Icon)
$ws = New-Object -ComObject WScript.Shell
$places = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
foreach ($folder in $places) {
    if (-not $folder) { continue }
    # replace the old "Fed Clipper" shortcut (the app's previous name)
    $old = Join-Path $folder 'Fed Clipper.lnk'
    if (Test-Path $old) { Remove-Item $old -ErrorAction SilentlyContinue }
    $lnk = $ws.CreateShortcut((Join-Path $folder 'Breachcut.lnk'))
    $lnk.TargetPath = $Target
    $lnk.Arguments = '"' + $Script + '"'
    $lnk.WorkingDirectory = $Dir
    $lnk.IconLocation = $Icon
    $lnk.Description = 'Breachcut - turn your Siege clips into TikToks and YouTube edits'
    $lnk.Save()
}
Write-Host ' - Shortcuts added to your Desktop and Start menu'
