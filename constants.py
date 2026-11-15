"""AstrBot 万象画卷插件常量。"""

# API 超时配置
API_TIMEOUT_DEFAULT = 60.0
API_TIMEOUT_SLOW = 120.0
MAX_IMAGE_BYTES = 20 * 1024 * 1024
DEFAULT_BATCH_LIMIT = 10
MAX_BATCH_LIMIT = DEFAULT_BATCH_LIMIT
DEFAULT_OPTIMIZER_MODEL = "gpt-4o-mini"
DEFAULT_OPTIMIZER_STYLE = "手机日常原生感"
OPTIMIZER_STYLE_OPTIONS = (
    DEFAULT_OPTIMIZER_STYLE,
    "自拍专用极致真实",
    "电影级光影大片",
    "日系插画大师",
    "3D 潮玩盲盒",
    "自定义模式",
)
DEFAULT_DRAW_PENDING_MESSAGE = "🎨 收到灵感，正在绘制..."
DEFAULT_SELFIE_PENDING_MESSAGE = "ℹ️ 正在为「{persona_name}」生成自拍，请稍候..."
DEFAULT_DRAW_ERROR_MESSAGE = "💥 绘制失败: {error}"
DEFAULT_SELFIE_ERROR_MESSAGE = "💥 自拍生成失败: {error}"
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-image"

class APIType:
    """接口类型枚举"""
    OPENAI_IMAGE = "openai_image"
    OPENAI_CHAT = "openai_chat"  # 新增 Chat 解析出图类型
    GEMINI_OFFICIAL = "gemini_official"
    CUSTOM_ENDPOINT = "custom_endpoint"

class MessageEmoji:
    """消息表情符号"""
    ERROR = "❌"
    SUCCESS = "✅"
    WARNING = "⚠️"
    INFO = "ℹ️"
    PAINTING = "🎨"


# 以下常量由 main.py 迁入，供主模块与命令/任务/缓存 mixin 共享。
CACHE_DIR_NAMES = ("temp_images", "user_refs")
PLUGIN_RESULT_ERROR_MAX_LENGTH = 240
MAX_REFERENCE_IMAGES_PER_REQUEST = 14
MAX_REMOTE_REDIRECTS = 3
MAX_BACKGROUND_TASKS = 16
MAX_ACTIVE_BACKGROUND_TASKS = 4
CACHE_IMAGE_EXTENSIONS = frozenset({
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".bmp",
    ".avif",
    ".heic",
    ".heif",
    ".tif",
    ".tiff",
    ".jfif",
})
DEFAULT_CACHE_CLEANUP_INTERVAL_HOURS = 24
DEFAULT_MAX_CACHE_SIZE_MB = 512
LEGACY_COMMAND_PREFIXES = ("/", "!", "！", ".")
PRESET_VIEW_COMMANDS = ("查看预设",)
PRESET_LIST_COMMANDS = ("查看预设", "极速宏")
RESERVED_PRESET_NAMES = frozenset({
    "万象帮助",
    "查看预设",
    "极速宏",
    "添加预设",
    "删除预设",
    "清理缓存",
    "签到",
    "人设",
    "切换人设",
    "切换链路",
    "切换模型",
    "画",
    "自拍",
    "视频",
})
RESERVED_GENERATION_PARAM_KEYS = frozenset({
    "user_refs",
    "user_ref",
    "persona_refs",
    "persona_ref",
    "n",
    "stream",
    "messages",
    "input",
    "prompt",
    "tools",
})

# 配置/页面层常量（原 main.py，2026-11-16 迁入，main 与 main_config 共享）
PAGE_PREVIEW_IMAGE_BYTES = 80 * 1024 * 1024
MAX_PAGE_IMAGE_TOKENS = 4096
NATIVE_ACTIVE_PERSONA_FILE_PREFIX = "files/persona_config/persona_ref_image/"
MASKED_API_KEY_PREFIX = "••••"
MAX_CONFIG_REQUEST_BYTES = 128 * 1024 * 1024
MAX_PLUGIN_REQUEST_BYTES = 96 * 1024 * 1024
MAX_PROMPT_CHARS = 16_000
MAX_EXTRA_PARAMS_CHARS = 8_192
LEGACY_PERSONA_CONFIG_KEYS = (
    "persona_name",
    "persona_base_prompt",
    "persona_ref_image",
    "persona_ref_images",
)
LEGACY_PROVIDER_API_KEY_KEYS = ("API密钥",)
