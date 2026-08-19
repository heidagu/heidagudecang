# Windows 打包步骤（内网主机手动执行）

在任意 Windows 10/11 x64 主机上执行，产物为 `VConv-windows-x64-v0.1.0.zip`，
拷回后上传到 GitHub release 即可。

## 0. 前提

- 已安装 [Python 3.9+（64 位）](https://www.python.org/downloads/windows/)
  （安装时勾选 **Add Python to PATH**；验证：`python --version`）
- 主机能访问 PyPI（`pip` 需要联网）。**若无外网**：把主机 Python 版本
  （`python --version`）告知，在 Mac 上用 `pip download --platform win_amd64`
  生成离线 wheel 包，随源码一起拷过去，用 `pip install --no-index --find-links wheels -r requirements-dev.txt` 安装。

## 1. 传输源码

把 Mac 上的 `/tmp/vconv-windows-src.zip`（git archive 快照）拷到 Windows 主机
（U 盘 / 内网共享均可），解压到例如 `C:\vconv-src\`。

## 2. 执行打包

**方式一（推荐）**：在资源管理器中**双击 `packaging\build_windows.bat`**，一键完成。

**方式二**：打开 PowerShell 手动执行：

```powershell
cd C:\vconv-src
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

脚本会自动完成：

1. 创建独立虚拟环境 `.venv-build`（不影响系统 Python）
2. `pip install -r requirements-dev.txt`（Flask + PyInstaller）
3. `pyinstaller packaging\vconv.spec` → `dist\VConv\`
4. 压缩为 `VConv-windows-x64-v0.1.0.zip`（在仓库根目录）

## 3. 冒烟测试（可选但建议）

```powershell
# 启动打包产物（默认端口 8756，可用 --port 指定）
dist\VConv\VConv.exe --no-browser
```

浏览器打开 `http://127.0.0.1:8756/`：
- 右上角 ffmpeg 状态正常（未安装时点「一键下载」应能下载 gyan.dev 构建）
- 添加一个视频文件 → 开始转换 → 左侧进度条推进 → 完成

## 4. 传回并上传 release

把 `VConv-windows-x64-v0.1.0.zip` 拷回 Mac 项目目录，执行：

```bash
gh release upload v0.1.0 VConv-windows-x64-v0.1.0.zip
```

## 常见问题

- **`pyinstaller` 不是内部或外部命令** → 用完整路径
  `.venv-build\Scripts\pyinstaller.exe`（脚本里已用完整路径，通常不会遇到）
- **杀毒软件拦截** → PyInstaller 产物常见误报；在 Windows Defender 中放行
  `dist\VConv\` 后重试，或改用 GitHub Actions 备用渠道（release.yml 勾选 windows）
- **下载 ffmpeg 失败** → 主机无外网时：在任意有网机器下载 gyan.dev 的
  ffmpeg essentials zip，解压出 `ffmpeg.exe`/`ffprobe.exe` 放进
  `%LOCALAPPDATA%\vconv\ffmpeg\`（目录不存在就创建），重启 VConv 即自动检测到
