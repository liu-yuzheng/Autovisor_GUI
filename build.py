import os
import shutil
import subprocess
import sys

# 默认构建命令行版; 加 --gui 构建图形界面版(窗口模式, 无控制台窗口)。
GUI_BUILD = "--gui" in sys.argv[1:]
name = "AutovisorGUI" if GUI_BUILD else "Autovisor"
entry = "GUI.py" if GUI_BUILD else "Autovisor.py"

# 必须与 modules.installer.SUPPORTED_PYTHON 一致: 冻结产物内嵌的解释器会由
# Autovisor.exe 启动时校验, 不在范围内会直接拒绝运行。
BUILD_PYTHON = ((3, 10), (3, 11), (3, 12), (3, 13))

ROOT = os.path.dirname(os.path.abspath(__file__))


def check_build_python():
    """用固定的解释器构建, 避免 PATH 上另一个 pyinstaller 悄悄换掉运行时。"""
    if sys.version_info[:2] not in BUILD_PYTHON:
        supported = ", ".join(f"{major}.{minor}" for major, minor in BUILD_PYTHON)
        raise SystemExit(
            f"构建需要 Python {supported}, 当前是 {sys.version_info[0]}.{sys.version_info[1]}"
            f"({sys.executable}); 否则打包出来的 {name}.exe 会在启动时拒绝运行。"
        )
    try:
        import PyInstaller  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            f"当前 Python 缺少 PyInstaller: {sys.executable}\n"
            "可执行: uv run --with pyinstaller python build.py"
        ) from exc


check_build_python()

cmd = [
    sys.executable,
    "-m",
    "PyInstaller",
    "--log-level=INFO",
    "--noconfirm",
    "-i",
    os.path.join(ROOT, "resources", "zhs.ico"),
    "--onedir",
    "--contents-directory=internal",
    f"--name={name}",
    os.path.join(ROOT, entry),
    "--exclude-module",
    "cv2",
    "--exclude-module",
    "numpy",
]
# 命令行版保留控制台(便于 --check-browser 等参数); 界面版必须是窗口模式。
# 注意: PyInstaller 的默认值是"控制台程序", 只写 --onedir 不写 -w 的话,
# 双击 exe 会先弹一个命令提示符窗口(子系统 = 3 CONSOLE), 必须显式给 -w。
cmd.insert(4, "-w" if GUI_BUILD else "-c")

code = subprocess.call(cmd)
if code != 0:
    raise SystemExit(f"pyinstaller 构建失败: {name} (exit={code})")

dist_dir = f"./dist/{name}"
os.makedirs(os.path.join(dist_dir, "resources"), exist_ok=True)
os.makedirs(os.path.join(dist_dir, "data"), exist_ok=True)
shutil.copyfile("./resources/QRcode.jpg", f"{dist_dir}/resources/QRcode.jpg")
shutil.copyfile("./config.ini.example", f"{dist_dir}/config.ini")
shutil.copyfile("./data/mirrors.json", f"{dist_dir}/data/mirrors.json")
shutil.copyfile("./resources/stealth.min.js", f"{dist_dir}/resources/stealth.min.js")
# 界面文案单独放在 exe 同目录: 改文字不用重新打包,
# 保存后重启程序即生效(打包进 internal 的那份只是删掉文件时的兜底)。
shutil.copyfile("./ui_text.py", f"{dist_dir}/ui_text.py")

# 稳定 ABI (abi3) 扩展(如 opencv 的 cv2.pyd)依赖 python3.dll,
# PyInstaller 不会自动收集,必须随包提供,否则运行时报 "DLL load failed while importing cv2".
python3_dll = os.path.join(sys.base_prefix, "python3.dll")
if os.path.isfile(python3_dll):
    shutil.copyfile(python3_dll, f"{dist_dir}/internal/python3.dll")
else:
    print(f"WARNING: 未找到 {python3_dll}, 发行版可能因缺少 python3.dll 导致 cv2 加载失败!")

shutil.rmtree("./build", ignore_errors=True)
spec_file = f"./{name}.spec"
if os.path.isfile(spec_file):
    os.remove(spec_file)

print(f"构建完成: {dist_dir}")
