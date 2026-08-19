import os
import shutil
import sys
import tempfile

# 保证 vconv 包可导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vconv import config  # noqa: E402

# 记录真实缓存目录（env 尚未覆盖时）
_real_cache = config.ffmpeg_cache_dir()

# 测试不污染真实配置/历史：全部写到临时目录
_test_data = os.path.join(tempfile.gettempdir(), "vconv_test_data")
os.environ.setdefault("VCONV_DATA_DIR", _test_data)

# 若真实缓存目录里已有一键下载的 ffmpeg，软链接到测试目录供集成测试使用
# （CI 上无缓存目录时，集成测试会通过 PATH 找到 brew/apt/choco 安装的 ffmpeg）
_cache = config.ffmpeg_cache_dir()
os.makedirs(_cache, exist_ok=True)
for name in ("ffmpeg", "ffprobe"):
    if os.name == "nt":
        name += ".exe"
    src = os.path.join(_real_cache, name)
    dst = os.path.join(_cache, name)
    if os.path.isfile(src) and not os.path.exists(dst):
        try:
            os.symlink(src, dst)
        except OSError:      # Windows 无权限创建符号链接时退回复制
            try:
                shutil.copy2(src, dst)
            except OSError:
                pass
