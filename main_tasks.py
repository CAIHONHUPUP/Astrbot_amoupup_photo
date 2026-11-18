"""「我能永远定格你」任务层 mixin。"""

import asyncio
import base64
import binascii
import ipaddress
import json
import mimetypes
import os
import re
import socket
import time
import uuid
from dataclasses import replace
from urllib.parse import urljoin, urlparse
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple, TYPE_CHECKING
import aiohttp

try:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.message_components import Image, Plain, At
    from astrbot.api import logger
except ImportError:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.event.components import Image, Plain, At
    from astrbot.api.utils import logger
if TYPE_CHECKING:
    from .constants import (
        MAX_ACTIVE_BACKGROUND_TASKS,
        MAX_BACKGROUND_TASKS,
        MAX_IMAGE_BYTES,
        MAX_REFERENCE_IMAGES_PER_REQUEST,
        MAX_REMOTE_REDIRECTS,
        PLUGIN_RESULT_ERROR_MAX_LENGTH,
    )
    from .core.chain_manager import ChainRunResult
    from .utils import (
        guess_image_content_type,
        save_image_bytes,
        sniff_image_content_type,
        split_data_url,
    )
else:
    try:
        from .constants import (
            MAX_ACTIVE_BACKGROUND_TASKS,
            MAX_BACKGROUND_TASKS,
            MAX_IMAGE_BYTES,
            MAX_REFERENCE_IMAGES_PER_REQUEST,
            MAX_REMOTE_REDIRECTS,
            PLUGIN_RESULT_ERROR_MAX_LENGTH,
        )
        from .core.chain_manager import ChainRunResult
        from .utils import (
            guess_image_content_type,
            save_image_bytes,
            sniff_image_content_type,
            split_data_url,
        )
    except ImportError:
        from constants import (
            MAX_ACTIVE_BACKGROUND_TASKS,
            MAX_BACKGROUND_TASKS,
            MAX_IMAGE_BYTES,
            MAX_REFERENCE_IMAGES_PER_REQUEST,
            MAX_REMOTE_REDIRECTS,
            PLUGIN_RESULT_ERROR_MAX_LENGTH,
        )
        from core.chain_manager import ChainRunResult
        from utils import (
            guess_image_content_type,
            save_image_bytes,
            sniff_image_content_type,
            split_data_url,
        )


if TYPE_CHECKING:
    from .models import PluginConfig


class OmniDrawTaskMixin:
    """任务层：后台任务、生图/自拍/GIF 任务与素材处理。"""

    data_dir: str
    plugin_config: "PluginConfig"
    _background_tasks: Set[asyncio.Task]
    _background_task_kinds: Dict[asyncio.Task, str]
    _background_semaphore: Optional[asyncio.Semaphore]
    _background_semaphore_loop: Optional[asyncio.AbstractEventLoop]
    _lease_cache_paths: Callable[..., List[str]]
    _release_cache_paths: Callable[..., None]
    _prune_cache_if_needed: Callable[..., Dict[str, Any]]
    _send_gif_result: Callable[..., Any]
    _schedule_cache_prune: Callable[..., None]
    _extract_at_user_id: Callable[..., Any]
    _build_qq_avatar_url: Callable[..., Any]
    _message_component_has_command: Callable[..., bool]
    _refresh_from_native_config_if_changed: Callable[..., None]
    _normalize_count: Callable[..., int]
    _permission_denied_message: Callable[..., str]
    _reserve_image_quota: Callable[..., Any]
    _image_quota_error_message: Callable[..., str]
    _run_selfie_generation: Callable[..., Any]
    _run_text2img_generation: Callable[..., Any]
    _release_image_quota: Callable[..., None]
    _finalize_image_quota: Callable[..., None]
    _record_generated_images: Callable[..., Any]

    def _create_background_task(
        self,
        coro: Any,
        kind: str = "generic",
        leased_paths: Optional[Iterable[str]] = None,
    ) -> asyncio.Task:
        leases = self._lease_cache_paths(leased_paths or [])
        if len(self._background_tasks) >= MAX_BACKGROUND_TASKS:
            self._release_cache_paths(leases)
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            raise RuntimeError("后台任务队列已满，请稍后再试。")

        loop = asyncio.get_running_loop()
        if self._background_semaphore is None or self._background_semaphore_loop is not loop:
            self._background_semaphore = asyncio.Semaphore(MAX_ACTIVE_BACKGROUND_TASKS)
            self._background_semaphore_loop = loop

        lifecycle = {"coroutine_started": False, "finalized": False}

        def finalize_lifecycle() -> None:
            if lifecycle["finalized"]:
                return
            lifecycle["finalized"] = True
            if not lifecycle["coroutine_started"]:
                close = getattr(coro, "close", None)
                if callable(close):
                    close()
            self._release_cache_paths(leases)

        semaphore = self._background_semaphore
        assert semaphore is not None

        async def run_limited() -> None:
            try:
                if kind == "scheduler":
                    lifecycle["coroutine_started"] = True
                    await coro
                    return
                async with semaphore:
                    lifecycle["coroutine_started"] = True
                    await coro
            finally:
                finalize_lifecycle()

        task = asyncio.create_task(run_limited())
        self._background_tasks.add(task)
        self._background_task_kinds[task] = str(kind or "generic")

        def _cleanup(done_task: asyncio.Task) -> None:
            finalize_lifecycle()
            self._background_tasks.discard(done_task)
            self._background_task_kinds.pop(done_task, None)
            if done_task.cancelled():
                return
            try:
                exc = done_task.exception()
            except asyncio.CancelledError:
                return
            if exc:
                logger.error(
                    f"[我能永远定格你] 后台任务异常退出: {exc}",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

        task.add_done_callback(_cleanup)
        return task

    async def terminate(self):
        if not self._background_tasks:
            return
        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        self._background_task_kinds.clear()
        self._background_semaphore = None
        self._background_semaphore_loop = None
        self._cache_cleanup_task = None
        logger.info("[我能永远定格你] 已取消所有后台视频任务。")

    async def _materialize_gif_result(
        self, result: Any, session: Optional[aiohttp.ClientSession] = None,
        leased_paths: Optional[List[str]] = None,
    ) -> Any:
        """Keep original GIF bytes in a bounded local file, including extensionless URLs."""
        image_url = self._get_image_result_url(result)
        if not image_url:
            return result
        if image_url.startswith("data:"):
            if len(image_url) > (MAX_IMAGE_BYTES * 4 // 3) + 4096:
                raise ValueError("GIF 超过 20MB 大小限制。")
            image_bytes, _ = await asyncio.to_thread(split_data_url, image_url)
        elif image_url.startswith(("http://", "https://")):
            if session is None:
                async with aiohttp.ClientSession() as download_session:
                    return await self._materialize_gif_result(result, download_session, leased_paths)
            image_bytes, _, _ = await self._download_remote_image(
                session, image_url, {"User-Agent": "OmniDraw/3.4"},
            )
        else:
            owned_leases = self._lease_cache_paths([image_url])
            try:
                path = self._validate_local_image_path(image_url, allowed_root=self.data_dir)

                def read_header():
                    with open(path, "rb") as image_file:
                        return image_file.read(6)

                if sniff_image_content_type(await asyncio.to_thread(read_header)) != "image/gif":
                    raise ValueError("节点未返回真实 GIF，已停止发送，未降级为静态图片。")
                if leased_paths is not None:
                    leased_paths.extend(owned_leases)
                    owned_leases = []
                return result
            finally:
                self._release_cache_paths(owned_leases)
        if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
            raise ValueError("GIF 为空或超过 20MB 大小限制。")
        if sniff_image_content_type(image_bytes) != "image/gif":
            raise ValueError("节点未返回真实 GIF，已停止发送，未降级为静态图片。")
        path = os.path.abspath(os.path.join(
            self.data_dir, "temp_images", f"gif_{int(time.time() * 1000)}_0_{uuid.uuid4().hex[:8]}.gif",
        ))
        # Lease before the worker creates the file; a cancelled worker never acquires a late lease.
        owned_leases = self._lease_cache_paths([path])
        try:
            await asyncio.to_thread(self._write_gif_file, path, image_bytes)
            materialized = replace(result, image_url=path) if isinstance(result, ChainRunResult) else path
            if leased_paths is not None:
                leased_paths.extend(owned_leases)
                owned_leases = []
            else:
                self._prune_cache_if_needed("temp_images", protected_paths=[path])
            return materialized
        finally:
            self._release_cache_paths(owned_leases)

    def _is_gif_result(self, result: Any) -> bool:
        image_url = self._get_image_result_url(result)
        return (
            getattr(result, "output_format", "") == "gif"
            or guess_image_content_type(image_url, fallback="") == "image/gif"
        )

    def _write_gif_file(self, path: str, image_bytes: bytes) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "xb") as image_file:
            image_file.write(image_bytes)

    async def _command_image_delivery(
        self, event: AstrMessageEvent, result: Any, elapsed_seconds: float,
        gif_requested: bool = False,
    ) -> Optional[Any]:
        if gif_requested or self._is_gif_result(result):
            leased_paths: List[str] = []
            try:
                result = await self._materialize_gif_result(result, leased_paths=leased_paths)
                self._prune_cache_if_needed("temp_images", protected_paths=leased_paths)
                if callable(getattr(event, "send", None)):
                    await self._send_gif_result(event, result, elapsed_seconds, include_metadata=True)
                    return None
            finally:
                self._release_cache_paths(leased_paths)
        return event.chain_result(self._build_image_success_components(result, elapsed_seconds))

    def _get_image_result_url(self, result: Any) -> str:
        if isinstance(result, ChainRunResult):
            return result.image_url
        return result if isinstance(result, str) else ""

    def _build_image_success_components(
        self,
        result: Any,
        elapsed_seconds: Optional[float] = None,
        include_metadata: bool = True,
    ) -> List[Any]:
        image_url = self._get_image_result_url(result)
        components: List[Any] = []
        if include_metadata and isinstance(result, ChainRunResult):
            display_elapsed = elapsed_seconds if elapsed_seconds is not None else result.elapsed_seconds
            lines = self._generation_metadata_lines(display_elapsed, result.model)
            if lines:
                components.append(Plain("\n".join(lines) + "\n"))
        components.append(self._create_image_component(image_url))
        return components

    def _create_image_component(self, image_url: str) -> Image:
        if image_url.startswith("data:image"):
            image_bytes, content_type = split_data_url(image_url)
            save_dir = os.path.join(self.data_dir, "temp_images")
            file_path = save_image_bytes(image_bytes, save_dir, image_url, "img", 0, content_type)
            self._schedule_cache_prune("temp_images", [file_path])
            return Image.fromFileSystem(file_path)
        if image_url.startswith("http"):
            return Image.fromURL(image_url)
        return Image.fromFileSystem(os.path.abspath(image_url))

    async def _send_generated_images(
        self,
        event: AstrMessageEvent,
        results: Iterable[Any],
        elapsed_seconds: Optional[float] = None,
        include_metadata: bool = False,
    ) -> int:
        sent = 0
        for result in results:
            if not self._get_image_result_url(result):
                continue
            if self._is_gif_result(result):
                await self._send_gif_result(event, result, elapsed_seconds, include_metadata)
            else:
                await event.send(event.chain_result(
                    self._build_image_success_components(result, elapsed_seconds, include_metadata=include_metadata)
                ))
            sent += 1
            await asyncio.sleep(0.5)
        return sent

    def _format_generation_elapsed(self, elapsed_seconds: Optional[float]) -> str:
        try:
            seconds = max(0.0, float(elapsed_seconds or 0.0))
        except (TypeError, ValueError):
            seconds = 0.0
        return f"{seconds:.1f}s"

    def _generation_metadata_lines(
        self,
        elapsed_seconds: Optional[float] = None,
        model: str = "",
    ) -> List[str]:
        lines = []
        if getattr(self.plugin_config, "show_generation_time", False) and elapsed_seconds is not None:
            lines.append(f"⏱️ 生图耗时：{self._format_generation_elapsed(elapsed_seconds)}")
        if getattr(self.plugin_config, "show_request_model", False) and str(model or "").strip():
            lines.append(f"🤖 请求模型：{str(model).strip()}")
        return lines

    def _get_event_images(
        self,
        event: AstrMessageEvent,
        include_at_avatars: bool = False,
        at_after_command: str = "",
    ) -> List[str]:
        images = []
        visited = set()
        allow_at_avatar = not bool(at_after_command)

        def _search(obj: Any) -> None:
            nonlocal allow_at_avatar
            if obj is None or id(obj) in visited:
                return
            visited.add(id(obj))
            obj_type = type(obj).__name__

            if obj_type == "Image":
                path = getattr(obj, "path", getattr(obj, "file", getattr(obj, "file_path", None)))
                url = getattr(obj, "url", None)
                ref = path if path and not str(path).startswith("http") else url
                if ref:
                    images.append(str(ref))
                return

            if include_at_avatars and allow_at_avatar and (obj_type == "At" or isinstance(obj, At)):
                _at_uid = self._extract_at_user_id(obj)
                try:
                    _self_id = str(event.get_self_id() or "")
                except Exception:
                    _self_id = ""
                # [patched] 跳过 @ 机器人自己，否则唤醒用的 @ 会把 bot 头像当成参考图
                if _self_id and str(_at_uid) == _self_id:
                    return
                avatar_url = self._build_qq_avatar_url(_at_uid)
                if avatar_url:
                    images.append(avatar_url)
                return

            if obj_type == "Plain":
                if self._message_component_has_command(obj, at_after_command):
                    allow_at_avatar = True
                text = getattr(obj, "text", "")
                if text and str(text).startswith("data:image"):
                    images.append(str(text))
                return

            if isinstance(obj, (list, tuple)):
                for item in obj:
                    _search(item)
                return

            attrs = []
            if hasattr(obj, "__dict__"):
                attrs.extend(vars(obj).keys())
            if hasattr(obj, "__slots__"):
                attrs.extend(obj.__slots__)
            blocked = {"context", "star", "bot", "provider", "session", "config", "plugin_config"}
            for key in set(attrs) - blocked:
                try:
                    _search(getattr(obj, key))
                except Exception:
                    continue

        _search(getattr(event, "message_obj", None))
        quote_obj = getattr(getattr(event, "message_obj", None), "quote", None)
        if quote_obj:
            _search(quote_obj)

        seen: set[Any] = set()
        unique_images: List[str] = []
        for item in images:
            if item in seen:
                continue
            seen.add(item)
            unique_images.append(item)
        return unique_images

    async def _read_response_limited(self, response: aiohttp.ClientResponse, limit: int = MAX_IMAGE_BYTES) -> bytes:
        chunks = []
        total = 0
        async for chunk in response.content.iter_chunked(64 * 1024):
            total += len(chunk)
            if total > limit:
                raise ValueError(f"图片超过大小限制 {limit // 1024 // 1024}MB")
            chunks.append(chunk)
        return b"".join(chunks)

    def _looks_like_image_bytes(self, image_bytes: bytes) -> bool:
        if not image_bytes:
            return False
        if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            return True
        if image_bytes.startswith(b"\xff\xd8\xff"):
            return True
        if image_bytes.startswith((b"GIF87a", b"GIF89a", b"BM")):
            return True
        if image_bytes.startswith((b"II*\x00", b"MM\x00*")):
            return True
        if len(image_bytes) >= 12 and image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
            return True
        if (
            len(image_bytes) >= 12
            and image_bytes[4:8] == b"ftyp"
            and image_bytes[8:12] in {b"avif", b"avis"}
        ):
            return True
        return False

    def _is_path_within_root(self, path: str, root: str) -> bool:
        try:
            real_path = os.path.realpath(os.path.abspath(path))
            real_root = os.path.realpath(os.path.abspath(root))
            return os.path.commonpath([real_root, real_path]) == real_root
        except (OSError, ValueError):
            return False

    def _validate_local_image_path(self, path: str, allowed_root: str = "") -> str:
        abs_path = os.path.abspath(str(path or ""))
        if not abs_path or not os.path.isfile(abs_path) or os.path.islink(abs_path):
            raise ValueError("本地参考图必须是可读取的普通文件")
        if allowed_root:
            if not self._is_path_within_root(abs_path, allowed_root):
                raise ValueError("本地参考图不在允许目录内")
            if not self._is_path_within_root(os.path.realpath(abs_path), os.path.realpath(allowed_root)):
                raise ValueError("本地参考图不在允许目录内")
        current = os.path.abspath(allowed_root or os.path.dirname(abs_path))
        try:
            relative = os.path.relpath(abs_path, current)
            for component in relative.split(os.sep):
                if component in {"", "."}:
                    continue
                current = os.path.join(current, component)
                if os.path.islink(current):
                    raise ValueError("本地参考图路径不允许包含符号链接")
        except ValueError:
            raise
        if os.path.getsize(abs_path) > MAX_IMAGE_BYTES:
            raise ValueError(f"图片超过大小限制 {MAX_IMAGE_BYTES // 1024 // 1024}MB")
        with open(abs_path, "rb") as file:
            header = file.read(32)
        if not self._looks_like_image_bytes(header):
            raise ValueError("本地参考文件不是受支持的图片")
        return abs_path

    def _validate_remote_image_url(self, image_url: str) -> None:
        parsed = urlparse(str(image_url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("参考图 URL 仅支持 http/https")
        if parsed.username or parsed.password:
            raise ValueError("参考图 URL 不允许包含用户凭据")

        try:
            addresses = socket.getaddrinfo(
                parsed.hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise ValueError(f"参考图域名解析失败: {exc}") from exc

        resolved_ips = {item[4][0] for item in addresses if item and item[4]}
        if not resolved_ips:
            raise ValueError("参考图域名没有可用地址")
        for value in resolved_ips:
            try:
                if not ipaddress.ip_address(value).is_global:
                    raise ValueError("参考图 URL 不允许访问本机、私网或链路本地地址")
            except ValueError as exc:
                if "不允许访问" in str(exc):
                    raise
                raise ValueError("参考图域名解析结果无效") from exc

    async def _download_remote_image(
        self,
        session: aiohttp.ClientSession,
        image_url: str,
        headers: Dict[str, str],
    ) -> Tuple[bytes, str, str]:
        current_url = str(image_url or "").strip()
        for redirect_count in range(MAX_REMOTE_REDIRECTS + 1):
            await asyncio.to_thread(self._validate_remote_image_url, current_url)
            async with session.get(
                current_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=False,
            ) as response:
                if 300 <= response.status < 400:
                    location = str(response.headers.get("Location", "") or "").strip()
                    if not location or redirect_count >= MAX_REMOTE_REDIRECTS:
                        raise ValueError("参考图重定向次数过多或缺少目标地址")
                    current_url = urljoin(current_url, location)
                    continue
                if response.status != 200:
                    raise ValueError(f"参考图下载失败，状态码 {response.status}")
                image_bytes = await self._read_response_limited(response)
                if not self._looks_like_image_bytes(image_bytes[:32]):
                    raise ValueError("远程内容不是受支持的图片")
                return image_bytes, response.headers.get("Content-Type", ""), current_url
        raise ValueError("参考图重定向次数过多")

    async def _process_and_save_images(
        self,
        raw_images: Iterable[str],
        session: Optional[aiohttp.ClientSession] = None,
        allow_local_refs: bool = False,
    ) -> List[str]:
        processed_paths: List[str] = []
        if not raw_images:
            return processed_paths

        raw_images = list(raw_images)
        if len(raw_images) > MAX_REFERENCE_IMAGES_PER_REQUEST:
            raise ValueError(f"参考图最多允许 {MAX_REFERENCE_IMAGES_PER_REQUEST} 张")

        save_dir = os.path.join(self.data_dir, "user_refs")
        os.makedirs(save_dir, exist_ok=True)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        session_obj = session
        session_owner = False
        try:
            for idx, img_ref in enumerate(raw_images):
                if not img_ref:
                    continue
                img_ref = str(img_ref)
                lowered_ref = img_ref.lower()

                if lowered_ref.startswith("data:image"):
                    try:
                        max_data_url_chars = (MAX_IMAGE_BYTES * 4 // 3) + 4096
                        if len(img_ref) > max_data_url_chars:
                            raise ValueError("Base64 图片超过大小限制")
                        decoded, content_type = split_data_url(img_ref)
                        if len(decoded) > MAX_IMAGE_BYTES:
                            raise ValueError("Base64 图片超过大小限制")
                        if not self._looks_like_image_bytes(decoded[:32]):
                            raise ValueError("Base64 内容不是受支持的图片")
                        file_path = await asyncio.to_thread(
                            save_image_bytes,
                            decoded,
                            save_dir,
                            img_ref,
                            "ref",
                            idx,
                            content_type,
                        )
                        processed_paths.append(file_path)
                    except (IndexError, ValueError, binascii.Error, OSError) as exc:
                        logger.warning(f"[我能永远定格你] Base64 参考图处理失败: {exc}")
                    continue

                if not lowered_ref.startswith(("http://", "https://")):
                    try:
                        # [2026-11-16] 事件/命令带来的本地图只允许落在插件 data_dir 内；
                        # 只有受信任的插件 API（allow_local_refs=True，如 amo 的 plugin_data）放行。
                        processed_paths.append(
                            self._validate_local_image_path(
                                img_ref,
                                allowed_root="" if allow_local_refs else self.data_dir,
                            )
                        )
                    except (OSError, ValueError) as exc:
                        logger.warning(f"[我能永远定格你] 本地参考图处理失败: {exc}")
                    continue

                if session_obj is None:
                    session_obj = aiohttp.ClientSession()
                    session_owner = True

                for attempt in range(1, 4):
                    try:
                        img_data, content_type, final_url = await self._download_remote_image(
                            session_obj,
                            img_ref,
                            headers,
                        )
                        file_path = await asyncio.to_thread(
                            save_image_bytes,
                            img_data,
                            save_dir,
                            final_url,
                            "ref",
                            idx,
                            content_type,
                        )
                        processed_paths.append(file_path)
                        break
                    except Exception as exc:
                        if attempt == 3:
                            parsed = urlparse(img_ref)
                            safe_source = f"{parsed.scheme}://{parsed.hostname or ''}{parsed.path}"
                            logger.warning(f"[我能永远定格你] 参考图下载失败: {safe_source} ({exc})")
                        else:
                            await asyncio.sleep(1)

            await asyncio.to_thread(lambda: self._prune_cache_if_needed("user_refs", protected_paths=processed_paths))
        finally:
            if session_owner and session_obj is not None:
                await session_obj.close()
        return processed_paths

    async def generate_images_for_plugin(
        self,
        prompt: str,
        count: int = 1,
        aspect_ratio: str = "",
        size: str = "",
        extra_params: str = "",
        refs: Any = None,
        mode: str = "",
        event: Optional[AstrMessageEvent] = None,
        record_usage: bool = False,
        allow_local_refs: bool = False,
        use_gif: bool = False,
        frame_count: int = 9,
        frame_duration_ms: int = 200,
        scene_lock: str = "",
        pose_lock: str = "",
        negative_prompt: str = "",
        skip_outfit_lock: bool = False,
        pose_ref: Any = None,
    ) -> Dict[str, Any]:
        """Generate image(s) for another plugin and return serializable image data.

        This method deliberately does not call event.send(). Existing chat commands
        and LLM tools keep their original "generate and send" behavior.
        """
        self._refresh_from_native_config_if_changed()
        prompt = str(prompt or "").strip()
        raw_refs = self._validated_plugin_image_refs(refs, allow_local_refs=allow_local_refs)
        generation_mode = self._plugin_generation_mode(mode)
        if not prompt and not raw_refs and generation_mode != "selfie":
            return {"success": False, "message": "缺少 prompt 或 refs。", "images": []}

        count = self._normalize_count(count)
        reserved_count = 0
        generation = {}
        if event is not None:
            permission_error = self._permission_denied_message(event)
            if permission_error:
                return {"success": False, "message": permission_error, "images": []}
            if record_usage:
                quota_error, reserved_count = self._reserve_image_quota(event, count)
            else:
                quota_error = self._image_quota_error_message(event, count)
            if quota_error:
                return {"success": False, "message": quota_error, "images": []}

        try:
            if generation_mode == "selfie":
                generation = await self._run_selfie_generation(
                    event,
                    prompt,
                    count,
                    aspect_ratio=aspect_ratio,
                    size=size,
                    extra_params=extra_params,
                    refs=raw_refs,
                    use_gif=use_gif,
                    frame_count=frame_count,
                    frame_duration_ms=frame_duration_ms,
                    scene_lock=scene_lock,
                    pose_lock=pose_lock,
                    negative_prompt=negative_prompt,
                    allow_local_refs=allow_local_refs,
                    skip_outfit_lock=skip_outfit_lock,
                    pose_ref=pose_ref,
                )
            else:
                generation = await self._run_text2img_generation(
                    event,
                    prompt or "根据参考图生成一张自然、清晰、符合原图语义的图片。",
                    count,
                    aspect_ratio=aspect_ratio,
                    size=size,
                    extra_params=extra_params,
                    refs=raw_refs,
                    use_gif=use_gif,
                    frame_count=frame_count,
                    frame_duration_ms=frame_duration_ms,
                    scene_lock=scene_lock,
                    pose_lock=pose_lock,
                    negative_prompt=negative_prompt,
                    allow_local_refs=allow_local_refs,
                )

            response, valid_results = self._plugin_generation_response(generation)
            if not response.get("success"):
                if event is not None and record_usage and reserved_count:
                    self._release_image_quota(event, reserved_count)
                return response

            if event is not None and record_usage:
                if reserved_count:
                    self._finalize_image_quota(event, reserved_count, len(valid_results))
                else:
                    self._record_generated_images(event, len(valid_results))
            return response
        except asyncio.CancelledError:
            if event is not None and record_usage and reserved_count:
                self._release_image_quota(event, reserved_count)
            raise
        except Exception:
            if event is not None and record_usage and reserved_count:
                self._release_image_quota(event, reserved_count)
            raise
        finally:
            if generation.get("leased_result_paths"):
                self._release_cache_paths(generation["leased_result_paths"])

    def _plugin_generation_response(self, generation: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Any]]:
        images = []
        valid_results = []
        errors = []
        prompts = list(generation.get("prompts", []) or [])
        for index, result in enumerate(generation.get("results", []) or [], start=1):
            result_prompt = str(prompts[index - 1]) if index <= len(prompts) else ""
            if isinstance(result, Exception):
                errors.append(self._safe_plugin_error_message(result))
                continue
            if not self._get_image_result_url(result):
                errors.append(f"empty image result at index {index}")
                continue
            valid_results.append(result)
            images.append(self._serialize_plugin_image_result(result, index, result_prompt))

        if not images:
            message = "所有绘图节点请求失败。"
            if errors:
                message = f"{message} {'; '.join(errors)}"
            return {"success": False, "message": message, "images": [], "errors": errors}, []

        response = {
            "success": True,
            "message": f"已成功生成 {len(images)} 张图片。",
            "images": images,
            "count": len(images),
            "requested_count": generation.get("requested_count", len(images)),
            "ref_count": len(generation.get("raw_refs", [])),
            "processed_ref_count": len(generation.get("safe_refs", [])),
            "mode": generation.get("mode", "text2img"),
            "chain": generation.get("chain", generation.get("mode", "text2img")),
        }
        if errors:
            response["errors"] = errors
        return response, valid_results

    def _plugin_generation_mode(self, mode: Any) -> str:
        mode_text = str(mode or "").strip().lower()
        if mode_text in {"selfie", "persona", "persona_selfie", "portrait", "自拍", "人设自拍"}:
            return "selfie"
        return "text2img"

    def _normalize_plugin_prompts(self, prompts: Any, fallback_prompt: str, count: int) -> List[str]:
        fallback_prompt = str(fallback_prompt or "").strip()
        prompt_list = [
            str(prompt or "").strip() or fallback_prompt
            for prompt in (prompts or [])
        ]
        target_count = max(1, int(count or 1))
        if len(prompt_list) < target_count:
            prompt_list.extend([fallback_prompt] * (target_count - len(prompt_list)))
        return prompt_list[:target_count]

    def _as_plugin_image_refs(self, refs: Any) -> List[str]:
        refs_iterable: Iterable[Any]
        if refs is None:
            return []
        if isinstance(refs, str):
            refs_text = refs.strip()
            if not refs_text:
                return []
            if refs_text.startswith(("[", "{")):
                try:
                    return self._as_plugin_image_refs(json.loads(refs_text))
                except Exception:  # silent: refs 不是 JSON 时按字符串/换行继续解析
                    pass
            if refs_text.startswith("data:image"):
                return [refs_text]
            refs_iterable = re.split(r"[\r\n]+", refs_text)
        elif isinstance(refs, (list, tuple, set)):
            refs_iterable = refs
        else:
            refs_iterable = [refs]

        normalized = []
        for ref in refs_iterable:
            if isinstance(ref, dict):
                ref = (
                    ref.get("image_url")
                    or ref.get("data_url")
                    or ref.get("url")
                    or ref.get("file_path")
                    or ref.get("path")
                    or ""
                )
            ref_text = str(ref or "").strip()
            if ref_text:
                normalized.append(ref_text)
        seen: set[str] = set()
        unique_refs: List[str] = []
        for ref in normalized:
            if ref in seen:
                continue
            seen.add(ref)
            unique_refs.append(ref)
        return unique_refs

    def _validated_plugin_image_refs(self, refs: Any, allow_local_refs: bool = False) -> List[str]:
        normalized = self._as_plugin_image_refs(refs)
        if len(normalized) > MAX_REFERENCE_IMAGES_PER_REQUEST:
            raise ValueError(f"参考图最多允许 {MAX_REFERENCE_IMAGES_PER_REQUEST} 张")

        validated = []
        for ref in normalized:
            lowered = ref.lower()
            if lowered.startswith("data:image") or lowered.startswith(("http://", "https://")):
                validated.append(ref)
                continue
            allowed_root = "" if allow_local_refs else self.data_dir
            validated.append(self._validate_local_image_path(ref, allowed_root=allowed_root))
        return validated

    def _plugin_bool(self, value: Any, default: bool = True) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "启用", "是"}:
            return True
        if text in {"0", "false", "no", "n", "off", "禁用", "否"}:
            return False
        return default

    def _unwrap_tool_event(self, event: Any) -> Any:
        wrapped = getattr(getattr(event, "context", None), "event", None)
        return wrapped if wrapped is not None else event

    def _generation_failure_message(self, generation: Dict[str, Any]) -> str:
        errors = [
            self._safe_plugin_error_message(result)
            for result in generation.get("results", []) if isinstance(result, Exception)
        ]
        return "所有绘图节点请求失败" + (f"：{'；'.join(errors[:3])}" if errors else "")

    def _safe_plugin_error_message(self, exc: Any) -> str:
        text = str(exc or "").strip() or "未知错误"
        text = re.sub(
            r"(?i)([?&](?:api[_-]?key|key|access[_-]?token|token|secret|password)=)[^&#\s]+",
            r"\1<redacted>",
            text,
        )
        text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}", r"\1<redacted>", text)
        text = re.sub(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{7,}\b", "<redacted>", text)
        text = re.sub(
            r"(?i)\b(api[\s_-]*key|access[\s_-]*token|token|secret|authorization|password)\b"
            r"\s*[:=]\s*['\"]?[^'\"\s,;})]+",
            lambda match: f"{match.group(1)}=<redacted>",
            text,
        )
        text = re.sub(r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=]+", "<image_data_url>", text)
        if len(text) > PLUGIN_RESULT_ERROR_MAX_LENGTH:
            text = text[: PLUGIN_RESULT_ERROR_MAX_LENGTH - 3].rstrip() + "..."
        return text

    def _serialize_plugin_image_result(
        self,
        result: Any,
        index: int,
        prompt: str = "",
    ) -> Dict[str, Any]:
        image_url = self._get_image_result_url(result)
        source_type = "file"
        url = ""
        data_url = ""
        file_path = ""
        content_type = ""

        if image_url.startswith("data:image"):
            source_type = "data_url"
            data_url = image_url
            try:
                image_bytes, content_type = split_data_url(image_url)
                content_type = sniff_image_content_type(image_bytes, content_type)
                save_dir = os.path.join(self.data_dir, "temp_images")
                file_path = save_image_bytes(image_bytes, save_dir, image_url, "plugin", index, content_type)
                self._schedule_cache_prune("temp_images", [file_path])
            except Exception as exc:
                logger.warning(f"[我能永远定格你] 插件生图结果落盘失败: {exc}")
        elif image_url.startswith("http"):
            source_type = "url"
            url = image_url
            content_type = guess_image_content_type(image_url, fallback="")
        else:
            file_path = os.path.abspath(image_url)
            content_type = mimetypes.guess_type(file_path)[0] or ""
            if content_type == "image/gif":
                self._validate_local_image_path(file_path, allowed_root=self.data_dir)
                with open(file_path, "rb") as gif_file:
                    data_url = "data:image/gif;base64," + base64.b64encode(gif_file.read(MAX_IMAGE_BYTES + 1)).decode("ascii")

        return {
            "index": index,
            "image_url": image_url,
            "source_type": source_type,
            "url": url,
            "file_path": file_path,
            "data_url": data_url,
            "content_type": content_type,
            "output_format": "gif" if content_type == "image/gif" else getattr(result, "output_format", ""),
            "provider_id": getattr(result, "provider_id", ""),
            "model": getattr(result, "model", ""),
            "elapsed_seconds": getattr(result, "elapsed_seconds", None),
            "prompt": prompt,
        }

    def _load_locked_outfit(self) -> str:
        """读 amo 写入的今日穿搭（data_dir/amo_outfit.json），当天自拍都锁这一套。

        文件不是今天的、或没有 text 就返回空串，完全不影响原来的自拍流程。
        """
        path = os.path.join(self.data_dir, "amo_outfit.json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return ""
        except Exception:
            return ""
        if not isinstance(data, dict):
            return ""
        text = str(data.get("text") or "").strip()
        day = str(data.get("day") or "").strip()
        if not text:
            return ""
        if day and day != time.strftime("%Y-%m-%d"):
            return ""
        return text[:200]

    def _negative_prompt_for(self, prompt: str, scene_type: str = "") -> str:
        from .core.negative_prompt import get_negative_prompt
        return get_negative_prompt(self.plugin_config, scene_type, prompt)
