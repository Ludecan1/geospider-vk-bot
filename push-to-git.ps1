# Создать публичный репозиторий на GitHub и запушить код (нужен: gh auth login)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$repo = 'geospider-vk-bot'
gh auth status | Out-Null

$pushUrl = "https://oauth2:$((gh auth token))@github.com/Ludecan1/$repo.git"

if (-not (git remote get-url origin 2>$null)) {
    gh repo create $repo --public --source=. --remote=origin
    git push $pushUrl main
    git branch --set-upstream-to=origin/main main 2>$null
} else {
    git push $pushUrl main
}

$url = (gh repo view --json url -q .url) + '.git'
Write-Host "`nGit URL для Bothost:`n$url`n"
