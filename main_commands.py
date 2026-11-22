"""「我能永远定格你」命令层 mixin。"""

import asyncio
import copy
import json
import os
import random
import re
import time
from typing import Any, AsyncGenerator, Callable, Dict, Iterable, List, Optional, Tuple, TYPE_CHECKING
import aiohttp

try:
    from astrbot.api.event import AstrMessageEvent, filter
    from astrbot.api.message_components import Plain
    from astrbot.api import llm_tool, logger
except ImportError:
    from astrbot.api.event import AstrMessageEvent, filter
    from astrbot.api.event.components import Plain
    from astrbot.api import llm_tool
    from astrbot.api.utils import logger

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:
    def get_astrbot_data_path() -> str:
        return os.path.join(os.getcwd(), "data")
if TYPE_CHECKING:
    from .constants import (
        DEFAULT_BATCH_LIMIT,
        DEFAULT_DRAW_ERROR_MESSAGE,
        DEFAULT_SELFIE_ERROR_MESSAGE,
        LEGACY_COMMAND_PREFIXES,
        MessageEmoji,
        PRESET_LIST_COMMANDS,
        PRESET_VIEW_COMMANDS,
        RESERVED_GENERATION_PARAM_KEYS,
        RESERVED_PRESET_NAMES,
    )
    from .core.image_options import normalize_gif_options
    from .models import PLUGIN_VERSION
    from .utils import handle_errors
else:
    try:
        from .constants import (
            DEFAULT_BATCH_LIMIT,
            DEFAULT_DRAW_ERROR_MESSAGE,
            DEFAULT_SELFIE_ERROR_MESSAGE,
            LEGACY_COMMAND_PREFIXES,
            MessageEmoji,
            PRESET_LIST_COMMANDS,
            PRESET_VIEW_COMMANDS,
            RESERVED_GENERATION_PARAM_KEYS,
            RESERVED_PRESET_NAMES,
        )
        from .core.image_options import normalize_gif_options
        from .models import PLUGIN_VERSION
        from .utils import handle_errors
    except ImportError:
        from constants import (
            DEFAULT_BATCH_LIMIT,
            DEFAULT_DRAW_ERROR_MESSAGE,
            DEFAULT_SELFIE_ERROR_MESSAGE,
            LEGACY_COMMAND_PREFIXES,
            MessageEmoji,
            PRESET_LIST_COMMANDS,
            PRESET_VIEW_COMMANDS,
            RESERVED_GENERATION_PARAM_KEYS,
            RESERVED_PRESET_NAMES,
        )
        from core.image_options import normalize_gif_options
        from models import PLUGIN_VERSION
        from utils import handle_errors


if TYPE_CHECKING:
    from .core.parser import CommandParser
    from .core.video_manager import VideoManager
    from .models import PluginConfig


#: 跨插件生图 principal 契约（task-27，photo 侧）
#: 能陪/守护调用 generate_images_for_plugin 时必须带 principal；event 为空时也校验：
#: - caller 必须在受信插件名单内（本进程内跨插件调用，防的是误调用/坏形状）；
#: - kind=owner 走与命令路径同一套权限+每日配额+用量记账；
#: - kind=system 是系统内部生成（参考图/房间图），不占个人配额、不计“她的今日生图”。
PLUGIN_PRINCIPAL_CONTRACT = "amo-image-principal"
PLUGIN_PRINCIPAL_VERSION = 1
PLUGIN_PRINCIPAL_KINDS = ("owner", "system")
TRUSTED_PLUGIN_CALLERS = (
    "Astrbot_amoupup_forever",
    "Astrbot_amoupup_watch",
    "Astrbot_amoupup_data",
)
#: 权限失败时的“能说出口”的话：不出现权限/配额/配额不足/event 等工程词
PLUGIN_PRINCIPAL_SAFE_DENY = "这个只给老公看哦。"


class OmniDrawCommandMixin:
    """命令层：指令入口、权限与配额判定、命令文案与预设。"""

    plugin_config: "PluginConfig"
    raw_config: Dict[str, Any]
    cmd_parser: "CommandParser"
    video_manager: "VideoManager"
    _config_lock: Any
    _usage_lock: Any
    _usage_stats: Dict[str, Any]
    _quota_reservations: Dict[str, int]
    _to_nonnegative_int: Callable[..., int]
    _create_background_task: Callable[..., asyncio.Task]
    _release_cache_paths: Callable[..., None]
    _clear_cache_images: Callable[..., Dict[str, Any]]
    _persist_usage_stats: Callable[..., None]
    _normalize_usage_stats: Callable[..., Dict[str, Any]]
    _current_usage_stats: Callable[..., Dict[str, Any]]
    _run_draw_command: Callable[..., Any]
    _get_event_images: Callable[..., Any]
    _process_and_save_images: Callable[..., Any]
    _unwrap_tool_event: Callable[..., Any]
    _plugin_bool: Callable[..., bool]
    generate_images_for_plugin: Callable[..., Any]
    _get_image_result_url: Callable[..., Any]
    _generation_failure_message: Callable[..., Any]
    _send_generated_images: Callable[..., Any]
    _safe_plugin_error_message: Callable[..., Any]
    _load_json_file: Callable[..., Dict[str, Any]]
    _refresh_from_native_config_if_changed: Callable[..., None]
    _save_page_config: Callable[..., Any]
    _run_selfie_generation: Callable[..., Any]
    _run_text2img_generation: Callable[..., Any]

    @filter.command("万象帮助", alias={"定格帮助"})
    @handle_errors
    async def cmd_help(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        prefix = self._display_command_prefix()

        def cmd(name: str) -> str:
            return f"{prefix}{name}" if prefix else name

        msg = (
            f"📖 我能永远定格你 v{PLUGIN_VERSION}\n"
            f"{cmd('画')} [提示词] [--参数 值]\n"
            f"{cmd('gif')} [动作描述] [--frame_count 9 --frame_duration_ms 200]\n"
            f"{cmd('自拍')} [动作] [--参数 值]\n"
            f"{cmd('视频')} [提示词] [--参数 值]\n"
            f"{cmd('人设')}\n"
            f"{cmd('切换人设')} [序号/ID/名称]\n"
            f"{cmd('切换链路')} [画图/自拍/视频/副脑] [节点ID]\n"
            f"{cmd('切换模型')} [画图/自拍/视频] [序号或名称]\n"
            f"{cmd('签到')}\n"
            f"{cmd('清理缓存')}\n"
            f"{cmd('查看预设')} [预设名]\n"
            f"{cmd('添加预设')} [预设名] [提示词]\n"
            f"{cmd('删除预设')} [预设名]\n"
            f"{cmd('定格帮助')}（旧名 {cmd('万象帮助')}）\n\n"
            "GIF：使用 gpt-image-2.6-sunburst / flare 且开通 GIF 预览权限的 Images 节点。\n"
            "帧数 2–16，帧间隔 20–2000ms，默认 size=auto；自拍可加 --output_format gif。\n\n"
        )
        if self.plugin_config.presets:
            msg += "✨ 预设:\n" + "\n".join([cmd(str(preset)) for preset in self.plugin_config.presets.keys()])
        yield event.plain_result(msg)

    @filter.command("查看预设", prefix_optional=True)
    @handle_errors
    async def cmd_view_presets(
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
        selector = self._extract_command_message_any(event, PRESET_VIEW_COMMANDS + PRESET_LIST_COMMANDS, fallback)
        yield event.plain_result(self._build_preset_view_message(selector))

    @filter.command("极速宏", prefix_optional=True)
    @handle_errors
    async def cmd_fast_preset_list(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        permission_error = self._permission_denied_message(event)
        if permission_error:
            yield event.plain_result(permission_error)
            return
        yield event.plain_result(self._build_fast_preset_list_message())

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("清理缓存")
    @handle_errors
    async def cmd_clear_cache(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        result = await asyncio.to_thread(lambda: self._clear_cache_images(reason="command"))
        yield event.plain_result(
            f"{MessageEmoji.SUCCESS} 缓存清理完成：删除 {result['deleted_count']} 个图片文件，"
            f"释放 {result['human_deleted_size']}。\n"
            "范围：仅 temp_images 与 user_refs 内的图片文件。"
        )

    @filter.command("签到")
    @handle_errors
    async def cmd_checkin(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        status = self._access_status(event)
        if not status.get("allowed", True):
            yield event.plain_result(self._permission_denied_message(event))
            return
        if status.get("unlimited"):
            yield event.plain_result(f"{MessageEmoji.INFO} 你已命中{status.get('reason') or '白名单'}，今日生图不受次数限制，无需签到。")
            return
        if self._daily_image_limit() <= 0:
            yield event.plain_result(f"{MessageEmoji.INFO} 当前未启用每日生图限制，无需签到。")
            return
        if not getattr(self.plugin_config, "enable_checkin", False):
            yield event.plain_result(f"{MessageEmoji.INFO} 当前未启用签到领额度。")
            return

        reply = ""
        with self._usage_lock:
            stats = self._current_usage_stats()
            users = stats.setdefault("users", {})
            user_id = status.get("user_id") or self._get_event_user_id(event)
            record = users.setdefault(user_id, {"user_id": user_id, "count": 0, "last_at": 0, "bonus": 0, "checkin_at": 0})
            used = self._to_nonnegative_int(record.get("count", 0))
            bonus = self._to_nonnegative_int(record.get("bonus", 0))
            checkin_at = self._to_nonnegative_int(record.get("checkin_at", 0))
            base_limit = self._daily_image_limit()
            if checkin_at > 0:
                reply = (
                    f"{MessageEmoji.INFO} 今日已经签到过啦：额外额度 +{bonus} 张，"
                    f"当前已使用 {used}/{base_limit + bonus} 张。"
                )
            else:
                bonus_min = self._to_nonnegative_int(getattr(self.plugin_config, "checkin_bonus_min", 1), 1)
                bonus_max = self._to_nonnegative_int(getattr(self.plugin_config, "checkin_bonus_max", 3), 3)
                if bonus_max < bonus_min:
                    bonus_max = bonus_min
                gained = random.randint(bonus_min, bonus_max) if bonus_max > bonus_min else bonus_min
                record["user_id"] = user_id
                record["count"] = used
                record["bonus"] = bonus + gained
                record["checkin_at"] = int(time.time())
                record["access_level"] = "limited"
                group_id = status.get("group_id") or self._get_event_group_id(event)
                if group_id:
                    record["group_id"] = group_id
                display_name = self._get_event_user_label(event).strip()
                if display_name and display_name != user_id:
                    record["display_name"] = display_name
                stats["total"] = sum(self._to_nonnegative_int(item.get("count", 0)) for item in users.values())
                self._persist_usage_stats()
                reply = (
                    f"{MessageEmoji.SUCCESS} 签到成功，今日额外生图额度 +{gained} 张。"
                    f"当前额度 {used}/{base_limit + bonus + gained} 张。"
                )
        yield event.plain_result(reply)

    @filter.command("人设")
    @handle_errors
    async def cmd_persona_list(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        permission_error = self._permission_denied_message(event)
        if permission_error:
            yield event.plain_result(permission_error)
            return

        msg = "🎭 可用人设:\n"
        for index, persona in enumerate(self.plugin_config.personas, start=1):
            marker = "👉" if persona.id == self.plugin_config.active_persona_id else "  "
            msg += f"{marker} [{index}] {persona.name} ({persona.id}) · 参考图 {len(persona.ref_images)} 张\n"
        msg += "\n使用 /切换人设 [序号/ID/名称] 切换自拍人格与对应参考图组。"
        yield event.plain_result(msg)

    @filter.command("画")
    @handle_errors
    async def cmd_draw(
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
        async for result in self._run_draw_command(event, "画", fallback):
            yield result

    @filter.command("gif")
    @handle_errors
    async def cmd_gif(
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
        # A previously configured /gif macro keeps precedence and is not run twice.
        if self._find_preset_name("gif"):
            return
        fallback = " ".join(str(item) for item in [p1, p2, p3, p4, p5, p6, p7, p8, p9, p10] if item).strip()
        async for result in self._run_draw_command(event, "gif", fallback, use_gif=True):
            yield result

    @filter.command("视频")
    @handle_errors
    async def cmd_video(
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

        fallback = " ".join(str(item) for item in [p1, p2, p3, p4, p5, p6, p7, p8, p9, p10] if item).strip()
        message = self._extract_command_message(event, "视频", fallback)
        raw_refs = self._get_event_images(event)
        if not message and not raw_refs:
            yield event.plain_result(f"{MessageEmoji.WARNING} 请输入视频提示词或附带参考图。")
            return

        safe_refs = []
        if raw_refs:
            needs_session = any(str(ref).startswith("http") for ref in raw_refs)
            if needs_session:
                async with aiohttp.ClientSession() as session:
                    safe_refs = await self._process_and_save_images(raw_refs, session=session)
            else:
                safe_refs = await self._process_and_save_images(raw_refs)
        prompt, kwargs = self.cmd_parser.parse(message)
        kwargs = self._sanitize_generation_kwargs(kwargs)
        if not prompt and safe_refs:
            prompt = "根据参考图生成一段自然、流畅、清晰的视频。"

        msg = f"{MessageEmoji.INFO} 视频任务已提交后台渲染..."
        if self.plugin_config.verbose_report:
            msg += f"\n📝 渲染提示词: {prompt}\n⚙️ 附加参数：{len(kwargs)} 个\n🖼️ 参考图/首尾帧：{len(safe_refs)} 张"
        pending_result = await self._send_plain_message(event, msg)
        if pending_result is not None:
            yield pending_result

        try:
            self._create_background_task(
                self.video_manager.background_task_runner(event, prompt, safe_refs, kwargs),
                kind="video",
                leased_paths=safe_refs,
            )
        except RuntimeError as exc:
            yield event.plain_result(f"{MessageEmoji.WARNING} {exc}")

    @llm_tool(name="generate_selfie")
    async def tool_generate_selfie(
        self,
        event: AstrMessageEvent,
        action: str,
        count: int = 1,
        aspect_ratio: str = "",
        size: str = "",
        extra_params: str = "",
        return_result: bool = False,
        refs: str = "",
        use_gif: bool = False,
        frame_count: int = 9,
        frame_duration_ms: int = 200,
    ) -> str:
        """
        以此 AI 助理的固定人设拍摄自拍或生成角色 GIF 动图。
        用户要求眨眼、挥手等动态自拍或 GIF 时自主设 use_gif=true，并在 action 描述连续动作。
        GIF 需要已配置且有 GIF 预览权限的 gpt-image-2.6-sunburst/flare Images 节点；不可用时报告原因，不伪装静态图。
        Args:
            action (string): 动作、姿态、服装、场景或画面描述。
            count (int): 需要生成的图片数量。默认为 1。
            aspect_ratio (string): 宽高比例，例如 1:1、3:4、9:16、16:9。
            size (string): 分辨率或尺寸参数，例如 1024x1024。
            extra_params (string): 附加模型参数透传，格式为 --key value，可同时传多个。
            return_result (bool): 仅供其他插件显式调用时使用。为 true 时不自动下发图片，而是返回 JSON 图片结果。
            refs (string): 仅在 return_result 为 true 时使用。自拍参考图 URL、本地路径或 data URL；多个参考图可用换行分隔，也可传 JSON 数组字符串。
            use_gif (bool): 是否生成原生 GIF，默认 false 保持静态自拍。true 时绕过静态副脑，size 建议留空或 auto，aspect_ratio 留空。
            frame_count (int): use_gif=true 时的帧数，2 至 16，默认 9；count 是 GIF 文件数量而非帧数。
            frame_duration_ms (int): use_gif=true 时每帧间隔毫秒，20 至 2000，默认 200。模型按 10ms 取整。
        """
        event = self._unwrap_tool_event(event)
        if not self._plugin_bool(return_result, default=False):
            # 同一轮里模型可能连调两次 generate_selfie：第二次直接拒掉，避免重复发图
            duplicate = self._selfie_duplicate_notice(event)
            if duplicate:
                return duplicate
        permission_error = self._permission_denied_message(event)
        if permission_error:
            if self._plugin_bool(return_result, default=False):
                return json.dumps({"success": False, "message": permission_error, "images": [], "mode": "selfie"}, ensure_ascii=False)
            return permission_error

        reserved_count = 0
        generation = {}
        try:
            if self._plugin_bool(return_result, default=False):
                explicit_refs = bool(str(refs or "").strip())
                result = await self.generate_images_for_plugin(
                    prompt=action,
                    count=count,
                    aspect_ratio=aspect_ratio,
                    size=size,
                    extra_params=extra_params,
                    refs=refs if explicit_refs else self._get_event_images(event),
                    mode="selfie",
                    event=event,
                    record_usage=True,
                    # [2026-11-16] 事件图/LLM 给的本地图一律限制在 data_dir 内；
                    # 只有 amo 这类直接调用 Python 方法的受信任插件才显式开 allow_local_refs。
                    allow_local_refs=False,
                    use_gif=use_gif,
                    frame_count=frame_count,
                    frame_duration_ms=frame_duration_ms,
                )
                return json.dumps(result, ensure_ascii=False)
            count = self._normalize_count(count)
            quota_error, reserved_count = self._reserve_image_quota(event, count)
            if quota_error:
                return quota_error
            _mid = str(getattr(getattr(event, "message_obj", None), "message_id", "") or "")
            self._attempt_turn = _mid or str(id(event))
            generation = await self._run_selfie_generation(
                event,
                action,
                count,
                aspect_ratio=aspect_ratio,
                size=size,
                extra_params=extra_params,
                use_gif=use_gif,
                frame_count=frame_count,
                frame_duration_ms=frame_duration_ms,
            )
            valid_results = [result for result in generation["results"] if self._get_image_result_url(result)]
            if not valid_results:
                raise RuntimeError(self._generation_failure_message(generation))
            sent = await self._send_generated_images(event, valid_results)
            self._complete_image_quota(event, reserved_count, sent)
            self._mark_selfie_sent(event)
            return f"系统提示：已成功生成并下发了 {sent} 张图。"
        except asyncio.CancelledError:
            self._release_image_quota(event, reserved_count)
            raise
        except Exception as exc:
            self._release_image_quota(event, reserved_count)
            logger.error(f"[我能永远定格你] LLM 自拍工具失败: {exc}", exc_info=True)
            if self._plugin_bool(return_result, default=False):
                return json.dumps({
                    "success": False,
                    "message": f"画图失败 ({self._safe_plugin_error_message(exc)})。",
                    "images": [],
                    "mode": "selfie",
                }, ensure_ascii=False)
            return f"系统提示：画图失败 ({self._safe_plugin_error_message(exc)})。"
        finally:
            if generation.get("leased_result_paths"):
                self._release_cache_paths(generation["leased_result_paths"])

    @llm_tool(name="generate_image")
    async def tool_generate_image(
        self,
        event: AstrMessageEvent,
        prompt: str,
        count: int = 1,
        aspect_ratio: str = "",
        size: str = "",
        extra_params: str = "",
        return_result: bool = False,
        refs: str = "",
        use_gif: bool = False,
        frame_count: int = 9,
        frame_duration_ms: int = 200,
    ) -> str:
        """
        AI 画图 / GIF 动图工具。当用户要求绘图、改图、动态表情包或 GIF 时调用此工具。
        用户要 GIF、循环动画或动态表情时自主设 use_gif=true，在 prompt 描述连续动作及循环，不要改用视频工具。
        普通图片保持 use_gif=false。GIF 需要有 GIF 预览权限的 gpt-image-2.6-sunburst/flare Images 节点。
        若 GIF 节点不可用，解释失败原因，不自动改成静态图，也不要反复重新生成。
        Args:
            prompt (string): 图片提示词，描述主体、风格、场景、构图和细节。
            count (int): 图片数量。默认为 1。
            aspect_ratio (string): 宽高比例，例如 1:1、3:4、9:16、16:9。
            size (string): 分辨率或尺寸参数，例如 1024x1024。
            extra_params (string): 其他模型参数透传，格式为 --key value，可同时传多个。
            return_result (bool): 仅供其他插件显式调用时使用。为 true 时不自动下发图片，而是返回 JSON 图片结果。
            refs (string): 仅在 return_result 为 true 时使用。参考图 URL、本地路径或 data URL；多个参考图可用换行分隔，也可传 JSON 数组字符串。
            use_gif (bool): 是否生成原生 GIF，默认 false。true 时绕过静态副脑，size 建议留空或 auto，aspect_ratio 留空。
            frame_count (int): use_gif=true 时的帧数，2 至 16，默认 9；count 是 GIF 文件数量而非帧数。
            frame_duration_ms (int): use_gif=true 时每帧间隔毫秒，20 至 2000，默认 200。模型按 10ms 取整。
        """
        event = self._unwrap_tool_event(event)
        permission_error = self._permission_denied_message(event)
        if permission_error:
            if self._plugin_bool(return_result, default=False):
                return json.dumps({"success": False, "message": permission_error, "images": [], "mode": "text2img"}, ensure_ascii=False)
            return permission_error

        reserved_count = 0
        generation = {}
        try:
            if self._plugin_bool(return_result, default=False):
                explicit_refs = bool(str(refs or "").strip())
                result = await self.generate_images_for_plugin(
                    prompt=prompt,
                    count=count,
                    aspect_ratio=aspect_ratio,
                    size=size,
                    extra_params=extra_params,
                    refs=refs if explicit_refs else self._get_event_images(event),
                    event=event,
                    record_usage=True,
                    # [2026-11-16] 事件图/LLM 给的本地图一律限制在 data_dir 内；
                    # 只有 amo 这类直接调用 Python 方法的受信任插件才显式开 allow_local_refs。
                    allow_local_refs=False,
                    use_gif=use_gif,
                    frame_count=frame_count,
                    frame_duration_ms=frame_duration_ms,
                )
                return json.dumps(result, ensure_ascii=False)
            count = self._normalize_count(count)
            quota_error, reserved_count = self._reserve_image_quota(event, count)
            if quota_error:
                return quota_error
            generation = await self._run_text2img_generation(
                event,
                prompt,
                count,
                aspect_ratio=aspect_ratio,
                size=size,
                extra_params=extra_params,
                use_gif=use_gif,
                frame_count=frame_count,
                frame_duration_ms=frame_duration_ms,
            )
            valid_results = [result for result in generation["results"] if self._get_image_result_url(result)]
            if not valid_results:
                raise RuntimeError(self._generation_failure_message(generation))
            sent = await self._send_generated_images(event, valid_results)
            self._complete_image_quota(event, reserved_count, sent)
            return f"系统提示：已成功下发 {sent} 张图。"
        except asyncio.CancelledError:
            self._release_image_quota(event, reserved_count)
            raise
        except Exception as exc:
            self._release_image_quota(event, reserved_count)
            logger.error(f"[我能永远定格你] LLM 画图工具失败: {exc}", exc_info=True)
            if self._plugin_bool(return_result, default=False):
                return json.dumps({
                    "success": False,
                    "message": f"画图失败 ({self._safe_plugin_error_message(exc)})。",
                    "images": [],
                    "mode": "text2img",
                }, ensure_ascii=False)
            return f"系统提示：画图失败 ({self._safe_plugin_error_message(exc)})。"
        finally:
            if generation.get("leased_result_paths"):
                self._release_cache_paths(generation["leased_result_paths"])

    @llm_tool(name="generate_video")
    async def tool_generate_video(
        self,
        event: AstrMessageEvent,
        prompt: str,
        count: int = 1,
        aspect_ratio: str = "",
        size: str = "",
        extra_params: str = "",
    ) -> str:
        """
        AI 视频生成工具。当用户要求生成一段视频时调用。
        Args:
            prompt (string): 视频提示词，描述画面、动作、镜头运动、时长感和风格。
            count (int): 视频数量。默认为 1。
            aspect_ratio (string): 宽高比例，例如 9:16、16:9、1:1。
            size (string): 分辨率或尺寸参数，例如 1280x720、1920x1080。
            extra_params (string): 附加参数，透传至底层视频引擎，格式为 --key value。
        """
        permission_error = self._permission_denied_message(event)
        if permission_error:
            return permission_error

        try:
            count = self._normalize_count(count)
            raw_refs = self._get_event_images(event)
            safe_refs = []
            if raw_refs:
                needs_session = any(str(ref).startswith("http") for ref in raw_refs)
                if needs_session:
                    async with aiohttp.ClientSession() as session:
                        safe_refs = await self._process_and_save_images(raw_refs, session=session)
                else:
                    safe_refs = await self._process_and_save_images(raw_refs)
            kwargs = self._parse_extra_params(extra_params)
            if aspect_ratio:
                kwargs["aspect_ratio"] = aspect_ratio
            if size:
                kwargs["size"] = size

            accepted = 0
            for _ in range(count):
                try:
                    self._create_background_task(
                        self.video_manager.background_task_runner(
                            event,
                            prompt,
                            safe_refs,
                            kwargs,
                            include_metadata=False,
                        ),
                        kind="video",
                        leased_paths=safe_refs,
                    )
                    accepted += 1
                except RuntimeError:
                    break
            if not accepted:
                return "系统提示：后台视频任务队列已满，请稍后再试。"
            return f"系统提示：已在后台独立提交了 {accepted} 个视频渲染任务。请告诉用户正在渲染中。"
        except Exception as exc:
            logger.error(f"[我能永远定格你] LLM 视频工具失败: {exc}", exc_info=True)
            return f"系统提示：失败 ({self._safe_plugin_error_message(exc)})。"

    def _extract_at_user_id(self, obj: Any) -> str:
        for attr in ("qq", "user_id", "target", "id", "uin"):
            value = getattr(obj, attr, None)
            if value:
                return str(value).strip()
        return ""

    def _build_qq_avatar_url(self, user_id: str) -> str:
        user_id = str(user_id or "").strip()
        if not user_id or user_id.lower() == "all" or not re.fullmatch(r"\d+", user_id):
            return ""
        return f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"

    def _extend_command_prefixes(self, prefixes: List[str], value: Any) -> None:
        if value is None:
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                self._extend_command_prefixes(prefixes, item)
            return

        prefix = str(value).strip()
        if prefix not in prefixes:
            prefixes.append(prefix)

    def _configured_astrbot_command_prefixes(self) -> List[str]:
        config_path = os.path.join(get_astrbot_data_path(), "cmd_config.json")
        try:
            stat = os.stat(config_path)
            signature = f"{stat.st_mtime_ns}:{stat.st_size}"
        except OSError:
            signature = ""
        if (
            getattr(self, "_astrbot_cmd_prefix_config_path", "") == config_path
            and getattr(self, "_astrbot_cmd_prefix_config_signature", None) == signature
        ):
            return list(getattr(self, "_astrbot_cmd_prefix_cache", []))

        prefixes: List[str] = []
        command_config = self._load_json_file(config_path)
        self._extend_command_prefixes(prefixes, command_config.get("wake_prefix"))
        self._extend_command_prefixes(prefixes, command_config.get("command_prefix"))
        self._extend_command_prefixes(prefixes, command_config.get("command_prefixes"))

        self._astrbot_cmd_prefix_config_path = config_path
        self._astrbot_cmd_prefix_config_signature = signature
        self._astrbot_cmd_prefix_cache = list(prefixes)
        return prefixes

    def _command_prefixes(self) -> List[str]:
        prefixes: List[str] = []
        self._extend_command_prefixes(prefixes, self._configured_astrbot_command_prefixes())
        self._extend_command_prefixes(prefixes, LEGACY_COMMAND_PREFIXES)
        return prefixes or ["/"]

    def _command_text_without_prefix(self, text: str, allow_bare: bool = False) -> str:
        text = str(text or "").strip()
        if not text:
            return ""

        prefixes = self._command_prefixes()
        for prefix in sorted((item for item in prefixes if item), key=len, reverse=True):
            if text.startswith(prefix):
                return text[len(prefix):].lstrip()

        if allow_bare or "" in prefixes:
            return text
        return ""

    def _event_is_at_or_wake_command(self, event: AstrMessageEvent) -> bool:
        value = getattr(event, "is_at_or_wake_command", False)
        if callable(value):
            try:
                value = value()
            except Exception:
                value = False
        return bool(value)

    def _stop_event_if_possible(self, event: AstrMessageEvent) -> None:
        stop_event = getattr(event, "stop_event", None)
        if callable(stop_event):
            try:
                stop_event()
            except Exception:  # silent: 停止事件回调是尽力而为，异常不阻断主流程
                pass

    async def _send_plain_message(self, event: AstrMessageEvent, message: str) -> Optional[Any]:
        result = event.plain_result(message)
        send = getattr(event, "send", None)
        if callable(send):
            try:
                await send(result)
            except Exception as exc:
                logger.warning(f"[我能永远定格你] 等待提示发送失败，生成任务继续执行: {exc}")
            return None
        return result

    def _command_prefix_regex(self) -> str:
        prefixes = [prefix for prefix in self._command_prefixes() if prefix]
        if not prefixes:
            return ""
        escaped = [re.escape(prefix) for prefix in sorted(prefixes, key=len, reverse=True)]
        return "(?:" + "|".join(escaped) + ")"

    def _display_command_prefix(self) -> str:
        for prefix in self._command_prefixes():
            if prefix:
                return prefix
        return ""

    def _message_component_has_command(self, obj: Any, command: str) -> bool:
        if not command or type(obj).__name__ != "Plain":
            return False
        text = str(getattr(obj, "text", "") or "")
        prefix_pattern = self._command_prefix_regex()
        prefix_part = rf"(?:{prefix_pattern})?" if prefix_pattern else ""
        pattern = rf"(^|\s){prefix_part}{re.escape(command)}(?:\s|$)"
        return bool(re.search(pattern, text))

    def _get_event_user_id(self, event: AstrMessageEvent) -> str:
        try:
            sender_id = event.get_sender_id()
            if sender_id:
                return str(sender_id)
        except Exception:  # silent: get_sender_id 缺失时走 message_obj 兜底
            pass

        message_obj = getattr(event, "message_obj", None)
        for attr in ("sender_id", "user_id", "member_id"):
            value = getattr(event, attr, None) or getattr(message_obj, attr, None)
            if value:
                return str(value)
        return "unknown"

    def _get_event_user_label(self, event: AstrMessageEvent) -> str:
        for method_name in ("get_sender_name", "get_sender_nickname"):
            method = getattr(event, method_name, None)
            if callable(method):
                try:
                    value = method()
                    if value:
                        return str(value)
                except Exception:  # silent: 可选 sender 方法探测，异常视为没有
                    pass

        message_obj = getattr(event, "message_obj", None)
        sender = getattr(message_obj, "sender", None)
        for obj in (sender, message_obj, event):
            for attr in ("nickname", "card", "username", "name", "sender_name"):
                value = getattr(obj, attr, None)
                if value:
                    return str(value)
        return ""

    def _event_is_group_message(self, event: AstrMessageEvent) -> bool:
        method = getattr(event, "get_message_type", None)
        if callable(method):
            try:
                value = method()
                raw_value = getattr(value, "value", value)
                if "group" in str(raw_value).lower():
                    return True
            except Exception:  # silent: 群聊类型探测失败按非群聊处理
                pass

        message_obj = getattr(event, "message_obj", None)
        for obj in (message_obj, event):
            for attr in ("type", "message_type"):
                value = getattr(obj, attr, None)
                raw_value = getattr(value, "value", value)
                if "group" in str(raw_value).lower():
                    return True
        return False

    def _get_event_group_id(self, event: AstrMessageEvent) -> str:
        for method_name in ("get_group_id",):
            method = getattr(event, method_name, None)
            if callable(method):
                try:
                    value = method()
                    if value:
                        return str(value)
                except Exception:  # silent: get_group_id 可选，缺失时走属性兜底
                    pass

        message_obj = getattr(event, "message_obj", None)
        for obj in (message_obj, event):
            for attr in ("group_id", "group", "room_id", "channel_id"):
                value = getattr(obj, attr, None)
                if value:
                    return str(value)

        if self._event_is_group_message(event):
            for method_name in ("get_session_id",):
                method = getattr(event, method_name, None)
                if callable(method):
                    try:
                        value = str(method() or "").strip()
                        if value:
                            return value
                    except Exception:  # silent: session_id 推断群号失败时继续扫属性
                        pass
            for obj in (event, message_obj):
                value = str(getattr(obj, "session_id", "") or "").strip()
                if value:
                    return value

        for method_name in ("get_session_id",):
            method = getattr(event, method_name, None)
            if callable(method):
                try:
                    value = method()
                    group_id = self._extract_group_id_from_text(value)
                    if group_id:
                        return group_id
                except Exception:  # silent: session_id 文本解析群号失败时继续扫属性
                    pass

        for obj in (event, message_obj):
            for attr in ("unified_msg_origin", "session_id", "session", "origin"):
                group_id = self._extract_group_id_from_text(getattr(obj, attr, ""))
                if group_id:
                    return group_id
        return ""

    def _extract_group_id_from_text(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        parts = text.split(":", 2)
        if len(parts) == 3 and "group" in parts[1].lower():
            return parts[2].strip()
        lowered = text.lower()
        if "group" not in lowered and "群" not in text:
            return ""
        labelled_match = re.search(r"(?:group_id|group|群)[=:_\-\s]+(\d+)", text, flags=re.I)
        if labelled_match:
            return labelled_match.group(1)
        matches = re.findall(r"\d+", text)
        return matches[0] if matches else ""

    def _config_id_set(self, value: Any) -> set:
        if isinstance(value, (list, tuple, set)):
            source = value
        else:
            source = re.split(r"[\s,]+", str(value or "").replace("\r", "\n"))
        return {str(item).strip() for item in source if str(item).strip()}

    def _access_status(self, event: AstrMessageEvent, refresh: bool = True) -> Dict[str, Any]:
        if refresh:
            self._refresh_from_native_config_if_changed()

        user_id = self._get_event_user_id(event)
        group_id = self._get_event_group_id(event)
        usable_users = self._config_id_set(getattr(self.plugin_config, "usable_users", []))
        blocked_users = self._config_id_set(getattr(self.plugin_config, "blocked_users", []))
        unlimited_users = self._config_id_set(getattr(self.plugin_config, "unlimited_users", []))
        if not unlimited_users:
            unlimited_users = self._config_id_set(getattr(self.plugin_config, "allowed_users", []))
        unlimited_groups = self._config_id_set(getattr(self.plugin_config, "unlimited_groups", []))

        status = {
            "user_id": user_id,
            "group_id": group_id,
            "allowed": True,
            "unlimited": False,
            "level": "limited",
            "reason": "",
        }
        if user_id in blocked_users:
            status.update({"allowed": False, "level": "blocked_user", "reason": "用户黑名单"})
            return status
        if usable_users and user_id not in usable_users:
            status.update({"allowed": False, "level": "not_usable_user", "reason": "可使用人员白名单"})
            return status
        if usable_users:
            status.update({"level": "usable_user", "reason": "可使用人员白名单"})
        if user_id in unlimited_users:
            status.update({"unlimited": True, "level": "unlimited_user", "reason": "用户白名单"})
            return status
        if group_id and group_id in unlimited_groups:
            status.update({"unlimited": True, "level": "unlimited_group", "reason": "群组白名单"})
            return status
        return status

    def _permission_denied_message(self, event: AstrMessageEvent) -> str:
        status = self._access_status(event)
        if status.get("allowed", True):
            return ""
        if status.get("level") == "not_usable_user":
            return f"{MessageEmoji.WARNING} 当前仅允许可使用人员白名单内用户使用「我能永远定格你」。"
        return f"{MessageEmoji.WARNING} 你已被加入用户黑名单，无法使用「我能永远定格你」。"

    def _normalize_plugin_principal(self, principal: Any) -> Optional[Dict[str, Any]]:
        """跨插件 principal -> 与 event 同形状的 access status；坏形状返回 None。

        约定见模块常量 PLUGIN_PRINCIPAL_*；调用方（能陪）传：
        {contract, version, caller, kind, user_id, scope, umo}
        """
        if not isinstance(principal, dict):
            return None
        caller = str(principal.get("caller") or "").strip()
        kind = str(principal.get("kind") or "").strip().lower()
        if caller not in TRUSTED_PLUGIN_CALLERS or kind not in PLUGIN_PRINCIPAL_KINDS:
            return None
        user_id = str(principal.get("user_id") or "").strip() or ("%s:owner" % caller)
        status = {
            "user_id": user_id,
            "group_id": str(principal.get("group_id") or "").strip(),
            "allowed": True,
            "unlimited": kind == "system",
            "level": "plugin_" + kind,
            "reason": "",
            "caller": caller,
            "kind": kind,
            "contract": str(principal.get("contract") or ""),
            "principal": dict(principal),
        }
        blocked_users = self._config_id_set(getattr(self.plugin_config, "blocked_users", []))
        if user_id in blocked_users:
            status.update({"allowed": False, "level": "blocked_user", "reason": "用户黑名单"})
        return status

    def _plugin_legacy_status(self) -> Dict[str, Any]:
        """旧版调用方（无 event 也无 principal）：放行但打标，用量记到 unattributed。

        这是滚动升级期的过渡：旧能陪还会 record_usage=True 但不带 principal，
        这里至少把图计入面板（修 11-22 面板停在 11-21），并把 contract_miss 记账，
        面板/健康项可见；新能陪（photo_generate.py）已改为带 principal 走完整校验。
        """
        return {
            "user_id": "", "group_id": "", "allowed": True, "unlimited": True,
            "level": "legacy_system", "reason": "missing principal", "legacy": True,
            "caller": "", "kind": "", "contract": "",
        }

    def _plugin_principal_status(self, principal: Any, event: Any = None) -> Optional[Dict[str, Any]]:
        """生图入口的 access status：event 优先，其次 principal；都没有返回 None。"""
        if event is not None:
            return self._access_status(event)
        return self._normalize_plugin_principal(principal)

    def _resolve_plugin_access(self, event: Any, principal: Any):
        """生图入口统一解析：返回 (access_status, record_source, contract_error)。

        - event 优先（命令/工具路径，原有权限判定不变）；
        - event=None 时 principal 必须合法（owner：权限+配额+用量；system：内部生成）；
        - 都没有 = 旧版调用：放行但打标、计 contract_miss（滚动升级期，面板可见）。
        """
        if event is not None:
            return self._access_status(event), "event", ""
        if principal is not None:
            status = self._normalize_plugin_principal(principal)
            if status is None:
                return None, "", PLUGIN_PRINCIPAL_SAFE_DENY
            return status, "plugin", ""
        self._notice_contract("contract_miss", "generate_images_for_plugin")
        return self._plugin_legacy_status(), "legacy", ""

    def _with_usage_contract(self, response: Any, access: Any, usage_recorded: bool,
                             record_source: str = "") -> Dict[str, Any]:
        """给生图结果补契约字段：调用方据此判断权限/配额/用量是否真的走了。"""
        data = dict(response) if isinstance(response, dict) else {"success": False, "images": []}
        data["usage_recorded"] = bool(usage_recorded)
        data["contract"] = PLUGIN_PRINCIPAL_CONTRACT
        data["principal"] = {
            "caller": str((access or {}).get("caller") or ""),
            "kind": str((access or {}).get("kind") or ""),
            "level": str((access or {}).get("level") or ""),
            "user_id": str((access or {}).get("user_id") or ""),
            "source": str(record_source or ""),
        }
        return data

    def _ensure_usage_runtime(self) -> None:
        """测试桩/老实例可能没在 __init__ 里建 usage 设施：按需补齐（幂等）。"""
        if not hasattr(self, "_usage_lock"):
            import threading
            self._usage_lock = threading.RLock()
        if not hasattr(self, "_usage_stats"):
            self._usage_stats = {}
        if not hasattr(self, "_quota_reservations"):
            self._quota_reservations = {}

    def _notice_contract(self, key: str = "contract_miss", where: str = "") -> None:
        """跨插件契约缺口计数（不写日志洪水）：面板 + /status 可见。"""
        key = str(key or "contract_miss")
        try:
            self._ensure_usage_runtime()
            with self._usage_lock:
                stats = self._normalize_usage_stats(self._usage_stats)
                stats[key] = self._to_nonnegative_int(stats.get(key, 0)) + 1
                if key == "contract_miss" and where:
                    stats["contract_miss_last"] = str(where)[:64]
                self._usage_stats = stats
                self._persist_usage_stats()
        except Exception as exc:  # 计数失败不影响生图
            logger.warning(f"[我能永远定格你] 契约计数失败: {exc}")

    def _get_event_text(self, event: AstrMessageEvent) -> str:
        text = getattr(event, "message_str", "") or getattr(getattr(event, "message_obj", None), "message_str", "")
        if text:
            return str(text).strip()

        message = getattr(getattr(event, "message_obj", None), "message", []) or []
        plain_text = "".join(getattr(comp, "text", "") for comp in message if isinstance(comp, Plain)).strip()
        if plain_text:
            return plain_text
        return str(getattr(event, "message_obj", "") or "").strip()

    def _daily_image_limit(self) -> int:
        if not getattr(self.plugin_config, "enable_daily_limit", False):
            return 0
        configured_limit = self._to_nonnegative_int(getattr(self.plugin_config, "daily_image_limit", 20), 20)
        return max(1, configured_limit)

    def _image_quota_state(self, event: AstrMessageEvent) -> Dict[str, Any]:
        return self._status_quota_state(self._access_status(event))

    def _status_quota_state(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """配额状态：event 与跨插件 principal 收敛成同一个 access status 后再算。"""
        self._ensure_usage_runtime()
        status = dict(status or {})
        limit = self._daily_image_limit()
        user_id = str(status.get("user_id") or "")
        with self._usage_lock:
            stats = self._normalize_usage_stats(self._usage_stats)
            self._usage_stats = stats
            record = stats.get("users", {}).get(user_id, {}) if user_id else {}
            used = self._to_nonnegative_int(record.get("count", 0))
            bonus = self._to_nonnegative_int(record.get("bonus", 0))
            reserved = self._to_nonnegative_int(self._quota_reservations.get(user_id, 0))
        effective_limit = 0 if status.get("unlimited") or limit <= 0 else limit + bonus
        return {
            **status,
            "user_id": user_id,
            "base_limit": limit,
            "bonus": bonus,
            "effective_limit": effective_limit,
            "used": used,
            "reserved": reserved,
            "remaining": max(0, effective_limit - used - reserved) if effective_limit > 0 else 0,
            "checkin_at": self._to_nonnegative_int(record.get("checkin_at", 0)),
        }

    def _reserve_status_quota(self, status: Dict[str, Any], requested_count: int) -> Tuple[str, int]:
        """按 access status 预留配额；event / principal / legacy 三条入口共用。"""
        requested_count = max(1, self._to_nonnegative_int(requested_count, 1))
        quota = self._status_quota_state(status)
        if not quota.get("allowed", True):
            return str(quota.get("reason") or "forbidden"), 0
        if quota.get("unlimited") or quota.get("base_limit", 0) <= 0:
            return "", 0
        user_id = str(quota.get("user_id") or "")
        if not user_id:
            return "", 0
        used = self._to_nonnegative_int(quota.get("used", 0))
        reserved = self._to_nonnegative_int(quota.get("reserved", 0))
        effective_limit = self._to_nonnegative_int(quota.get("effective_limit", 0))
        if used + reserved + requested_count > effective_limit:
            return self._quota_short_message(quota, requested_count), 0
        with self._usage_lock:
            current = self._to_nonnegative_int(self._quota_reservations.get(user_id, 0))
            self._quota_reservations[user_id] = current + requested_count
        return "", requested_count

    def _quota_short_message(self, quota: Dict[str, Any], requested_count: int = 1) -> str:
        used = self._to_nonnegative_int(quota.get("used", 0))
        base_limit = self._to_nonnegative_int(quota.get("base_limit", 0))
        bonus = self._to_nonnegative_int(quota.get("bonus", 0))
        effective_limit = self._to_nonnegative_int(quota.get("effective_limit", base_limit))
        reserved = self._to_nonnegative_int(quota.get("reserved", 0))
        remaining = max(0, effective_limit - used - reserved)
        limit_text = f"{effective_limit} 张"
        if bonus:
            limit_text = f"{effective_limit} 张（基础 {base_limit} + 签到 {bonus}）"
        return (
            f"{MessageEmoji.WARNING} 今日生图额度不足：你已使用 {used}/{limit_text}，"
            f"本次需要 {requested_count} 张，剩余 {remaining} 张。"
        )

    def _release_quota_for(self, event: Any, access: Dict[str, Any], reserved_count: int) -> None:
        """统一释放：event 路径走原有 hook（可被测试替换），principal 路径直接释放。"""
        if not reserved_count:
            return
        if event is not None:
            self._release_image_quota(event, reserved_count)
        else:
            self._release_status_quota(access, reserved_count)

    def _reserve_image_quota(self, event: AstrMessageEvent, requested_count: int) -> Tuple[str, int]:
        status = self._access_status(event, refresh=False)
        if not status.get("allowed", True):
            return self._permission_denied_message(event), 0
        return self._reserve_status_quota(status, requested_count)

    def _release_status_quota(self, status: Dict[str, Any], reserved_count: int) -> None:
        if reserved_count is None or str((status or {}).get("user_id") or "") == "":
            return
        self._ensure_usage_runtime()
        reserved_count = max(0, self._to_nonnegative_int(reserved_count, 0))
        if reserved_count <= 0:
            return
        user_id = str((status or {}).get("user_id") or "")
        if not user_id:
            return
        with self._usage_lock:
            current = self._to_nonnegative_int(self._quota_reservations.get(user_id, 0))
            remaining = max(0, current - reserved_count)
            if remaining:
                self._quota_reservations[user_id] = remaining
            else:
                self._quota_reservations.pop(user_id, None)

    def _release_image_quota(self, event: AstrMessageEvent, reserved_count: int) -> None:
        self._release_status_quota(self._access_status(event, refresh=False), reserved_count)

    def _finalize_status_quota(
        self,
        status: Dict[str, Any],
        reserved_count: int,
        actual_count: int,
        source: str = "event",
    ) -> None:
        """结算预留：先释放，再按实际出图张数计入 usage（source=event/plugin/legacy）。"""
        self._ensure_usage_runtime()
        user_id = str((status or {}).get("user_id") or "")
        reserved_count = max(0, self._to_nonnegative_int(reserved_count, 0))
        with self._usage_lock:
            if reserved_count and user_id:
                current = self._to_nonnegative_int(self._quota_reservations.get(user_id, 0))
                remaining = max(0, current - reserved_count)
                if remaining:
                    self._quota_reservations[user_id] = remaining
                else:
                    self._quota_reservations.pop(user_id, None)
            self._record_status_images_locked(status, actual_count, source)

    def _finalize_image_quota(
        self,
        event: AstrMessageEvent,
        reserved_count: int,
        actual_count: int,
    ) -> None:
        self._finalize_status_quota(self._access_status(event, refresh=False),
                                    reserved_count, actual_count, "event")

    def _complete_image_quota(
        self,
        event: AstrMessageEvent,
        reserved_count: int,
        actual_count: int,
    ) -> None:
        if reserved_count:
            self._finalize_image_quota(event, reserved_count, actual_count)
        else:
            self._record_generated_images(event, actual_count)

    def _image_quota_error_for_status(self, status: Dict[str, Any], requested_count: int = 1) -> str:
        """不预留、只检查：event 与 principal 共用。旧版内部调用没有身份，不查配额。"""
        status = dict(status or {})
        if status.get("legacy") or not str(status.get("user_id") or "").strip():
            return ""
        quota = self._status_quota_state(status)
        if not quota.get("allowed", True):
            return str(quota.get("reason") or "forbidden")
        if quota.get("unlimited") or quota.get("base_limit", 0) <= 0:
            return ""
        requested_count = max(1, self._to_nonnegative_int(requested_count, 1))
        used = self._to_nonnegative_int(quota.get("used", 0))
        effective_limit = self._to_nonnegative_int(quota.get("effective_limit", 0))
        remaining = max(0, effective_limit - used - self._to_nonnegative_int(quota.get("reserved", 0)))
        if requested_count <= remaining:
            return ""
        return self._quota_short_message(quota, requested_count)

    def _image_quota_error_message(self, event: AstrMessageEvent, requested_count: int = 1) -> str:
        status = self._access_status(event)
        if not status.get("allowed", True):
            return self._permission_denied_message(event)
        return self._image_quota_error_for_status(status, requested_count)

    def _record_status_images(self, status: Dict[str, Any], count: int = 1, source: str = "event") -> None:
        self._ensure_usage_runtime()
        count = self._to_nonnegative_int(count)
        if count <= 0:
            return
        with self._usage_lock:
            self._record_status_images_locked(status, count, source)

    def _record_status_images_locked(self, status: Dict[str, Any], count: int = 1,
                                     source: str = "event") -> None:
        """按 access status 记用量；source 让面板能分“命令/跨插件/旧版”三条路径。"""
        self._ensure_usage_runtime()
        status = dict(status or {})
        stats = self._normalize_usage_stats(self._usage_stats)
        self._usage_stats = stats
        user_id = str(status.get("user_id") or "").strip()
        if not user_id:
            user_id = "legacy:unattributed" if str(source) == "legacy" else "unattributed"
        users = stats.setdefault("users", {})
        record = users.setdefault(
            user_id,
            {"user_id": user_id, "count": 0, "last_at": 0, "bonus": 0, "checkin_at": 0},
        )
        record["user_id"] = user_id
        record["count"] = self._to_nonnegative_int(record.get("count", 0)) + count
        record["bonus"] = self._to_nonnegative_int(record.get("bonus", 0))
        record["checkin_at"] = self._to_nonnegative_int(record.get("checkin_at", 0))
        record["last_at"] = int(time.time())
        record["access_level"] = str(status.get("level") or "limited")
        record["source"] = str(source or "event")
        group_id = status.get("group_id") or ""
        if group_id:
            record["group_id"] = group_id
        display_name = str((status.get("principal") or {}).get("display_name") or "").strip()
        if not display_name:
            display_name = str(status.get("display_name") or "").strip()
        if display_name and display_name != user_id:
            record["display_name"] = display_name
        stats["total"] = sum(self._to_nonnegative_int(item.get("count", 0)) for item in users.values())
        try:
            self._persist_usage_stats()
        except Exception as exc:  # 测试桩/数据目录不可写：内存计数仍然有效
            logger.warning(f"[我能永远定格你] 生图统计保存失败: {exc}")

    def _record_generated_images(self, event: AstrMessageEvent, count: int = 1) -> None:
        self._record_status_images(self._access_status(event, refresh=False), count, "event")

    def _record_generated_images_locked(self, event: AstrMessageEvent, count: int = 1) -> None:
        self._record_status_images_locked(self._access_status(event, refresh=False), count, "event")

    def _extract_command_message(self, event: AstrMessageEvent, command: str, fallback: str = "") -> str:
        return self._extract_command_message_any(event, (command,), fallback)

    def _extract_command_message_any(
        self,
        event: AstrMessageEvent,
        commands: Iterable[str],
        fallback: str = "",
    ) -> str:
        text = self._get_event_text(event)
        if not text:
            return fallback.strip()
        command_text = self._command_text_without_prefix(text, allow_bare=True)
        for command in sorted((str(item or "").strip() for item in commands), key=len, reverse=True):
            if not command:
                continue
            pattern = rf"^{re.escape(command)}(?:\s+(.*))?$"
            match = re.match(pattern, command_text, flags=re.S)
            if match:
                return (match.group(1) or "").strip()
        return fallback.strip()

    def _extract_compact_command_payload(self, text: str, command: str, allow_bare: bool = False) -> str:
        return self._extract_compact_command_payload_any(text, (command,), allow_bare=allow_bare)

    def _extract_compact_command_payload_any(
        self,
        text: str,
        commands: Iterable[str],
        allow_bare: bool = False,
    ) -> str:
        command_text = self._command_text_without_prefix(text, allow_bare=allow_bare)
        for command in sorted((str(item or "").strip() for item in commands), key=len, reverse=True):
            if not command or not command_text.startswith(command):
                continue
            payload = command_text[len(command):]
            if not payload or payload[0].isspace():
                continue
            return payload.strip()
        return ""

    def _parse_extra_params(self, extra_params: str) -> Dict[str, Any]:
        if not extra_params:
            return {}
        _, parsed = self.cmd_parser.parse(extra_params)
        return self._sanitize_generation_kwargs(parsed)

    def _sanitize_generation_kwargs(self, kwargs: Any) -> Dict[str, Any]:
        if not isinstance(kwargs, dict):
            return {}

        kwargs = normalize_gif_options(kwargs)
        sanitized = {}
        for raw_key, value in kwargs.items():
            key = str(raw_key or "").strip()
            if not key:
                continue
            if key.lower() in RESERVED_GENERATION_PARAM_KEYS:
                logger.warning(f"[我能永远定格你] 已忽略不允许透传的生成参数: {key}")
                continue
            sanitized[key] = value
        return sanitized

    def _image_request_options(
        self, extra_params: str, aspect_ratio: str, size: str,
        use_gif: bool = False, frame_count: int = 9, frame_duration_ms: int = 200,
    ) -> Dict[str, Any]:
        options: Dict[str, Any] = {}
        if aspect_ratio:
            options["aspect_ratio"] = aspect_ratio
        if size:
            options["size"] = size
        _, extra = self.cmd_parser.parse(extra_params or "")
        options.update(extra)
        if self._plugin_bool(use_gif, default=False):
            # Parse before sanitizing so GIF-incompatible reserved options cannot vanish silently.
            options.update(output_format="gif", frame_count=frame_count, frame_duration_ms=frame_duration_ms)
        return self._sanitize_generation_kwargs(options)

    def _normalize_count(self, count: Any) -> int:
        try:
            parsed_count = int(float(str(count).strip()))
        except Exception:
            parsed_count = 1
        limit = self.plugin_config.max_batch_count or DEFAULT_BATCH_LIMIT
        return min(max(1, parsed_count), max(1, limit))

    def _preset_items(self) -> List[tuple]:
        presets = getattr(self.plugin_config, "presets", {}) or {}
        return [
            (str(name).strip(), str(prompt or "").strip())
            for name, prompt in presets.items()
            if str(name).strip()
        ]

    def _normalize_preset_selector(self, selector: str) -> str:
        selector = str(selector or "").strip()
        if len(selector) >= 2:
            bracket_pairs = {"[": "]", "【": "】", "「": "」", "《": "》"}
            closing = bracket_pairs.get(selector[0])
            if closing and selector.endswith(closing):
                return selector[1:-1].strip()
        return selector

    def _find_preset_name(self, selector: str) -> str:
        selector = self._normalize_preset_selector(selector)
        if not selector:
            return ""
        if selector in (getattr(self.plugin_config, "presets", {}) or {}):
            return selector

        selector_lower = selector.lower()
        for name, _ in self._preset_items():
            if name.lower() == selector_lower:
                return name
        return ""

    def _build_preset_list_message(self) -> str:
        preset_names = [name for name, _ in self._preset_items()]
        if not preset_names:
            return f"{MessageEmoji.INFO} 当前没有配置极速宏预设。"

        lines = ["✨ 预设列表"]
        lines.extend(f"{index}. {name}" for index, name in enumerate(preset_names, start=1))
        return "\n".join(lines)

    def _build_preset_detail_message(self, selector: str) -> str:
        preset_name = self._find_preset_name(selector)
        if not preset_name:
            return f"{MessageEmoji.WARNING} 找不到预设: {selector}\n可发送 /查看预设 查看所有预设名。"

        preset_prompt = (getattr(self.plugin_config, "presets", {}) or {}).get(preset_name, "")
        return f"✨ 预设详情\n名称：{preset_name}\n提示词：{preset_prompt}"

    def _build_preset_view_message(self, selector: str = "") -> str:
        selector = str(selector or "").strip()
        if selector:
            return self._build_preset_detail_message(selector)
        return self._build_preset_list_message()

    def _build_fast_preset_list_message(self) -> str:
        return self._build_preset_list_message()

    def _parse_preset_trigger(self, text: str, allow_bare: bool = False) -> Tuple[str, str]:
        command_text = self._command_text_without_prefix(text, allow_bare=allow_bare)
        if not command_text:
            return "", ""

        command_text_lower = command_text.lower()
        for preset_name, _ in sorted(self._preset_items(), key=lambda item: len(item[0]), reverse=True):
            preset_name_lower = preset_name.lower()
            if command_text_lower == preset_name_lower:
                return preset_name, ""
            if (
                command_text_lower.startswith(preset_name_lower)
                and len(command_text) > len(preset_name)
                and command_text[len(preset_name)].isspace()
            ):
                return preset_name, command_text[len(preset_name):].strip()
        return "", ""

    def _match_preset_trigger(self, text: str, allow_bare: bool = False) -> str:
        preset_name, _ = self._parse_preset_trigger(text, allow_bare=allow_bare)
        return preset_name

    def _build_preset_generation_prompt(self, preset_prompt: str, extra_rules: str = "") -> str:
        preset_prompt = str(preset_prompt or "").strip()
        extra_rules = str(extra_rules or "").strip()
        if not extra_rules:
            return preset_prompt
        if not preset_prompt:
            return extra_rules
        return f"{preset_prompt}\nAdditional requirements: {extra_rules}"

    def _parse_preset_add_payload(self, payload: str) -> tuple:
        payload = str(payload or "").strip()
        if not payload:
            return "", ""
        for separator in (":", "："):
            if separator in payload:
                preset_name, preset_prompt = payload.split(separator, 1)
                return preset_name.strip(), preset_prompt.strip()
        parts = payload.split(maxsplit=1)
        if len(parts) < 2:
            return parts[0].strip(), ""
        return parts[0].strip(), parts[1].strip()

    def _validate_preset_name(self, preset_name: str) -> str:
        preset_name = str(preset_name or "").strip()
        if not preset_name:
            return "缺少预设名。"
        if any(char in preset_name for char in (":", "：", "\r", "\n", "\t")):
            return "预设名不能包含冒号、换行或制表符。"
        if any(prefix and preset_name.startswith(prefix) for prefix in self._command_prefixes()):
            return "预设名不需要包含指令前缀。"
        if preset_name in RESERVED_PRESET_NAMES:
            return "预设名不能与「我能永远定格你」内置指令同名。"
        return ""

    def _replace_presets(self, presets: Dict[str, str]) -> None:
        normalized = [f"{name}:{prompt}" for name, prompt in presets.items()]
        self._save_page_config({"presets": normalized})

    def _upsert_preset(self, preset_name: str, preset_prompt: str) -> bool:
        with self._config_lock:
            self._refresh_from_native_config_if_changed()
            presets = dict(self._preset_items())
            existed = preset_name in presets
            presets[preset_name] = preset_prompt
            self._replace_presets(presets)
            return existed

    def _delete_preset(self, selector: str) -> str:
        with self._config_lock:
            self._refresh_from_native_config_if_changed()
            preset_name = self._find_preset_name(selector)
            if not preset_name:
                return ""
            presets = dict(self._preset_items())
            presets.pop(preset_name, None)
            self._replace_presets(presets)
            return preset_name

    def _format_reply_message(self, template: str, default: str, **values: Any) -> str:
        raw_template = str(template or "").strip() or default

        class _SafeValues(dict):
            def __missing__(self, key: str) -> str:
                return "{" + key + "}"

        safe_values = _SafeValues({key: str(value) for key, value in values.items()})
        try:
            formatted = raw_template.format_map(safe_values)
        except Exception:
            formatted = raw_template
        return formatted.strip() or default

    def _format_pending_message(self, template: str, default: str, **values: Any) -> str:
        return self._format_reply_message(template, default, **values)

    def _build_command_error_message(self, func_name: str, exc: Exception, error_kind: str = "exception") -> Optional[str]:
        func_name = str(func_name or "")
        raw_error_text = str(exc or "").strip()
        error_text = (
            self._safe_plugin_error_message(exc)
            if raw_error_text
            else ("操作超时，请稍后重试" if error_kind == "timeout" else "操作失败，请联系管理员")
        )
        error_type = type(exc).__name__ if exc is not None else ""

        if func_name in {"cmd_draw", "cmd_gif", "on_message_preset"}:
            command = {"cmd_draw": "画", "cmd_gif": "gif"}.get(func_name, "宏指令")
            return self._format_reply_message(
                self.plugin_config.draw_error_message,
                DEFAULT_DRAW_ERROR_MESSAGE,
                command=command,
                error=error_text,
                error_type=error_type,
                persona_name=getattr(self.plugin_config, "persona_name", ""),
            )

        if func_name == "cmd_selfie":
            return self._format_reply_message(
                self.plugin_config.selfie_error_message,
                DEFAULT_SELFIE_ERROR_MESSAGE,
                command="自拍",
                error=error_text,
                error_type=error_type,
                persona_name=getattr(self.plugin_config, "persona_name", ""),
            )

        return None

    def _get_active_provider(self, chain_type: str = "text2img"):
        chain = self.plugin_config.chains.get(chain_type, [])
        if chain_type == "video":
            for provider_id in chain:
                provider = self.plugin_config.get_video_provider(provider_id)
                if provider:
                    return provider
            return self.plugin_config.video_providers[0] if self.plugin_config.video_providers else None

        for provider_id in chain:
            provider = self.plugin_config.get_provider(provider_id)
            if provider:
                return provider
        return self.plugin_config.providers[0] if self.plugin_config.providers else None

    def _set_chain_config(self, chain_key: str, provider_id: str) -> bool:
        with self._config_lock:
            self._refresh_from_native_config_if_changed()
            provider = (
                self.plugin_config.get_video_provider(provider_id) if chain_key == "video"
                else self.plugin_config.get_provider(provider_id)
            )
            if not provider:
                return False
            section = "optimizer_config" if chain_key == "optimizer" else "router_config"
            self._save_page_config({section: {f"chain_{chain_key}": provider_id}})
            return True

    def _set_provider_model(self, chain_key: str, provider_id: str, selected_model: str) -> None:
        with self._config_lock:
            provider_key = "video_providers" if chain_key == "video" else "providers"
            providers = copy.deepcopy(self.raw_config.get(provider_key, []))
            for provider in providers:
                if isinstance(provider, dict) and str(provider.get("id", provider.get("节点ID", ""))) == provider_id:
                    provider["model"] = selected_model
                    if selected_model not in provider.get("available_models", []):
                        provider.setdefault("available_models", []).insert(0, selected_model)
                    self._save_page_config({provider_key: providers})
                    return

    def _switch_model_config(self, chain_key: str, target: str, model_idx: str) -> str:
        with self._config_lock:
            self._refresh_from_native_config_if_changed()
            provider = self._get_active_provider(chain_key)
            if not provider:
                return f"{MessageEmoji.WARNING} 当前 {target} 链路没有可用节点。"
            models = provider.available_models
            if not models:
                return f"{MessageEmoji.WARNING} 当前节点 ({provider.id}) 未配置可选模型。"
            if not model_idx:
                msg = f"🎛️ 节点 {provider.id} 的可用模型:\n"
                for index, model_name in enumerate(models):
                    marker = "👉" if model_name == provider.model else "  "
                    msg += f"{marker} [{index}] {model_name}\n"
                return msg + f"\n回复 /切换模型 {target} [序号或名称] 进行选择"
            selected_model = ""
            try:
                index = int(model_idx)
                if 0 <= index < len(models):
                    selected_model = models[index]
            except ValueError:
                selected_model = next((model_name for model_name in models if model_name == model_idx), "")
            if not selected_model:
                return f"{MessageEmoji.WARNING} 模型序号或名称无效。"
            self._set_provider_model(chain_key, provider.id, selected_model)
            return f"{MessageEmoji.SUCCESS} 已将 {target} 节点 ({provider.id}) 默认模型切换为: {selected_model}"

    def _find_persona_profile(self, selector: str) -> Optional[Any]:
        selector = str(selector or "").strip()
        if not selector:
            return None

        try:
            index = int(selector)
            if 1 <= index <= len(self.plugin_config.personas):
                return self.plugin_config.personas[index - 1]
            if index == 0 and self.plugin_config.personas:
                return self.plugin_config.personas[0]
        except ValueError:  # silent: 人物 selector 不是整数，按 id/名字匹配
            pass

        selector_lower = selector.lower()
        for persona in self.plugin_config.personas:
            if persona.id.lower() == selector_lower or persona.name.lower() == selector_lower:
                return persona
        for persona in self.plugin_config.personas:
            if selector_lower in persona.name.lower():
                return persona
        return None

    def _set_active_persona(self, selector: str) -> Optional[Tuple[str, int]]:
        with self._config_lock:
            self._refresh_from_native_config_if_changed()
            persona = self._find_persona_profile(selector)
            if not persona:
                return None
            self._save_page_config({"persona_config": {"active_persona_id": persona.id}})
            return self.plugin_config.persona_name, len(self.plugin_config.persona_ref_images)

    def _selfie_turn_store(self):
        store = getattr(self, "_selfie_last_ok", None)
        if not isinstance(store, dict):
            store = {}
            self._selfie_last_ok = store
        return store

    def _selfie_duplicate_notice(self, event, window=30.0):
        """同一会话 30 秒内已经成功发过自拍时，拒绝重复生成（防模型连调两次）。"""
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        last = self._selfie_turn_store().get(umo)
        if last is None:
            return ""
        if time.monotonic() - last < window:
            logger.info("[我能永远定格你] 同一轮重复调用 generate_selfie，已跳过（umo=%s）", umo[:48])
            return "系统提示：这一轮已经生成并下发过一张了，不重复生成；要换姿势请说明再发。"
        return ""

    def _mark_selfie_sent(self, event):
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        self._selfie_turn_store()[umo] = time.monotonic()
