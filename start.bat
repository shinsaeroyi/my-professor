@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo ====================================
echo    교수님 에이전트 시작합니다...
echo ====================================
echo.

REM Python 설치 확인
python --version >nul 2>&1
if errorlevel 1 (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo.
    echo  1. https://www.python.org/downloads/ 접속
    echo  2. "Download Python" 클릭하여 설치
    echo  3. 설치 시 "Add Python to PATH" 반드시 체크!
    echo  4. 설치 후 이 파일을 다시 실행해주세요.
    echo.
    pause
    exit
)

echo [1/2] Python 확인 완료

REM 패키지 설치 확인 (flask가 없으면 전체 설치)
python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo [2/2] 필요한 패키지 설치 중... (최초 1회, 1~3분 소요)
    echo       잠시만 기다려주세요...
    pip install -r requirements.txt --quiet
    if errorlevel 1 (
        echo.
        echo [오류] 패키지 설치에 실패했습니다.
        echo  pip가 없다면: python -m ensurepip --upgrade 실행 후 다시 시도
        echo.
        pause
        exit
    )
    echo       패키지 설치 완료!
) else (
    echo [2/2] 패키지 확인 완료
)

echo.
echo ====================================
echo    브라우저가 자동으로 열립니다!
echo    종료하려면 이 창을 닫으세요.
echo ====================================
echo.

python app.py
pause
