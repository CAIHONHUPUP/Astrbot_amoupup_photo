"""
AstrBot「我能永远定格你」生图插件。

负责命令入口、LLM 工具、配置页面 API、图片缓存与后台视频任务生命周期。
"""
import asyncio
import copy
import os
import socket  # noqa: F401  test_gif_workflow 用 patch.object(main.socket, ...)
import threading
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import aiohttp

try:
    from astrbot.api.star import Context, Star, register
    from astrbot.api.event import AstrMessageEvent, filter
    from astrbot.api import logger
except ImportError:
    from astrbot.api.star import Context, Star, register
    from astrbot.api.event import AstrMessageEvent, filter
    from astrbot.api.utils import logger

try:
    from astrbot.api.event import EventMessageType
except ImportError:
    from astrbot.api.event.filter import EventMessageType

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:
    def get_astrbot_data_path() -> str:
        return os.path.join(os.getcwd(), "data")

from .constants import (  # noqa: F401  MAX_* 由 tests 通过 main 模块读取
    DEFAULT_DRAW_PENDING_MESSAGE,
    DEFAULT_SELFIE_PENDING_MESSAGE,
    LEGACY_PROVIDER_API_KEY_KEYS,
    MAX_ACTIVE_BACKGROUND_TASKS,
    MAX_BACKGROUND_TASKS,
    MAX_REFERENCE_IMAGES_PER_REQUEST,
    MessageEmoji,
    PRESET_VIEW_COMMANDS,
    RESERVED_PRESET_NAMES,
)
from .core.chain_manager import ChainManager
from .core.image_options import is_gif_request
from .core.negative_prompt import DEFAULT_BASE
from .core.parser import CommandParser
from .core.prompt_optimizer import action_names_outfit
from .models import PLUGIN_AUTHOR, PLUGIN_NAME, PLUGIN_VERSION
from .utils import handle_errors

try:
    from astrbot.api.message_components import File
except ImportError:
    try:
        from astrbot.api.event.components import File
    except ImportError:
        File = None

from .main_cache import OmniDrawCacheMixin
from .main_commands import OmniDrawCommandMixin
from .main_config import OmniDrawConfigMixin
from .main_tasks import OmniDrawTaskMixin

PRESET_ADD_COMMANDS = ("添加预设",)
PRESET_DELETE_COMMANDS = ("删除预设",)
#: 旧版本默认负面词（含“二次元, 插画”）；只在配置值仍是它时自动升级。
_LEGACY_BASE_NEGATIVE_DEFAULT = "人物, 人类, 文字, 水印, 签名, 二次元, 插画, 畸形, 多余的肢体, 模糊, 低画质"
CONFIG_KEYS = {
    "permission_config",
    "persona_config",
    "optimizer_config",
    "router_config",
    "presets",
    "providers",
    "video_providers",
    "usage_config",
    "cache_config",
    "reply_config",
    "image_generation",
    "verbose_report",
    "show_generation_time",
    "show_request_model",
    "persona_name",
    "persona_base_prompt",
    "persona_ref_image",
    "persona_ref_images",
}


#: “别人/他物持机”的说法：命中就放开全身构图，否则一律按自拍处理。
#: 只做正向的“持机动作”匹配，不解析否定句（“不要让朋友帮我拍”仍会命中“朋友帮”）；
#: 中文否定叠加嵌套从句时关键词无法正确切分，误判的代价只是构图多了点自由度。
#: 只收“持机动作”，不收“同框的人”（朋友/同伴/一起），否则“和朋友一起自拍”会被误判。
_EXTERNAL_CAMERA_HINTS = (
    "帮我拍", "帮忙拍", "帮拍", "帮我照", "帮忙照",
    "让他拍", "让她拍", "让人拍", "让别人拍", "让人帮", "找人拍",
    "请人拍", "请他拍", "请她拍",
    "路人帮", "路人拍",
    "朋友帮", "朋友给", "朋友拍", "同学帮", "同学给", "同学拍",
    "同伴帮", "同伴给", "同伴拍", "同事帮", "同事给", "同事拍",
    "家人帮", "家人给", "家人拍",
    "摄影师", "三脚架", "定时拍", "支架拍", "自拍杆架",
)


def _ref_key(ref):
    """参考图去重键：本地路径按 normcase+abspath 归一，URL/data URL 原样。"""
    text = str(ref or "").strip()
    if not text or text.startswith(("http://", "https://", "data:image")):
        return text
    return os.path.normcase(os.path.abspath(text))


@register(PLUGIN_NAME, PLUGIN_AUTHOR, f"我能永远定格你 v{PLUGIN_VERSION}", PLUGIN_VERSION)
class OmniDrawPlugin(OmniDrawConfigMixin, OmniDrawCommandMixin, OmniDrawTaskMixin, OmniDrawCacheMixin, Star):
    def __init__(self, context: Context, config: Optional[dict] = None):
        super().__init__(context)

        self.data_dir = self._resolve_data_dir()
        os.makedirs(self.data_dir, exist_ok=True)
        self.config_path = os.path.join(self.data_dir, "omnidraw_persist_config.json")
        self.usage_stats_path = os.path.join(self.data_dir, "omnidraw_usage_stats.json")
        self._usage_stats = self._load_usage_stats()
        self._quota_reservations: Dict[str, int] = {}
        self._background_tasks: set[asyncio.Task] = set()
        self._background_task_kinds: Dict[asyncio.Task, str] = {}
        self._background_semaphore: Optional[asyncio.Semaphore] = None
        self._background_semaphore_loop: Optional[asyncio.AbstractEventLoop] = None
        self._config_lock = threading.RLock()
        self._usage_lock = threading.RLock()
        self._cache_lease_lock = threading.RLock()
        self._cache_leases: Dict[str, int] = {}
        self._cache_cleanup_task: Optional[asyncio.Task] = None
        self._page_image_tokens: Dict[str, str] = {}
        self._page_image_lock = threading.RLock()
        self._page_image_secret = uuid.uuid4().hex
        try:
            self._event_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._event_loop = None
        self._native_config = config if hasattr(config, "save_config") else None
        self._native_config_path = str(getattr(config, "config_path", "") or "")
        self._native_config_stamp = self._file_stamp(self._native_config_path)
        self._native_config_mtime = self._get_mtime(self._native_config_path)
        self._native_config_signature = self._file_signature(self._native_config_path)
        self._persist_config_mtime = self._get_mtime(self.config_path)

        self.cmd_parser = CommandParser()
        self._apply_runtime_config(self._load_initial_config(config or {}))
        self._persist_config()
        self._safe_update_context_config()
        self._cleanup_persona_refs()

        self.context.register_web_api(
            f"/{PLUGIN_NAME}/get_config",
            self.get_config_handler,
            ["GET"],
            "获取我能永远定格你配置",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/get_page_config",
            self.get_page_config_handler,
            ["GET"],
            "获取我能永远定格你轻量页面配置",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/save_config",
            self.save_config_handler,
            ["POST"],
            "保存我能永远定格你配置",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/get_usage_stats",
            self.get_usage_stats_handler,
            ["GET"],
            "获取我能永远定格你当日生图统计",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/get_cache_stats",
            self.get_cache_stats_handler,
            ["GET"],
            "获取我能永远定格你图片缓存统计",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/clear_cache",
            self.clear_cache_handler,
            ["POST"],
            "清理我能永远定格你图片缓存",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/get_image",
            self.get_image_handler,
            ["GET"],
            "获取我能永远定格你本地参考图预览",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/generate_image_for_plugin",
            self.generate_image_for_plugin_handler,
            ["POST"],
            "为其他插件生成图片并返回结果",
        )

    def _clean_runtime_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(config, dict):
            return {}

        def strip_template_keys(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: strip_template_keys(item) for key, item in value.items() if key != "__template_key"}
            if isinstance(value, list):
                return [strip_template_keys(item) for item in value]
            return copy.deepcopy(value)

        cleaned = {key: strip_template_keys(value) for key, value in config.items() if key in CONFIG_KEYS}
        for section in ("providers", "video_providers"):
            providers = cleaned.get(section)
            if not isinstance(providers, list):
                continue
            for provider in providers:
                if not isinstance(provider, dict):
                    continue
                if "api_keys" not in provider:
                    for legacy_key in LEGACY_PROVIDER_API_KEY_KEYS:
                        if legacy_key in provider:
                            provider["api_keys"] = provider[legacy_key]
                            break
                for legacy_key in LEGACY_PROVIDER_API_KEY_KEYS:
                    provider.pop(legacy_key, None)

        presets = cleaned.get("presets")
        if isinstance(presets, list):
            filtered_presets = []
            for preset in presets:
                if isinstance(preset, dict):
                    preset_name = str(preset.get("name", "")).strip()
                elif isinstance(preset, str):
                    separator = ":" if ":" in preset else ("：" if "：" in preset else "")
                    preset_name = preset.split(separator, 1)[0].strip() if separator else ""
                else:
                    preset_name = ""
                if preset_name in RESERVED_PRESET_NAMES:
                    logger.warning(f"[我能永远定格你] 已忽略与内置指令同名的预设: {preset_name}")
                    continue
                filtered_presets.append(preset)
            cleaned["presets"] = filtered_presets
        self._upgrade_legacy_negative_prompt(cleaned)
        self._sync_native_active_persona_upload(cleaned)
        return cleaned

    def _upgrade_legacy_negative_prompt(self, config: Dict[str, Any]) -> None:
        """[2026-11-16] 旧默认负面词含“二次元, 插画”，和日系插画风格互相打架。

        只升级“值仍等于旧默认”的配置，用户自己改过的一律不动。
        """
        image_generation = config.get("image_generation")
        if not isinstance(image_generation, dict):
            return
        if str(image_generation.get("base_negative") or "").strip() != _LEGACY_BASE_NEGATIVE_DEFAULT:
            return
        image_generation["base_negative"] = DEFAULT_BASE
        logger.info("[我能永远定格你] 已把旧的「二次元/插画」默认负面词升级为当前默认值。")

    async def _send_gif_result(
        self, event: AstrMessageEvent, result: Any,
        elapsed_seconds: Optional[float] = None, include_metadata: bool = False,
    ) -> None:
        leased_paths: List[str] = []
        try:
            result = await self._materialize_gif_result(result, leased_paths=leased_paths)
            path = self._get_image_result_url(result)
            components = self._build_image_success_components(result, elapsed_seconds, include_metadata)
            try:
                await event.send(event.chain_result(components))
            except Exception:
                if File is None:
                    raise
                # Retry delivery, never generation: some adapters reject animated images.
                logger.warning("[我能永远定格你] GIF 图片发送失败，尝试以原始 GIF 文件发送。")
                await event.send(event.chain_result([File(name=os.path.basename(path), file=path)]))
        finally:
            self._release_cache_paths(leased_paths)

    async def _run_text2img_generation(
        self,
        event: Optional[AstrMessageEvent],
        prompt: str,
        count: int,
        aspect_ratio: str = "",
        size: str = "",
        extra_params: str = "",
        refs: Any = None,
        use_gif: bool = False,
        frame_count: int = 9,
        frame_duration_ms: int = 200,
        scene_lock: str = "",
        pose_lock: str = "",
        negative_prompt: str = "",
        allow_local_refs: bool = False,
    ) -> Dict[str, Any]:
        request_options = self._image_request_options(
            extra_params, aspect_ratio, size, use_gif, frame_count, frame_duration_ms,
        )
        gif_requested = is_gif_request(request_options)
        leased_result_paths: List[str] = []
        try:
            async with aiohttp.ClientSession() as session:
                raw_refs = (
                    self._as_plugin_image_refs(refs)
                    if refs is not None
                    else (
                        self._get_event_images(event, include_at_avatars=True)
                        if event is not None
                        else []
                    )
                )
                # [patched] 文本生图不锁定人物外貌。
                # 参考图在这里是素材，可能是一只猫、一栋楼、一件衣服，不一定是人物；
                # 无条件锁定会要求"发色/瞳色/服装/体型与参考图一致"，对非人物主体是错位的。
                # 需要锁定人物的是 generate_selfie——那条路径的画面里必定是角色本人。
                # The still-image optimizer enforces a frozen moment, which would erase GIF motion.
                optimized_actions = (
                    [prompt] * count if gif_requested
                    else await self.prompt_optimizer.optimize(
                        prompt, count, session=session,
                        scene_lock=scene_lock, pose_lock=pose_lock,
                    )
                )
                optimized_actions = self._normalize_plugin_prompts(optimized_actions, prompt, count)
                safe_refs = await self._process_and_save_images(
                    raw_refs, session=session, allow_local_refs=allow_local_refs,
                )
                leased_paths = self._lease_cache_paths(safe_refs)

                kwargs: Dict[str, Any] = {"user_refs": safe_refs} if safe_refs else {}
                negative_prompt = str(negative_prompt or "").strip() or self._negative_prompt_for(prompt)
                logger.info("[生图] 正向词：%s | 负向词：%s", prompt, negative_prompt or "（空）")
                kwargs["negative_prompt"] = negative_prompt
                kwargs.update(request_options)

                try:
                    chain_manager = ChainManager(self.plugin_config, session)
                    tasks = [
                        chain_manager.run_chain_with_metadata("text2img", optimized_action, **kwargs)
                        for optimized_action in optimized_actions
                    ]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    if gif_requested:
                        results = await asyncio.gather(
                            *(self._materialize_gif_result(result, session, leased_result_paths) for result in results),
                            return_exceptions=True,
                        )
                        self._prune_cache_if_needed("temp_images", protected_paths=leased_result_paths)
                finally:
                    self._release_cache_paths(leased_paths)
        except BaseException:
            self._release_cache_paths(leased_result_paths)
            raise

        return {
            "prompts": optimized_actions,
            "results": results,
            "requested_count": count,
            "raw_refs": raw_refs,
            "safe_refs": safe_refs,
            "mode": "text2img",
            "chain": "text2img",
            "leased_result_paths": leased_result_paths,
        }

    def _resolve_locked_outfit(self, action: str, skip_outfit_lock: bool = False) -> str:
        """B2c：action 点名了衣服时 OUTFIT LOCK 让位；没点名仍锁今日穿搭。"""
        locked_outfit = self._load_locked_outfit()
        if locked_outfit and (skip_outfit_lock or action_names_outfit(action)):
            locked_outfit = ""
        return locked_outfit

    async def _run_selfie_generation(
        self,
        event: Optional[AstrMessageEvent],
        action: str,
        count: int,
        aspect_ratio: str = "",
        size: str = "",
        extra_params: str = "",
        refs: Any = None,
        use_gif: bool = False,
        frame_count: int = 9,
        frame_duration_ms: int = 200,
        scene_lock: str = "",
        pose_lock: str = "",
        negative_prompt: str = "",
        allow_local_refs: bool = False,
        skip_outfit_lock: bool = False,
        pose_ref: Any = None,
    ) -> Dict[str, Any]:
        request_options = self._image_request_options(extra_params, aspect_ratio, size, use_gif, frame_count, frame_duration_ms)
        gif_requested = is_gif_request(request_options)
        leased_result_paths: List[str] = []
        try:
            async with aiohttp.ClientSession() as session:
                fallback_action = action or "看着镜头微笑"
                raw_refs, target_refs = self._selfie_target_refs(event, refs, pose_ref)
                _has_external_camera, _is_self_shot = self._selfie_camera_flags(fallback_action)
                # [patched] amo 当天锁定的穿搭；B2c：action 点名衣服时让位。
                locked_outfit = self._resolve_locked_outfit(fallback_action, skip_outfit_lock)
                optimized_actions = [fallback_action] * count if gif_requested else await self.prompt_optimizer.optimize(
                    fallback_action, count, session=session,
                    has_reference=bool(target_refs), selfie=_is_self_shot,
                    external_camera=_has_external_camera,
                    locked_outfit=locked_outfit,
                    scene_lock=scene_lock, pose_lock=pose_lock,
                )
                optimized_actions = self._normalize_plugin_prompts(optimized_actions, fallback_action, count)
                safe_refs = await self._process_and_save_images(
                    target_refs, session=session, allow_local_refs=allow_local_refs,
                )
                leased_paths = self._lease_cache_paths(safe_refs)

                try:
                    chain_to_use = "selfie" if self.plugin_config.chains.get("selfie") else "text2img"
                    chain_manager = ChainManager(self.plugin_config, session)
                    prompts, tasks = self._build_selfie_tasks(
                        chain_manager, chain_to_use, optimized_actions, safe_refs,
                        locked_outfit, negative_prompt, request_options)
                    results = await self._collect_selfie_results(
                        tasks, gif_requested, session, leased_result_paths)
                finally:
                    self._release_cache_paths(leased_paths)
        except BaseException:
            self._release_cache_paths(leased_result_paths)
            raise

        return {
            "prompts": prompts,
            "results": results,
            "requested_count": count,
            "raw_refs": raw_refs,
            "safe_refs": safe_refs,
            "mode": "selfie",
            "chain": chain_to_use,
            "leased_result_paths": leased_result_paths,
        }

    def _selfie_target_refs(self, event, refs, pose_ref):
        """姿势 → 人设三视图 → 本次消息的图；三条来源合并后统一截断。"""
        raw_refs = (
            self._as_plugin_image_refs(refs)
            if refs is not None
            else (
                self._get_event_images(event, include_at_avatars=True)
                if event is not None
                else []
            )
        )
        # [patched] 人设参考图与本次消息里的参考图合并，而不是互相替换
        _persona_refs = list(self.plugin_config.persona_ref_images or [])
        _extra_refs = list(raw_refs or [])
        # 姿势参考图优先：只学动作，必须排在三位人设图前面，
        # 否则 amo 的「姿势→三视图→房间→穿搭」会被改写成「三视图→姿势→...」。
        _pose_refs = self._as_plugin_image_refs(pose_ref) if pose_ref else []
        if _pose_refs:
            _pose_keys = {_ref_key(item) for item in _pose_refs}
            # refs 里可能也带了同一张姿势图（兼容旧版 amo/兜底），去重。
            # 本地路径要按 normcase+abspath 比，否则 D:/a.png 和 D:\\a.png 会算两张。
            _extra_refs = [item for item in _extra_refs if _ref_key(item) not in _pose_keys]
        # 姿势 → 人设三视图 → 本次消息的图（房间/穿搭，顺序由调用方给）。
        # 三条来源都要一起截断：只截合并分支的话，人设图本身超过上限时仍会在
        # _process_and_save_images 里直接抛 ValueError，整次生图失败。
        target_refs = (
            _pose_refs + _persona_refs + _extra_refs
        )[:MAX_REFERENCE_IMAGES_PER_REQUEST]
        if target_refs:
            logger.info(
                "[生图] 参考图数量=%d 顺序=%s",
                len(target_refs),
                " → ".join(os.path.basename(str(ref)) for ref in target_refs),
            )
        return raw_refs, target_refs

    def _build_selfie_tasks(self, chain_manager, chain_to_use, optimized_actions,
                            safe_refs, locked_outfit, negative_prompt, request_options):
        prompts = []
        tasks = []
        # safe_refs 是处理后的绝对路径。build_persona_prompt 会另外塞一个
        # persona_ref（只带人设首图），而 target_refs 的第一段本来就是人设图，
        # 首图已经在 user_refs 里了，留着 persona_ref 就是同一张图传两次。
        # 这里按规范化后的路径确认它确实存在才删，处理失败的极端情况仍保留兜底。
        _safe_ref_set = {os.path.abspath(str(ref)) for ref in safe_refs}
        for optimized_action in optimized_actions:
            final_prompt, kwargs = self.persona_manager.build_persona_prompt(optimized_action)
            if locked_outfit:
                # 最后再钉一遍：副脑万一还是写了别的衣服，图像模型以这句为准
                final_prompt = (
                    final_prompt.rstrip(", ")
                    + ", she is wearing exactly: " + locked_outfit
                    + "; today's outfit is fixed and must not be changed or replaced"
                )
            if safe_refs:
                kwargs["user_refs"] = safe_refs
                _persona_first = kwargs.get("persona_ref")
                if (
                    _persona_first
                    # URL 形态不参与本地路径比较（provider 侧自行处理）
                    and not str(_persona_first).startswith(("http://", "https://"))
                    and os.path.abspath(str(_persona_first)) in _safe_ref_set
                ):
                    kwargs.pop("persona_ref", None)
            negative_prompt = str(negative_prompt or "").strip() or self._negative_prompt_for(final_prompt, "selfie")
            logger.info("[生图] 正向词：%s | 负向词：%s", final_prompt, negative_prompt or "（空）")
            kwargs["negative_prompt"] = negative_prompt
            kwargs.update(request_options)
            prompts.append(final_prompt)
            tasks.append(chain_manager.run_chain_with_metadata(chain_to_use, final_prompt, **kwargs))
        return prompts, tasks

    async def _collect_selfie_results(self, tasks, gif_requested, session, leased_result_paths):
        results = await asyncio.gather(*tasks, return_exceptions=True)
        if gif_requested:
            results = await asyncio.gather(
                *(self._materialize_gif_result(result, session, leased_result_paths) for result in results),
                return_exceptions=True,
            )
            self._prune_cache_if_needed("temp_images", protected_paths=leased_result_paths)
        return results

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("添加预设", prefix_optional=True)
    @handle_errors
    async def cmd_add_preset(
        self,
        event: AstrMessageEvent,
        p1: str = "",
        p2: str = "",
        p3: str = "",
        p4: str = "",
        p5: str = "",
        p6: str = "",
        p7: str = "",
        p8: str = "",
        p9: str = "",
        p10: str = "",
    ) -> AsyncGenerator[Any, None]:
        fallback = " ".join(str(item) for item in [p1, p2, p3, p4, p5, p6, p7, p8, p9, p10] if item).strip()
        payload = self._extract_command_message_any(event, PRESET_ADD_COMMANDS, fallback)
        preset_name, preset_prompt = self._parse_preset_add_payload(payload)
        name_error = self._validate_preset_name(preset_name)
        if name_error:
            yield event.plain_result(f"{MessageEmoji.WARNING} {name_error}\n用法: /添加预设 [预设名] [提示词]")
            return
        if not preset_prompt:
            yield event.plain_result(f"{MessageEmoji.WARNING} 缺少提示词。\n用法: /添加预设 [预设名] [提示词]")
            return

        existed = await self._run_config_operation(self._upsert_preset, preset_name, preset_prompt)
        action = "已更新" if existed else "已添加"
        yield event.plain_result(f"{MessageEmoji.SUCCESS} {action}预设「{preset_name}」。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("删除预设", prefix_optional=True)
    @handle_errors
    async def cmd_delete_preset(
        self,
        event: AstrMessageEvent,
        p1: str = "",
        p2: str = "",
        p3: str = "",
        p4: str = "",
        p5: str = "",
    ) -> AsyncGenerator[Any, None]:
        fallback = " ".join(str(item) for item in [p1, p2, p3, p4, p5] if item).strip()
        selector = self._extract_command_message_any(event, PRESET_DELETE_COMMANDS, fallback)
        if not selector:
            yield event.plain_result(f"{MessageEmoji.WARNING} 缺少预设名。\n用法: /删除预设 [预设名]")
            return

        deleted_name = await self._run_config_operation(self._delete_preset, selector)
        if not deleted_name:
            yield event.plain_result(f"{MessageEmoji.WARNING} 找不到预设: {selector}\n可发送 /查看预设 查看所有预设名。")
            return
        yield event.plain_result(f"{MessageEmoji.SUCCESS} 已删除预设「{deleted_name}」。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("切换人设")
    @handle_errors
    async def cmd_switch_persona(
        self,
        event: AstrMessageEvent,
        p1: str = "",
        p2: str = "",
        p3: str = "",
        p4: str = "",
        p5: str = "",
    ) -> AsyncGenerator[Any, None]:
        permission_error = self._permission_denied_message(event)
        if permission_error:
            yield event.plain_result(permission_error)
            return

        fallback = " ".join(str(item) for item in [p1, p2, p3, p4, p5] if item).strip()
        selector = self._extract_command_message(event, "切换人设", fallback)
        if not selector:
            yield event.plain_result(f"{MessageEmoji.WARNING} 缺少人设。用法: /切换人设 [序号/ID/名称]\n可先发送 /人设 查看列表。")
            return

        selected = await self._run_config_operation(self._set_active_persona, selector)
        if not selected:
            yield event.plain_result(f"{MessageEmoji.WARNING} 找不到人设: {selector}\n可先发送 /人设 查看列表。")
            return

        persona_name, reference_count = selected
        yield event.plain_result(
            f"{MessageEmoji.SUCCESS} 已切换至人设「{persona_name}」，"
            f"自拍将使用该人设的 {reference_count} 张参考图。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("切换链路")
    @handle_errors
    async def cmd_switch_chain(
        self,
        event: AstrMessageEvent,
        target: str = "",
        node_id: str = "",
    ) -> AsyncGenerator[Any, None]:
        permission_error = self._permission_denied_message(event)
        if permission_error:
            yield event.plain_result(permission_error)
            return

        target_map = {"画图": "text2img", "自拍": "selfie", "视频": "video", "副脑": "optimizer"}
        if target not in target_map:
            yield event.plain_result(f"{MessageEmoji.WARNING} 未知目标！支持: 画图/自拍/视频/副脑")
            return
        if not node_id:
            yield event.plain_result(f"{MessageEmoji.WARNING} 缺少节点 ID。用法: /切换链路 [目标] [节点ID]")
            return

        chain_key = target_map[target]
        changed = await self._run_config_operation(self._set_chain_config, chain_key, node_id)
        if not changed:
            yield event.plain_result(f"{MessageEmoji.WARNING} 找不到节点 ID: {node_id}")
            return

        yield event.plain_result(f"{MessageEmoji.SUCCESS} 已将 {target} 链路切换至节点: {node_id}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("切换模型")
    @handle_errors
    async def cmd_switch_model(
        self,
        event: AstrMessageEvent,
        target: str = "",
        model_idx: str = "",
    ) -> AsyncGenerator[Any, None]:
        permission_error = self._permission_denied_message(event)
        if permission_error:
            yield event.plain_result(permission_error)
            return

        target_map = {"画图": "text2img", "自拍": "selfie", "视频": "video"}
        if target and target not in target_map and not model_idx:
            model_idx = target
            target = "画图"
        if not target:
            target = "画图"
        if target not in target_map:
            yield event.plain_result(f"{MessageEmoji.WARNING} 未知目标！支持: 画图/自拍/视频")
            return

        chain_key = target_map[target]
        message = await self._run_config_operation(self._switch_model_config, chain_key, target, model_idx)
        yield event.plain_result(message)

    @filter.event_message_type(EventMessageType.ALL, priority=5)
    async def on_message_preset(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        text = self._get_event_text(event)
        allow_bare_command = self._event_is_at_or_wake_command(event)

        compact_view_selector = self._extract_compact_command_payload_any(
            text,
            PRESET_VIEW_COMMANDS,
            allow_bare=allow_bare_command,
        )
        if compact_view_selector:
            permission_error = self._permission_denied_message(event)
            if permission_error:
                yield event.plain_result(permission_error)
                self._stop_event_if_possible(event)
                return
            yield event.plain_result(self._build_preset_view_message(compact_view_selector))
            self._stop_event_if_possible(event)
            return

        if not self.plugin_config.presets:
            return
        cmd_name, extra_rules = self._parse_preset_trigger(text, allow_bare=allow_bare_command)
        if not cmd_name:
            return
        permission_error = self._permission_denied_message(event)
        if permission_error:
            yield event.plain_result(permission_error)
            self._stop_event_if_possible(event)
            return
        quota_error, reserved_count = self._reserve_image_quota(event, 1)
        if quota_error:
            yield event.plain_result(quota_error)
            self._stop_event_if_possible(event)
            return

        try:
            started_at = time.perf_counter()
            async with aiohttp.ClientSession() as session:
                raw_refs = self._get_event_images(event, include_at_avatars=True, at_after_command=cmd_name)
                preset_prompt = self.plugin_config.presets[cmd_name]
                safe_refs = await self._process_and_save_images(raw_refs, session=session)
                base_prompt, kwargs = self.cmd_parser.parse(preset_prompt)
                extra_prompt, extra_kwargs = self.cmd_parser.parse(extra_rules)
                kwargs.update(extra_kwargs)
                kwargs = self._sanitize_generation_kwargs(kwargs)
                prompt = self._build_preset_generation_prompt(base_prompt, extra_prompt)
                param_count = len(kwargs)
                if safe_refs:
                    kwargs["user_refs"] = safe_refs
                leased_paths = self._lease_cache_paths(safe_refs)

                msg = self._format_pending_message(
                    self.plugin_config.draw_pending_message,
                    DEFAULT_DRAW_PENDING_MESSAGE,
                    command=cmd_name,
                    prompt=prompt,
                    ref_count=len(safe_refs),
                    param_count=param_count,
                    persona_name=self.plugin_config.persona_name,
                )
                if self.plugin_config.verbose_report:
                    msg += f"\n📝 宏对应提示词: {prompt}\n⚙️ 附加参数：{param_count} 个"
                    if extra_prompt:
                        msg += f"\n➕ 追加规则：{extra_prompt}"
                    msg += f"\n🖼️ 实际参考图：{len(safe_refs)} 张"
                pending_result = await self._send_plain_message(event, msg)
                if pending_result is not None:
                    yield pending_result

                try:
                    chain_manager = ChainManager(self.plugin_config, session)
                    result = await chain_manager.run_chain_with_metadata("text2img", prompt, **kwargs)
                finally:
                    self._release_cache_paths(leased_paths)
            delivery = await self._command_image_delivery(
                event, result, time.perf_counter() - started_at, is_gif_request(kwargs),
            )
            self._complete_image_quota(event, reserved_count, 1)
            if delivery is not None:
                yield delivery
            self._stop_event_if_possible(event)
        except asyncio.CancelledError:
            self._release_image_quota(event, reserved_count)
            raise
        except Exception as exc:
            self._release_image_quota(event, reserved_count)
            yield event.plain_result(
                self._build_command_error_message("on_message_preset", exc)
                or f"💥 绘制失败: {self._safe_plugin_error_message(exc)}"
            )
            self._stop_event_if_possible(event)

    async def _run_draw_command(
        self, event: AstrMessageEvent, command: str, fallback: str, use_gif: bool = False,
    ) -> AsyncGenerator[Any, None]:
        permission_error = self._permission_denied_message(event)
        if permission_error:
            yield event.plain_result(permission_error)
            return
        quota_error, reserved_count = self._reserve_image_quota(event, 1)
        if quota_error:
            yield event.plain_result(quota_error)
            return

        message = self._extract_command_message(event, command, fallback)
        raw_refs = self._get_event_images(event, include_at_avatars=True, at_after_command=command)
        if not message and not raw_refs:
            self._release_image_quota(event, reserved_count)
            yield event.plain_result(f"{MessageEmoji.WARNING} 请输入提示词或附带参考图。")
            return

        try:
            started_at = time.perf_counter()
            async with aiohttp.ClientSession() as session:
                safe_refs = await self._process_and_save_images(raw_refs, session=session)
                prompt, kwargs = self.cmd_parser.parse(message)
                if use_gif:
                    kwargs["output_format"] = "gif"
                kwargs = self._sanitize_generation_kwargs(kwargs)
                if not prompt and safe_refs:
                    prompt = (
                        "根据参考图生成连贯的 GIF 动图，保持主体一致并呈现自然动作。"
                        if is_gif_request(kwargs) else "根据参考图生成一张自然、清晰、符合原图语义的图片。"
                    )
                param_count = len(kwargs)
                if safe_refs:
                    kwargs["user_refs"] = safe_refs
                leased_paths = self._lease_cache_paths(safe_refs)

                msg = self._format_pending_message(
                    self.plugin_config.draw_pending_message,
                    DEFAULT_DRAW_PENDING_MESSAGE,
                    command=command,
                    prompt=prompt,
                    ref_count=len(safe_refs),
                    param_count=param_count,
                    persona_name=self.plugin_config.persona_name,
                )
                if self.plugin_config.verbose_report:
                    msg += f"\n📝 最终提示词: {prompt}\n⚙️ 附加参数：{param_count} 个\n🖼️ 实际参考图：{len(safe_refs)} 张"
                pending_result = await self._send_plain_message(event, msg)
                if pending_result is not None:
                    yield pending_result

                try:
                    chain_manager = ChainManager(self.plugin_config, session)
                    result = await chain_manager.run_chain_with_metadata("text2img", prompt, **kwargs)
                finally:
                    self._release_cache_paths(leased_paths)
            delivery = await self._command_image_delivery(
                event, result, time.perf_counter() - started_at, is_gif_request(kwargs),
            )
            self._complete_image_quota(event, reserved_count, 1)
            if delivery is not None:
                yield delivery
        except asyncio.CancelledError:
            self._release_image_quota(event, reserved_count)
            raise
        except Exception:
            self._release_image_quota(event, reserved_count)
            raise

    def _selfie_camera_flags(self, action: str) -> Tuple[bool, bool]:
        """[patched] 按“谁在持机”决定构图自由度：返回 (has_external_camera, is_self_shot)。"""
        text = str(action or "")
        has_external_camera = any(hint in text for hint in _EXTERNAL_CAMERA_HINTS)
        return has_external_camera, not has_external_camera

    @filter.command("自拍")
    @handle_errors
    async def cmd_selfie(
        self,
        event: AstrMessageEvent,
        p1: str = "",
        p2: str = "",
        p3: str = "",
        p4: str = "",
        p5: str = "",
        p6: str = "",
        p7: str = "",
        p8: str = "",
        p9: str = "",
        p10: str = "",
    ) -> AsyncGenerator[Any, None]:
        permission_error = self._permission_denied_message(event)
        if permission_error:
            yield event.plain_result(permission_error)
            return
        quota_error, reserved_count = self._reserve_image_quota(event, 1)
        if quota_error:
            yield event.plain_result(quota_error)
            return

        fallback = " ".join(str(item) for item in [p1, p2, p3, p4, p5, p6, p7, p8, p9, p10] if item).strip()
        message = self._extract_command_message(event, "自拍", fallback)
        try:
            user_input, kwargs = self.cmd_parser.parse(message)
            kwargs = self._sanitize_generation_kwargs(kwargs)
            user_input = user_input or "看着镜头微笑"
            started_at = time.perf_counter()
            async with aiohttp.ClientSession() as session:
                # [patched] 命令自拍与 amo 自拍链路走同一套 optimize 参数：先把参考图定下来，
                # 才能告诉副脑“有参考图/是不是自拍”，并按 amo 当天穿搭锁衣服。
                raw_refs = self._get_event_images(event)
                target_refs = raw_refs if raw_refs else self.plugin_config.persona_ref_images
                _has_external_camera, _is_self_shot = self._selfie_camera_flags(user_input)
                locked_outfit = self._resolve_locked_outfit(user_input)
                optimized_actions = (
                    [user_input] if is_gif_request(kwargs)
                    else await self.prompt_optimizer.optimize(
                        user_input, count=1, session=session,
                        has_reference=bool(target_refs), selfie=_is_self_shot,
                        external_camera=_has_external_camera,
                        locked_outfit=locked_outfit,
                    )
                )
                final_prompt, extra_kwargs = self.persona_manager.build_persona_prompt(
                    optimized_actions[0] if optimized_actions else user_input
                )
                if locked_outfit:
                    # 与 _run_selfie_generation 一致：副脑写了别的衣服，也以这句为准
                    final_prompt = (
                        final_prompt.rstrip(", ")
                        + ", she is wearing exactly: " + locked_outfit
                        + "; today's outfit is fixed and must not be changed or replaced"
                    )
                extra_kwargs.update(kwargs)

                safe_refs = await self._process_and_save_images(target_refs, session=session)
                if safe_refs:
                    extra_kwargs["user_refs"] = safe_refs
                    if not raw_refs:
                        extra_kwargs.pop("persona_ref", None)
                leased_paths = self._lease_cache_paths(safe_refs)

                msg = self._format_pending_message(
                    self.plugin_config.selfie_pending_message,
                    DEFAULT_SELFIE_PENDING_MESSAGE,
                    command="自拍",
                    prompt=final_prompt,
                    user_input=user_input,
                    ref_count=len(safe_refs),
                    param_count=len(kwargs),
                    persona_name=self.plugin_config.persona_name,
                )
                if self.plugin_config.verbose_report:
                    msg += f"\n📝 构建提示词: {final_prompt}\n⚙️ 附加参数：{len(kwargs)} 个\n🖼️ 实际参考图：{len(safe_refs)} 张"
                pending_result = await self._send_plain_message(event, msg)
                if pending_result is not None:
                    yield pending_result

                try:
                    chain_to_use = "selfie" if self.plugin_config.chains.get("selfie") else "text2img"
                    chain_manager = ChainManager(self.plugin_config, session)
                    result = await chain_manager.run_chain_with_metadata(chain_to_use, final_prompt, **extra_kwargs)
                finally:
                    self._release_cache_paths(leased_paths)
            delivery = await self._command_image_delivery(
                event, result, time.perf_counter() - started_at, is_gif_request(kwargs),
            )
            self._complete_image_quota(event, reserved_count, 1)
            if delivery is not None:
                yield delivery
        except asyncio.CancelledError:
            self._release_image_quota(event, reserved_count)
            raise
        except Exception:
            self._release_image_quota(event, reserved_count)
            raise

# handler 装饰器在 mixin 里注册：AstrBot 只认 handler_module_path == 插件主模块，
# 导入完成后统一归一到本模块（2026-11-16 拆分时保留）。
_ALIGNED_MIXIN_MODULES = frozenset({
    "main_commands",
    "main_tasks",
    "main_cache",
    "main_config",
})


def _align_handler_module_paths() -> int:
    try:
        from astrbot.core.star.star_handler import star_handlers_registry
    except Exception:
        return 0
    target = __name__
    fixed = 0
    for metadata in list(star_handlers_registry):
        path = str(getattr(metadata, "handler_module_path", "") or "")
        if path.rsplit(".", 1)[-1] not in _ALIGNED_MIXIN_MODULES:
            continue
        old_full = str(getattr(metadata, "handler_full_name", "") or "")
        metadata.handler_module_path = target
        new_full = target + "_" + str(getattr(metadata, "handler_name", "") or "")
        metadata.handler_full_name = new_full
        mapping = getattr(star_handlers_registry, "star_handlers_map", None)
        if isinstance(mapping, dict) and old_full in mapping:
            mapping[new_full] = mapping.pop(old_full)
        handler = getattr(metadata, "handler", None)
        if handler is not None:
            try:
                handler.__module__ = target
            except Exception:  # silent: 修正 handler 模块名是尽力而为，失败不影响插件注册
                pass
        fixed += 1
    return fixed


_align_handler_module_paths()
