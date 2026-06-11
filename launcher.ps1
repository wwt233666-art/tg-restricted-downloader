[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host ''
Write-Host '============================================' -ForegroundColor Cyan
Write-Host '  Telegram 受限内容下载器' -ForegroundColor Cyan
Write-Host '============================================' -ForegroundColor Cyan
Write-Host ''
Write-Host '  1. 启动下载器（含使用指南）'
Write-Host '  2. 快速启动（跳过指南）'
Write-Host '  3. 查看频道列表'
Write-Host '  4. 只看私密频道'
Write-Host '  5. 切换/新建账户'
Write-Host '  6. 登出账户'
Write-Host ''

$choice = Read-Host '请选择 (1-6)'

switch ($choice) {
    '1' { python tg_downloader.py --interactive }
    '2' { python tg_downloader.py --interactive --skip-guide }
    '3' { python tg_downloader.py --list }
    '4' { python tg_downloader.py --list --private }
    '5' {
        $account = Read-Host '新账户名(英文)'
        python tg_downloader.py --session $account --interactive --skip-guide
    }
    '6' {
        $account = Read-Host '要登出的账户名(默认tg_user_session)'
        if (-not $account) { $account = 'tg_user_session' }
        python tg_downloader.py --session $account --logout
    }
}

Read-Host '按 Enter 键退出'

