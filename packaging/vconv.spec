# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：one-folder + windowed，显式打包 static 资源。

在 macOS 上产物为 dist/VConv.app，Windows 上为 dist/VConv/。
ffmpeg 二进制不随包分发（见 README 许可说明），运行时一键下载或手动指定。
"""

import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        # Flask 经典坑：static 目录不会自动进包，必须显式声明
        (os.path.join(ROOT, "vconv", "static"), os.path.join("vconv", "static")),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc", "doctest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VConv",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,       # --windowed：不弹终端窗口
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
    upx=False,
    upx_exclude=[],
    name="VConv",
)

# macOS .app 壳（PyInstaller 6.x 起需显式 BUNDLE；不签名，分发后需右键打开）
app = BUNDLE(
    coll,
    name="VConv.app",
    icon=None,
    bundle_identifier="com.heidagu.vconv",
)
