@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo  角色面板图批量生成（半身胸像 1:1）
echo  断点续跑：已存在的图会跳过，可随时中断重开
echo ============================================
echo.
echo 使用底模：anima29B_v10_bf16（角色特征还原更强）
echo 服装预设：需先跑 enable_all_outfits.py on
echo.
set ROLE_UNET=Anima\anima29B_v10_bf16.safetensors
"S:\AI_workV5\python_embeded\python.exe" "%~dp0build_char_panels.py" run
echo.
echo 跑完了。缩略图已同步覆盖到 presets\thumbs\
pause
