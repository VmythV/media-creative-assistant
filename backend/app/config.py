"""应用配置：全部从环境变量 / .env 读取，API Key 绝不入库。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- 云端模型（DashScope，OpenAI 兼容接口） ---
    dashscope_api_key: str | None = None
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_vl_model: str = "qwen-vl-max"
    qwen_llm_model: str = "qwen-max"

    # --- 本地模型 ---
    whisper_model: str = "small"
    ollama_base_url: str = "http://localhost:11434"

    # --- 数据目录 ---
    data_dir: Path = REPO_DIR / "data"

    # --- DaVinci Resolve 脚本环境（macOS 默认值） ---
    resolve_script_api: str = (
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
    )
    resolve_script_lib: str = (
        "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
    )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mca.db"

    @property
    def cache_dir(self) -> Path:
        """抽帧、提取音频等中间产物目录。"""
        return self.data_dir / "cache"


settings = Settings()
