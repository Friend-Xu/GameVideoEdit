@echo off
chcp 65001 >nul
echo ============================================
echo  GameVideoEdit 环境安装
echo ============================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到Python, 请先安装 Python 3.10+
    pause
    exit /b 1
)

if not exist ".venv" (
    echo [1/3] 创建虚拟环境...
    python -m venv .venv
) else (
    echo [1/3] 虚拟环境已存在, 跳过
)

echo [2/3] 安装依赖...
call .venv\Scripts\activate
pip install -r requirements.txt

echo [3/3] 验证模型文件...
.venv\Scripts\python scripts\verify_models.py

echo.
echo ============================================
echo  安装完成! 运行: .venv\Scripts\python app\main.py
echo ============================================
pause
