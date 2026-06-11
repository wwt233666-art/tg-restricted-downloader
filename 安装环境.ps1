# ============================================================
#  Telegram 受限内容下载器 — 环境安装脚本
#  首次使用前运行此脚本，自动检测并安装所有依赖
# ============================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

# ---- 颜色辅助函数 ----
function Write-OK   { Write-Host "  [OK] " -ForegroundColor Green -NoNewline; Write-Host $args[0] }
function Write-ERR  { Write-Host "  [ERR] " -ForegroundColor Red -NoNewline; Write-Host $args[0] }
function Write-INFO { Write-Host "  [..] " -ForegroundColor Cyan -NoNewline; Write-Host $args[0] }
function Write-WARN { Write-Host "  [!]  " -ForegroundColor Yellow -NoNewline; Write-Host $args[0] }

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Telegram 受限内容下载器 - 环境安装" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "本脚本将自动完成以下步骤:"
Write-Host "  1. 检测 Python 是否已安装"
Write-Host "  2. 安装所需 Python 依赖包"
Write-Host "  3. 验证所有依赖就绪"
Write-Host ""
Read-Host "按 Enter 键开始安装"

$allOK = $true

# ============================================================
# 第一步：检测 Python
# ============================================================
Write-Host ""
Write-Host "--- 第1步：检测 Python ---" -ForegroundColor Yellow
Write-Host ""

$pythonCmd = $null

# 尝试常见 Python 命令
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($LASTEXITCODE -eq 0 -and $ver -match "Python (\d+\.\d+)") {
            $pythonCmd = $cmd
            $pyVer = $Matches[1]
            Write-OK "找到 Python $pyVer  (命令: $cmd)"
            break
        }
    } catch { }
}

if (-not $pythonCmd) {
    Write-ERR "未找到 Python！"
    Write-Host ""
    Write-WARN "需要手动安装 Python，步骤如下:"
    Write-Host "    1. 打开浏览器访问 https://www.python.org/downloads/"
    Write-Host "    2. 下载 Windows 安装包 (推荐 Python 3.11+)"
    Write-Host "    3. 运行安装包，务必勾选 [Add Python to PATH]"
    Write-Host "    4. 安装完成后重新运行本脚本"
    Write-Host ""
    $allOK = $false
}

if (-not $allOK) {
    Read-Host "按 Enter 键退出"
    exit 1
}

# ============================================================
# 第二步：升级 pip
# ============================================================
Write-Host ""
Write-Host "--- 第2步：升级 pip ---" -ForegroundColor Yellow
Write-Host ""

try {
    Write-INFO "正在升级 pip..."
    & $pythonCmd -m pip install --upgrade pip --quiet 2>&1 | Out-Null
    Write-OK "pip 已升级到最新版"
} catch {
    Write-WARN "pip 升级跳过（不影响使用）"
}

# ============================================================
# 第三步：安装依赖包
# ============================================================
Write-Host ""
Write-Host "--- 第3步：安装依赖包 ---" -ForegroundColor Yellow
Write-Host ""

$packages = @(
    @{Name="telethon";     Desc="Telegram MTProto 客户端库"},
    @{Name="tqdm";         Desc="下载进度条显示"},
    @{Name="python-socks"; Desc="SOCKS/HTTP 代理支持"}
)

foreach ($pkg in $packages) {
    $name = $pkg.Name
    $desc = $pkg.Desc
    Write-INFO "安装 $name ... ($desc)"

    try {
        $output = & $pythonCmd -m pip install $name --quiet 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-OK "$name 安装成功"
        } else {
            Write-WARN "$name 可能已安装或安装失败"
        }
    } catch {
        Write-ERR "$name 安装失败: $_"
        $allOK = $false
    }
}

# ============================================================
# 第四步：验证所有依赖
# ============================================================
Write-Host ""
Write-Host "--- 第4步：验证依赖 ---" -ForegroundColor Yellow
Write-Host ""

$verifyScript = @"
import sys
errors = []
try:
    import telethon
    print(f"telethon {telethon.__version__} - OK")
except Exception as e:
    errors.append(f"telethon: {e}")
try:
    import tqdm
    print(f"tqdm {tqdm.__version__} - OK")
except Exception as e:
    errors.append(f"tqdm: {e}")
try:
    import socks
    print("python-socks - OK")
except Exception as e:
    errors.append(f"python-socks: {e}")
if errors:
    print("ERRORS:")
    for e in errors:
        print(f"  {e}")
    sys.exit(1)
print("ALL OK")
"@

try {
    $result = & $pythonCmd -c $verifyScript 2>&1
    Write-Host $result
    if ($LASTEXITCODE -ne 0) {
        Write-ERR "依赖验证失败，请检查上方错误信息"
        $allOK = $false
    } else {
        Write-OK "所有依赖验证通过！"
    }
} catch {
    Write-ERR "验证过程出错: $_"
    $allOK = $false
}

# ============================================================
# 完成
# ============================================================
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
if ($allOK) {
    Write-Host "  环境安装完成！" -ForegroundColor Green
    Write-Host ""
    Write-Host "  下一步:"
    Write-Host "    1. 编辑 tg_downloader.py 顶部的代理配置"
    Write-Host "       (找到 USE_PROXY 和 PROXY 行，改为你的代理端口)"
    Write-Host "    2. 双击 启动.bat 开始使用"
} else {
    Write-Host "  部分步骤失败，请根据上方提示处理。" -ForegroundColor Red
}
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Read-Host "按 Enter 键退出"

