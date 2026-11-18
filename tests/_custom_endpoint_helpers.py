# -*- coding: utf-8 -*-
"""custom_endpoint 回归套件的共享 fixtures（从 3273 行大文件原样抽出）。

包含 astrbot/quart 假模块、临时插件目录、假 HTTP/provider 工厂与常用别名。
"""
import base64
import importlib
import json
import sys
import types
from pathlib import Path


PLUGIN_DIR = Path(__file__).resolve().parents[1]
PACKAGE_NAME = PLUGIN_DIR.name
PACKAGE_PARENT = PLUGIN_DIR.parent
sys.path.insert(0, str(PACKAGE_PARENT))

astrbot_module = types.ModuleType("astrbot")
astrbot_api_module = types.ModuleType("astrbot.api")
astrbot_event_module = types.ModuleType("astrbot.api.event")
astrbot_event_components_module = types.ModuleType("astrbot.api.event.components")
astrbot_message_components_module = types.ModuleType("astrbot.api.message_components")
astrbot_star_module = types.ModuleType("astrbot.api.star")
quart_module = types.ModuleType("quart")


class _Logger:
    def __init__(self):
        self.messages = []

    def info(self, *args, **kwargs):
        self.messages.append(("info", " ".join(str(arg) for arg in args)))

    def warning(self, *args, **kwargs):
        self.messages.append(("warning", " ".join(str(arg) for arg in args)))

    def error(self, *args, **kwargs):
        self.messages.append(("error", " ".join(str(arg) for arg in args)))


fake_logger = _Logger()
setattr(astrbot_api_module, "logger", fake_logger)
setattr(astrbot_event_module, "AstrMessageEvent", object)


class _Plain:
    def __init__(self, text=""):
        self.text = text


class _At:
    def __init__(self, qq=""):
        self.qq = qq


class _Video:
    @classmethod
    def fromURL(cls, url):
        return {"type": "video", "url": url}


class _Image:
    @classmethod
    def fromFileSystem(cls, path):
        return {"type": "image_file", "path": path}

    @classmethod
    def fromURL(cls, url):
        return {"type": "image_url", "url": url}


class _Filter:
    class PermissionType:
        ADMIN = "admin"

    def command(self, *args, **kwargs):
        return lambda func: func

    def permission_type(self, *args, **kwargs):
        return lambda func: func

    def event_message_type(self, *args, **kwargs):
        return lambda func: func


class _Star:
    def __init__(self, context=None):
        self.context = context


def _identity_decorator(*args, **kwargs):
    return lambda item: item


def _jsonify(*args, **kwargs):
    return {"args": args, "kwargs": kwargs}


async def _send_file(*args, **kwargs):
    return {"args": args, "kwargs": kwargs}


setattr(astrbot_message_components_module, "Plain", _Plain)
setattr(astrbot_message_components_module, "Image", _Image)
setattr(astrbot_message_components_module, "At", _At)
setattr(astrbot_message_components_module, "Video", _Video)
setattr(astrbot_event_components_module, "Plain", _Plain)
setattr(astrbot_event_components_module, "Image", _Image)
setattr(astrbot_event_components_module, "At", _At)
setattr(astrbot_event_module, "filter", _Filter())
setattr(astrbot_event_module, "EventMessageType", types.SimpleNamespace(ALL="all"))
setattr(astrbot_api_module, "llm_tool", _identity_decorator)
setattr(astrbot_star_module, "Context", object)
setattr(astrbot_star_module, "Star", _Star)
setattr(astrbot_star_module, "register", _identity_decorator)
setattr(quart_module, "jsonify", _jsonify)
setattr(quart_module, "request", types.SimpleNamespace(get_json=lambda *args, **kwargs: {}))
setattr(quart_module, "send_file", _send_file)
sys.modules.setdefault("astrbot", astrbot_module)
sys.modules.setdefault("astrbot.api", astrbot_api_module)
sys.modules.setdefault("astrbot.api.event", astrbot_event_module)
sys.modules.setdefault("astrbot.api.event.components", astrbot_event_components_module)
sys.modules.setdefault("astrbot.api.message_components", astrbot_message_components_module)
sys.modules.setdefault("astrbot.api.star", astrbot_star_module)
sys.modules.setdefault("quart", quart_module)

models_module = importlib.import_module(f"{PACKAGE_NAME}.models")
base_module = importlib.import_module(f"{PACKAGE_NAME}.providers.base")
custom_endpoint_module = importlib.import_module(f"{PACKAGE_NAME}.providers.custom_endpoint_impl")
gemini_official_module = importlib.import_module(f"{PACKAGE_NAME}.providers.gemini_official_impl")
openai_impl_module = importlib.import_module(f"{PACKAGE_NAME}.providers.openai_impl")
openai_chat_module = importlib.import_module(f"{PACKAGE_NAME}.providers.openai_chat_impl")
provider_factory_module = importlib.import_module(f"{PACKAGE_NAME}.providers")
chain_manager_module = importlib.import_module(f"{PACKAGE_NAME}.core.chain_manager")
prompt_optimizer_module = importlib.import_module(f"{PACKAGE_NAME}.core.prompt_optimizer")
video_manager_module = importlib.import_module(f"{PACKAGE_NAME}.core.video_manager")
main_module = importlib.import_module(f"{PACKAGE_NAME}.main")
main_config = importlib.import_module(f"{PACKAGE_NAME}.main_config")

ProviderConfig = models_module.ProviderConfig
PluginConfig = models_module.PluginConfig
_normalize_api_type = models_module._normalize_api_type
ChainRunResult = chain_manager_module.ChainRunResult
ChainManager = chain_manager_module.ChainManager
PromptOptimizer = prompt_optimizer_module.PromptOptimizer
OmniDrawPlugin = main_module.OmniDrawPlugin
VideoManager = video_manager_module.VideoManager
VideoTaskError = video_manager_module.VideoTaskError
extract_error_message = base_module.extract_error_message
extract_image_url_from_response = base_module.extract_image_url_from_response
is_complete_endpoint_url = base_module.is_complete_endpoint_url
summarize_payload_for_log = base_module.summarize_payload_for_log
summarize_text_for_log = base_module.summarize_text_for_log
summarize_url_for_log = base_module.summarize_url_for_log
CustomEndpointProvider = custom_endpoint_module.CustomEndpointProvider
GeminiOfficialProvider = gemini_official_module.GeminiOfficialProvider
OpenAIProvider = openai_impl_module.OpenAIProvider
OpenAIChatProvider = openai_chat_module.OpenAIChatProvider


def _long_b64() -> str:
    return base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"image-bytes" * 20).decode("ascii")


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    async def text(self):
        return self.payload if isinstance(self.payload, str) else json.dumps(self.payload)

    async def json(self):
        if isinstance(self.payload, str):
            return json.loads(self.payload)
        return self.payload


class FakePost:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.posts = []
        self.gets = []

    def post(self, url, **kwargs):
        self.posts.append({"url": url, **kwargs})
        return FakePost(self.response)

    def get(self, url, **kwargs):
        self.gets.append({"url": url, **kwargs})
        return FakePost(self.response)

class FakeClientSession:
    def __init__(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

