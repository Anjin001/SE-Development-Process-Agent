"""读取项目根目录下的 .env。

模型地址、模型名和密钥都从环境变量取；把本地配置写进 .env，就不必每次改
start-local.cmd 或系统环境变量。已存在的环境变量优先，.env 只补空缺，
因此临时 `set SE_AGENT_MODEL=...` 仍然能覆盖文件里的值。
"""
import os
from pathlib import Path

DEFAULT_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def parse_env(text):
    """把 .env 文本解析成键值对，忽略空行、注释和不含等号的行。"""
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key.replace("_", "").isalnum() and key[0] not in "0123456789":
            values[key] = value
    return values


def load_env(path=None):
    """把 .env 里的键值补进 os.environ，返回实际读取的文件（缺失时返回 None）。"""
    target = Path(path or os.environ.get("SE_AGENT_ENV_FILE") or DEFAULT_ENV_PATH)
    try:
        text = target.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    for key, value in parse_env(text).items():
        os.environ.setdefault(key, value)
    return target
