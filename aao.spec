# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：把 aao.app 打包成 onedir。

资源策略：resource/data/config **外置**在 exe 同级目录（用户可改时间轴/校准/配置），
不打进 _internal。打包后需手动把 resource/ data/ config/ 复制到 dist/aao.app/ 旁。

maafw 的 MaaFramework.dll 等（maa/bin/）随 --collect-all maa 打进 _internal/maa/bin，
运行时由 configure_paths() 设 MAAFW_BINARY_PATH 指向。

打包：uv run pyinstaller aao.spec --noconfirm
首次需先 uv pip install pyinstaller（已在 dev 依赖外，临时装）。
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

# maafw：收 Python 模块 + maa/bin 下的 DLL（ctypes 加载，PyInstaller 静态分析发现不了）
maa_datas, maa_binaries, maa_hiddenimports = collect_all("maa")

# registry 用 importlib.import_module("custom.action") 动态扫描，
# PyInstaller 发现不了 custom.action/reco 下的子模块，必须显式收集。
custom_hiddenimports = collect_submodules("custom") + collect_submodules("aao")

hiddenimports = (
    [
        # loguru enqueue=True 用 multiprocessing，需显式收
        "multiprocessing",
        "multiprocessing.spawn",
        # websockets / json5 有动态导入
        "websockets",
        "json5",
        "json5.lib",
        # aao.utils.logger 用 _PercentLogger，确保收集
        "aao.utils.logger",
    ]
    + maa_hiddenimports
    + custom_hiddenimports
)

a = Analysis(
    ["aao/app.py"],
    pathex=[],
    binaries=maa_binaries,
    datas=maa_datas,  # 只打 maafw 自带资源，项目 resource/data/config 外置
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # 瘦身：排除用不到的 PySide6 大模块
        "PySide6.Qt3DAnimation",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DExtras",
        "PySide6.Qt3DInput",
        "PySide6.Qt3DLogic",
        "PySide6.Qt3DRender",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtGraphs",
        "PySide6.QtLocation",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtNetworkAuth",
        "PySide6.QtNfc",
        "PySide6.QtOpenGL",
        "PySide6.QtOpenGLWidgets",
        # QML / Quick 全家桶：我们纯 QtWidgets，不用 QML
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuick3D",
        "PySide6.QtQuickControls2",
        "PySide6.QtQuickWidgets",
        "PySide6.QtQmlModels",
        "PySide6.QtQmlWorkerScript",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtPositioning",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuick3D",
        "PySide6.QtQuickControls2",
        "PySide6.QtQuickWidgets",
        "PySide6.QtRemoteObjects",
        "PySide6.QtScxml",
        "PySide6.QtSensors",
        "PySide6.QtSerialBus",
        "PySide6.QtSerialPort",
        "PySide6.QtSpatialAudio",
        "PySide6.QtSql",
        "PySide6.QtStateMachine",
        "PySide6.QtSvg",
        "PySide6.QtSvgWidgets",
        "PySide6.QtTextToSpeech",
        "PySide6.QtUiTools",
        "PySide6.QtWebChannel",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebSockets",
        "PySide6.QtXml",
        "tkinter",
        "unittest",
        "pydoc",
    ],
    noarchive=False,
)

# 瘦身：从 binaries 删除确定不用的 DLL。
# QML/Quick dll（excludes 已挡 import，但 PyInstaller 仍可能收集 Qt6Quick.dll 等本体）
# + opengl32sw.dll（软件 OpenGL 兜底，桌面客户端有 GPU 不需要）
# + 不用的 maafw control unit（Win32-only，不用 ADB/Gamepad/Replay/Record）
_STRIP_DLLS = (
    "Qt6Quick", "Qt6Qml", "Qt6Quick3D", "Qt6QuickControls2",
    "Qt6QuickShapes", "Qt6QuickTemplates2", "Qt6QuickParticles",
    "Qt6QmlModels", "Qt6QmlWorkerScript", "Qt6LabsQml", "Qt6LabsSettings",
    "Qt6Pdf", "Qt6PdfWidgets",
    "opengl32sw",
    "d3dcompiler",
    "MaaAdbControlUnit",
    "MaaGamepadControlUnit",
    "MaaReplayControlUnit",
    "MaaRecordControlUnit",
    "MaaCustomControlUnit",
)


def _should_strip(dest: str) -> bool:
    name = dest.replace("\\", "/").rsplit("/", 1)[-1].lower()
    base = name.rsplit(".", 1)[0]
    return any(base == s.lower() for s in _STRIP_DLLS)


a.binaries = [b for b in a.binaries if not _should_strip(b[0])]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="aao.app",
    # debug=False,  # 稳定后改 True 去掉控制台（windowed 模式）
    console=True,  # 调试期保留控制台看 loguru 输出
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="aao.app",
)
