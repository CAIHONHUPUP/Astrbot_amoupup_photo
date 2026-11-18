"""「我能永远定格你」缓存层 mixin。"""

import asyncio
import os
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, TYPE_CHECKING

try:
    from astrbot.api import logger
except ImportError:
    from astrbot.api.utils import logger
if TYPE_CHECKING:
    from .constants import (
        CACHE_DIR_NAMES,
        CACHE_IMAGE_EXTENSIONS,
        DEFAULT_CACHE_CLEANUP_INTERVAL_HOURS,
        DEFAULT_MAX_CACHE_SIZE_MB,
    )
else:
    try:
        from .constants import (
            CACHE_DIR_NAMES,
            CACHE_IMAGE_EXTENSIONS,
            DEFAULT_CACHE_CLEANUP_INTERVAL_HOURS,
            DEFAULT_MAX_CACHE_SIZE_MB,
        )
    except ImportError:
        from constants import (
            CACHE_DIR_NAMES,
            CACHE_IMAGE_EXTENSIONS,
            DEFAULT_CACHE_CLEANUP_INTERVAL_HOURS,
            DEFAULT_MAX_CACHE_SIZE_MB,
        )


if TYPE_CHECKING:
    from .models import PluginConfig


class OmniDrawCacheMixin:
    """缓存层：图片缓存统计、租约、清理与定时维护。"""

    data_dir: str
    plugin_config: "PluginConfig"
    _cache_lease_lock: Any
    _cache_leases: Dict[str, int]
    _to_nonnegative_int: Callable[..., int]
    _create_background_task: Callable[..., asyncio.Task]

    def _cache_dir_paths(self) -> Dict[str, str]:
        return {name: os.path.abspath(os.path.join(self.data_dir, name)) for name in CACHE_DIR_NAMES}

    def _lease_cache_paths(self, paths: Iterable[str]) -> List[str]:
        leased = []
        with self._cache_lease_lock:
            for path in paths or []:
                abs_path = os.path.abspath(str(path or ""))
                if not abs_path:
                    continue
                self._cache_leases[abs_path] = self._cache_leases.get(abs_path, 0) + 1
                leased.append(abs_path)
        return leased

    def _release_cache_paths(self, paths: Iterable[str]) -> None:
        with self._cache_lease_lock:
            for path in paths or []:
                abs_path = os.path.abspath(str(path or ""))
                current = self._cache_leases.get(abs_path, 0)
                if current > 1:
                    self._cache_leases[abs_path] = current - 1
                else:
                    self._cache_leases.pop(abs_path, None)

    def _leased_cache_paths(self) -> set:
        with self._cache_lease_lock:
            return {path for path, count in self._cache_leases.items() if count > 0}

    def _is_cache_image_file(self, file_path: str, root_path: str) -> bool:
        abs_file = os.path.abspath(file_path)
        abs_root = os.path.abspath(root_path)
        try:
            common = os.path.commonpath([abs_root, abs_file])
        except ValueError:
            return False
        if common != abs_root:
            return False
        return os.path.splitext(abs_file)[1].lower() in CACHE_IMAGE_EXTENSIONS

    def _iter_cache_image_files(self) -> List[Dict[str, Any]]:
        files = []
        for cache_name, root_path in self._cache_dir_paths().items():
            if not os.path.isdir(root_path):
                continue
            for current_root, _, filenames in os.walk(root_path):
                abs_current_root = os.path.abspath(current_root)
                try:
                    if os.path.commonpath([root_path, abs_current_root]) != root_path:
                        continue
                except ValueError:
                    continue
                for filename in filenames:
                    file_path = os.path.abspath(os.path.join(abs_current_root, filename))
                    if not self._is_cache_image_file(file_path, root_path):
                        continue
                    try:
                        stat = os.stat(file_path)
                    except OSError:
                        continue
                    if not os.path.isfile(file_path):
                        continue
                    files.append(
                        {
                            "cache_name": cache_name,
                            "path": file_path,
                            "bytes": max(0, int(stat.st_size)),
                            "mtime": float(stat.st_mtime),
                        }
                    )
        return files

    def _format_bytes(self, size: int) -> str:
        value = float(max(0, int(size or 0)))
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024
        return f"{value:.1f} GB"

    def _cache_stats_for_page(self) -> Dict[str, Any]:
        dir_paths = self._cache_dir_paths()
        dir_stats: Dict[str, Dict[str, Any]] = {
            name: {
                "count": 0,
                "bytes": 0,
                "human_size": "0 B",
            }
            for name, path in dir_paths.items()
        }

        files = self._iter_cache_image_files()
        for item in files:
            stats = dir_stats.get(item["cache_name"])
            if not stats:
                continue
            stats["count"] += 1
            stats["bytes"] += item["bytes"]

        total_bytes = 0
        total_count = 0
        for stats in dir_stats.values():
            total_bytes += stats["bytes"]
            total_count += stats["count"]
            stats["human_size"] = self._format_bytes(stats["bytes"])

        return {
            "dirs": dir_stats,
            "total": {
                "count": total_count,
                "bytes": total_bytes,
                "human_size": self._format_bytes(total_bytes),
            },
            "targets": list(CACHE_DIR_NAMES),
            "image_extensions": sorted(CACHE_IMAGE_EXTENSIONS),
            "scanned_at": int(time.time()),
        }

    def _delete_cache_files(
        self,
        files: Iterable[Dict[str, Any]],
        reason: str = "manual",
        protected_paths: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        dir_paths = self._cache_dir_paths()
        protected = {os.path.abspath(path) for path in (protected_paths or []) if path}
        protected.update(self._leased_cache_paths())
        result: Dict[str, Any] = {
            "reason": reason,
            "deleted_count": 0,
            "deleted_bytes": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "human_deleted_size": "0 B",
            "dirs": {
                name: {"deleted_count": 0, "deleted_bytes": 0, "human_deleted_size": "0 B"}
                for name in CACHE_DIR_NAMES
            },
            "failed": [],
        }

        for item in files:
            cache_name = str(item.get("cache_name", ""))
            file_path = os.path.abspath(str(item.get("path", "")))
            root_path = dir_paths.get(cache_name)
            if not file_path or not root_path or file_path in protected:
                result["skipped_count"] += 1
                continue
            if not self._is_cache_image_file(file_path, root_path):
                result["skipped_count"] += 1
                continue
            try:
                file_size = os.path.getsize(file_path) if os.path.exists(file_path) else int(item.get("bytes", 0))
                with self._cache_lease_lock:
                    if self._cache_leases.get(file_path, 0) > 0:
                        result["skipped_count"] += 1
                        continue
                    os.remove(file_path)
                result["deleted_count"] += 1
                result["deleted_bytes"] += max(0, int(file_size))
                dir_result = result["dirs"].setdefault(
                    cache_name,
                    {"deleted_count": 0, "deleted_bytes": 0, "human_deleted_size": "0 B"},
                )
                dir_result["deleted_count"] += 1
                dir_result["deleted_bytes"] += max(0, int(file_size))
            except OSError as exc:
                result["failed_count"] += 1
                result["failed"].append({"path": file_path, "error": str(exc)})

        result["human_deleted_size"] = self._format_bytes(result["deleted_bytes"])
        for dir_result in result["dirs"].values():
            dir_result["human_deleted_size"] = self._format_bytes(dir_result["deleted_bytes"])
        return result

    def _clear_cache_images(self, reason: str = "manual") -> Dict[str, Any]:
        before = self._cache_stats_for_page()
        result = self._delete_cache_files(self._iter_cache_image_files(), reason=reason)
        result["before"] = before["total"]
        result["after"] = self._cache_stats_for_page()["total"]
        logger.info(
            f"[我能永远定格你] 缓存清理完成({reason})：删除 {result['deleted_count']} 个图片文件，"
            f"释放 {result['human_deleted_size']}。"
        )
        return result

    def _cache_size_limit_bytes(self) -> int:
        configured_mb = self._to_nonnegative_int(
            getattr(self.plugin_config, "max_cache_size_mb", DEFAULT_MAX_CACHE_SIZE_MB),
            DEFAULT_MAX_CACHE_SIZE_MB,
        )
        return max(1, configured_mb) * 1024 * 1024

    def _prune_cache_if_needed(
        self,
        trigger: str = "auto",
        protected_paths: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        if not getattr(self.plugin_config, "enable_size_limit_cleanup", False):
            return {"skipped": True, "reason": "size_limit_disabled"}

        files = self._iter_cache_image_files()
        total_bytes = sum(item["bytes"] for item in files)
        limit_bytes = self._cache_size_limit_bytes()
        if total_bytes <= limit_bytes:
            return {"skipped": True, "reason": "under_limit", "total_bytes": total_bytes, "limit_bytes": limit_bytes}

        protected = {os.path.abspath(path) for path in (protected_paths or []) if path}
        protected.update(self._leased_cache_paths())
        candidates = sorted(files, key=lambda item: (item.get("mtime", 0), item.get("path", "")))
        delete_files = []
        remaining_bytes = total_bytes
        for item in candidates:
            if remaining_bytes <= limit_bytes:
                break
            file_path = os.path.abspath(str(item.get("path", "")))
            if file_path in protected:
                continue
            delete_files.append(item)
            remaining_bytes -= item["bytes"]

        if not delete_files:
            return {
                "skipped": True,
                "reason": "only_protected_files_over_limit",
                "total_bytes": total_bytes,
                "limit_bytes": limit_bytes,
            }

        result = self._delete_cache_files(delete_files, reason=f"size_limit:{trigger}", protected_paths=protected)
        result["before"] = {"bytes": total_bytes, "human_size": self._format_bytes(total_bytes), "count": len(files)}
        result["after"] = self._cache_stats_for_page()["total"]
        logger.info(
            f"[我能永远定格你] 缓存达到上限，自动清理 {result['deleted_count']} 个图片文件，"
            f"释放 {result['human_deleted_size']}。"
        )
        return result

    def _schedule_cache_prune(
        self,
        trigger: str = "auto",
        protected_paths: Optional[Iterable[str]] = None,
    ) -> None:
        """把机会性缓存清理放到后台线程，别在事件循环里同步扫缓存目录。"""
        protected = [str(path) for path in (protected_paths or []) if path]
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # 没有运行中的事件循环（同步调用/退出阶段）：跳过这次机会性清理
            return
        try:
            self._create_background_task(
                asyncio.to_thread(self._prune_cache_if_needed, trigger, protected_paths=protected),
                kind="cache_prune",
                leased_paths=protected,
            )
        except RuntimeError:
            # 后台队列满：放弃这次机会性清理，定时/容量清理仍会处理
            return

    def _cache_cleanup_interval_seconds(self) -> int:
        configured_hours = self._to_nonnegative_int(
            getattr(self.plugin_config, "scheduled_cleanup_interval_hours", DEFAULT_CACHE_CLEANUP_INTERVAL_HOURS),
            DEFAULT_CACHE_CLEANUP_INTERVAL_HOURS,
        )
        return max(1, configured_hours) * 3600

    def _restart_cache_cleanup_task(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        owner_loop = getattr(self, "_event_loop", None)
        if owner_loop is not None and owner_loop is not loop and not owner_loop.is_closed():
            owner_loop.call_soon_threadsafe(self._restart_cache_cleanup_task)
            return
        current = getattr(self, "_cache_cleanup_task", None)
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        if current and not current.done() and current is not current_task:
            current.cancel()
        self._cache_cleanup_task = None

        if not getattr(self.plugin_config, "enable_scheduled_cleanup", False):
            return

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("[我能永远定格你] 当前没有运行中的事件循环，定时缓存清理将在下次配置热加载后启动。")
            return

        self._cache_cleanup_task = self._create_background_task(
            self._cache_cleanup_scheduler(),
            kind="scheduler",
        )
        logger.info(
            f"[我能永远定格你] 已启用定时缓存清理，每 {self._cache_cleanup_interval_seconds() // 3600} 小时清理一次。"
        )

    async def _cache_cleanup_scheduler(self) -> None:
        try:
            while getattr(self.plugin_config, "enable_scheduled_cleanup", False):
                await asyncio.sleep(self._cache_cleanup_interval_seconds())
                if not getattr(self.plugin_config, "enable_scheduled_cleanup", False):
                    return
                await asyncio.to_thread(self._clear_cache_images, reason="scheduled")
        except asyncio.CancelledError:
            raise
