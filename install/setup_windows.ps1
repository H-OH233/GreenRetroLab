# =====================================================================
# GreenRetroLab 新机一键安装引导（Windows / PowerShell）
# ---------------------------------------------------------------------
# 做什么：
#   1) 找到（或自动安装）Miniconda
#   2) 创建两个 conda 环境：molprop（性质预测）与 aizynthfinder（逆合成+报告）
#   3) 按已验证过的依赖组合安装（含国内镜像加速）
#   4) 写 env_config.json / env_config.bat，供 start.bat 自动定位解释器
#   5) 运行环境自检（mol_report.py --doctor）
#
# 用法（在本文件夹上右键“在终端中打开”，或 PowerShell 里）：
#     powershell -ExecutionPolicy Bypass -File install\setup_windows.ps1
# 常用参数：
#     -InstallMiniconda        没装 conda 时自动下载安装 Miniconda（约 130MB）
#     -Mirror <url>            指定 pip 镜像（默认清华，实测比直连快 40 倍）
#     -SkipMolprop             只装逆合成环境
#     -SkipAizynth             只装性质预测环境
#     -Force                   已存在的环境也重建
# =====================================================================
param(
    [string]$Mirror = "https://pypi.tuna.tsinghua.edu.cn/simple",
    [string]$TorchIndex = "https://download.pytorch.org/whl/cpu",
    [string]$MolpropEnv = "molprop",
    [string]$AizynthEnv = "aizynthfinder",
    [switch]$InstallMiniconda,
    [switch]$SkipMolprop,
    [switch]$SkipAizynth,
    [switch]$Force
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot          # 包根目录（install 的上一级）
$MinicondaRoot = Join-Path $env:USERPROFILE "miniconda3"

function Info($m) { Write-Host "[信息] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[成功] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[注意] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[失败] $m" -ForegroundColor Red }

Write-Host "======================================================================"
Write-Host " GreenRetroLab 安装引导"
Write-Host " 程序目录 : $Root"
Write-Host " pip 镜像 : $Mirror"
Write-Host "======================================================================"

# ---------------------------------------------------------------- 1) conda
function Find-Conda {
    $cmd = Get-Command conda -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @((Join-Path $MinicondaRoot "Scripts\conda.exe"),
                     "C:\ProgramData\miniconda3\Scripts\conda.exe",
                     (Join-Path $env:USERPROFILE "anaconda3\Scripts\conda.exe"),
                     "D:\miniconda3\Scripts\conda.exe")) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Install-MinicondaNow {
    $exe = Join-Path $env:TEMP "miniconda_setup.exe"
    $url = "https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe"
    Info "下载 Miniconda（约 130MB）..."
    try {
        Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing
    } catch {
        Fail "Miniconda 下载失败：$($_.Exception.Message)"
        Warn "请手动下载安装：$url，安装时勾选 Just Me，然后重新运行本脚本。"
        return $false
    }
    Info "静默安装到 $MinicondaRoot ..."
    Start-Process -FilePath $exe -ArgumentList "/InstallationType=JustMe", "/RegisterPython=0",
        "/AddToPath=0", "/S", "/D=$MinicondaRoot" -Wait
    return (Test-Path (Join-Path $MinicondaRoot "Scripts\conda.exe"))
}

$conda = Find-Conda
if (-not $conda) {
    Warn "没有找到 conda。"
    if ($InstallMiniconda) {
        if (Install-MinicondaNow) { $conda = Find-Conda } else { exit 1 }
    } else {
        Warn "两种选择："
        Warn "  A) 重新运行本脚本并加上 -InstallMiniconda 参数（自动下载安装）"
        Warn "  B) 手动安装 Miniconda 后重新运行：https://docs.conda.io/en/latest/miniconda.html"
        exit 1
    }
}
if (-not $conda) { Fail "仍然找不到 conda，请手动安装后重试。"; exit 1 }
Ok "conda: $conda"

# ---------------------------------------------------------------- 工具函数
function Get-EnvPython([string]$envName) {
    $raw = & $conda env list --json 2>$null | Out-String
    try {
        $data = $raw | ConvertFrom-Json
        foreach ($e in $data.envs) {
            if ((Split-Path $e -Leaf) -eq $envName) {
                $py = Join-Path $e "python.exe"
                if (Test-Path $py) { return $py }
            }
        }
    } catch { }
    return $null
}

function Ensure-Env([string]$envName, [string]$pyVersion) {
    $py = Get-EnvPython $envName
    if ($py -and -not $Force) { Info "环境 $envName 已存在：$py"; return $py }
    if ($py -and $Force) { Info "按 -Force 重建环境 $envName ..."; & $conda env remove -n $envName -y | Out-Null }
    Info "创建 conda 环境 $envName（python=$pyVersion）..."
    & $conda create -n $envName "python=$pyVersion" -y
    $py = Get-EnvPython $envName
    if (-not $py) { Fail "环境 $envName 创建失败"; return $null }
    Ok "环境 $envName 就绪：$py"
    return $py
}

function Pip-Install([string]$py, [string[]]$pipArgs) {
    & $py -m pip install --progress-bar off @pipArgs
    return $LASTEXITCODE
}

function Probe([string]$py, [string]$code) {
    $out = & $py -c $code 2>&1 | Out-String
    return ($LASTEXITCODE -eq 0), $out.Trim()
}

# ---------------------------------------------------------------- 2) molprop 环境
$molpropPython = $null
if (-not $SkipMolprop) {
    Write-Host ""
    Info "===== 步骤 1/3：性质预测环境（$MolpropEnv，Python 3.10 + deepchem/TF/torch）====="
    $molpropPython = Ensure-Env $MolpropEnv "3.10"
    if ($molpropPython) {
        Info "升级 pip ..."
        & $molpropPython -m pip install --upgrade pip -i $Mirror --progress-bar off | Out-Null
        $ok, $out = Probe $molpropPython "import torch"
        if ($ok) {
            Ok "torch 已存在，跳过安装"
        } else {
            Info "安装 CPU 版 PyTorch（约 200MB，避免误装 2.5GB 的 CUDA 版）..."
            $code = Pip-Install $molpropPython @("torch", "--index-url", $TorchIndex)
            if ($code -ne 0) {
                Warn "官方 CPU 源不可用，改用镜像安装 torch（体积更大，请耐心等待）..."
                Pip-Install $molpropPython @("torch", "-i", $Mirror) | Out-Null
            }
        }
        Info "安装其余依赖（约 10~20 分钟，取决于网速）..."
        $req = Join-Path $PSScriptRoot "requirements_molprop.txt"
        $code = Pip-Install $molpropPython @("-r", $req, "-i", $Mirror)
        $ok, $out = Probe $molpropPython "import numpy, sklearn, joblib, rdkit, torch, tensorflow, deepchem; print('PROBE_OK', tensorflow.__version__, deepchem.__version__)"
        if ($ok) { Ok "molprop 依赖自检通过：$($out -split "`n" | Select-String 'PROBE_OK')" }
        else { Fail "molprop 依赖自检失败：$out"; Warn "可重试：`"$molpropPython`" -m pip install -r `"$req`" -i $Mirror" }
    }
} else {
    $molpropPython = Get-EnvPython $MolpropEnv
    Info "按要求跳过 molprop 环境安装"
}

# ---------------------------------------------------------------- 3) aizynthfinder 环境
$aizynthPython = $null
if (-not $SkipAizynth) {
    Write-Host ""
    Info "===== 步骤 2/3：逆合成环境（$AizynthEnv，Python 3.11 + aizynthfinder 4.4.1）====="
    $aizynthPython = Ensure-Env $AizynthEnv "3.11"
    if ($aizynthPython) {
        Info "升级 pip ..."
        & $aizynthPython -m pip install --upgrade pip -i $Mirror --progress-bar off | Out-Null
        $req = Join-Path $PSScriptRoot "requirements_aizynthfinder.txt"
        Info "安装依赖（约 5~10 分钟）..."
        Pip-Install $aizynthPython @("-r", $req, "-i", $Mirror) | Out-Null
        Info "安装 aizynthfinder 与 reaction-utils（--no-deps，避开 route-distances 的依赖回溯死循环）..."
        Pip-Install $aizynthPython @("--no-deps", "aizynthfinder", "reaction-utils", "-i", $Mirror) | Out-Null
        Info "补齐 rxnutils 运行时依赖 ..."
        Pip-Install $aizynthPython @("wrapt-timeout-decorator", "xxhash", "swifter", "apted", "-i", $Mirror) | Out-Null
        $ok, $out = Probe $aizynthPython "import aizynthfinder.aizynthfinder, rdkit, onnxruntime, molbloom, tables, matplotlib; import importlib.metadata as m; print('PROBE_OK', m.version('aizynthfinder'), rdkit.__version__)"
        if ($ok) { Ok "aizynthfinder 依赖自检通过：$($out -split "`n" | Select-String 'PROBE_OK')" }
        else { Fail "aizynthfinder 依赖自检失败：$out" }
    }
} else {
    $aizynthPython = Get-EnvPython $AizynthEnv
    Info "按要求跳过 aizynthfinder 环境安装"
}

# ---------------------------------------------------------------- 4) 写配置
Write-Host ""
Info "===== 步骤 3/3：写入环境配置并自检 ====="
if (-not $aizynthPython) { $aizynthPython = Get-EnvPython $AizynthEnv }
if (-not $molpropPython) { $molpropPython = Get-EnvPython $MolpropEnv }

$envConfig = [ordered]@{
    molprop_python  = $molpropPython
    aizynth_python  = $aizynthPython
    ai_molprop_dir  = (Join-Path $Root "ai_molprop")
    aizynth_data    = (Join-Path $Root "aizynth_data")
    pip_mirror      = $Mirror
    installed_at    = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
}
$jsonPath = Join-Path $Root "env_config.json"
# 注意：PowerShell 的 -Encoding UTF8 会写 BOM，部分解析器会因此读不出 JSON；
# 这里主动写成「无 BOM」的 UTF-8，保证 Python 侧 json.loads 一定能解析。
[IO.File]::WriteAllText($jsonPath, ($envConfig | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))

# batch 用的小配置文件（start.bat 会 call 它）
$batLines = @(
    "@echo off",
    "rem 由 install\setup_windows.ps1 自动生成，供 start.bat 定位解释器",
    "set `"MOLREPORT_PYTHON=$aizynthPython`"",
    "set `"MOLPROP_PYTHON=$molpropPython`""
)
Set-Content -Path (Join-Path $Root "env_config.bat") -Value $batLines -Encoding ASCII
Ok "已写入 env_config.json 与 env_config.bat"

if ($aizynthPython) {
    Info "运行环境自检 ..."
    & $aizynthPython (Join-Path $Root "mol_report.py") --doctor
    $doctorCode = $LASTEXITCODE
    Write-Host ""
    if ($doctorCode -eq 0) {
        Ok "全部就绪！以后直接双击 start.bat 或运行："
    } else {
        Warn "自检还有问题（见上面 ✘ 项）。修复后可直接双击 start.bat。"
    }
} else {
    Warn "没有找到 $AizynthEnv 环境的 python，无法自检。"
}

Write-Host ""
Write-Host "----------------------------------------------------------------------"
Write-Host " 怎么用："
Write-Host "   1) 双击 start.bat（会提示你输入分子名或 SMILES）"
Write-Host "   2) 或命令行："
Write-Host "        `"$aizynthPython`" `"$Root\mol_report.py`" `"咖啡因`" `"阿司匹林`""
Write-Host "        `"$aizynthPython`" `"$Root\mol_report.py`" --inputs-file molecules_example.txt"
Write-Host "        `"$aizynthPython`" `"$Root\mol_report.py`" --doctor"
Write-Host " 报告输出在： $Root\reports"
Write-Host " 详细说明见： $Root\README.md"
Write-Host "----------------------------------------------------------------------"
