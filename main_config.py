"""「我能永远定格你」配置/页面层 mixin。"""

import asyncio
import base64
import binascii
import copy
import hashlib
import json
import mimetypes
import os
import time
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

from quart import jsonify, request, send_file

try:
    from astrbot.api import logger
except ImportError:  # 离线测试 / 老版本 AstrBot
    from astrbot.api.utils import logger

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:
    def get_astrbot_data_path() -> str:
        return os.path.join(os.getcwd(), "data")

if TYPE_CHECKING:
    from .constants import (
        DEFAULT_CACHE_CLEANUP_INTERVAL_HOURS,
        DEFAULT_DRAW_ERROR_MESSAGE,
        DEFAULT_DRAW_PENDING_MESSAGE,
        DEFAULT_MAX_CACHE_SIZE_MB,
        DEFAULT_SELFIE_ERROR_MESSAGE,
        DEFAULT_SELFIE_PENDING_MESSAGE,
        LEGACY_PERSONA_CONFIG_KEYS,
        MASKED_API_KEY_PREFIX,
        MAX_CONFIG_REQUEST_BYTES,
        MAX_EXTRA_PARAMS_CHARS,
        MAX_PAGE_IMAGE_TOKENS,
        MAX_PLUGIN_REQUEST_BYTES,
        MAX_PROMPT_CHARS,
        NATIVE_ACTIVE_PERSONA_FILE_PREFIX,
        PAGE_PREVIEW_IMAGE_BYTES,
    )
    from .core.persona_manager import PersonaManager
    from .core.prompt_optimizer import PromptOptimizer
    from .core.video_manager import VideoManager
    from .models import PLUGIN_NAME, PluginConfig, _cleanup_unused_persona_refs
    from .utils import split_data_url
else:
    try:
        from .constants import (
            DEFAULT_CACHE_CLEANUP_INTERVAL_HOURS,
            DEFAULT_DRAW_ERROR_MESSAGE,
            DEFAULT_DRAW_PENDING_MESSAGE,
            DEFAULT_MAX_CACHE_SIZE_MB,
            DEFAULT_SELFIE_ERROR_MESSAGE,
            DEFAULT_SELFIE_PENDING_MESSAGE,
            LEGACY_PERSONA_CONFIG_KEYS,
            MASKED_API_KEY_PREFIX,
            MAX_CONFIG_REQUEST_BYTES,
            MAX_EXTRA_PARAMS_CHARS,
            MAX_PAGE_IMAGE_TOKENS,
            MAX_PLUGIN_REQUEST_BYTES,
            MAX_PROMPT_CHARS,
            NATIVE_ACTIVE_PERSONA_FILE_PREFIX,
            PAGE_PREVIEW_IMAGE_BYTES,
        )
        from .core.persona_manager import PersonaManager
        from .core.prompt_optimizer import PromptOptimizer
        from .core.video_manager import VideoManager
        from .models import PLUGIN_NAME, PluginConfig, _cleanup_unused_persona_refs
        from .utils import split_data_url
    except ImportError:  # 顶层导入（测试/脚本）
        from constants import (
            DEFAULT_CACHE_CLEANUP_INTERVAL_HOURS,
            DEFAULT_DRAW_ERROR_MESSAGE,
            DEFAULT_DRAW_PENDING_MESSAGE,
            DEFAULT_MAX_CACHE_SIZE_MB,
            DEFAULT_SELFIE_ERROR_MESSAGE,
            DEFAULT_SELFIE_PENDING_MESSAGE,
            LEGACY_PERSONA_CONFIG_KEYS,
            MASKED_API_KEY_PREFIX,
            MAX_CONFIG_REQUEST_BYTES,
            MAX_EXTRA_PARAMS_CHARS,
            MAX_PAGE_IMAGE_TOKENS,
            MAX_PLUGIN_REQUEST_BYTES,
            MAX_PROMPT_CHARS,
            NATIVE_ACTIVE_PERSONA_FILE_PREFIX,
            PAGE_PREVIEW_IMAGE_BYTES,
        )
        from core.persona_manager import PersonaManager
        from core.prompt_optimizer import PromptOptimizer
        from core.video_manager import VideoManager
        from models import PLUGIN_NAME, PluginConfig, _cleanup_unused_persona_refs
        from utils import split_data_url


class OmniDrawConfigMixin:

    data_dir: str
    config_path: str
    usage_stats_path: str
    context: Any
    _config_lock: Any
    _usage_lock: Any
    _usage_stats: Dict[str, Any]
    _daily_image_limit: Callable[..., int]
    _native_config: Any
    _native_config_path: str
    _page_image_secret: str
    _page_image_lock: Any
    _page_image_tokens: Dict[str, str]
    _clean_runtime_config: Callable[..., Dict[str, Any]]
    _restart_cache_cleanup_task: Callable[..., None]
    _prune_cache_if_needed: Callable[..., Dict[str, Any]]
    _looks_like_image_bytes: Callable[..., bool]
    _validate_local_image_path: Callable[..., str]
    def _resolve_data_dir(self) -> str:
        base_data_dir = str(get_astrbot_data_path())
        return os.path.join(base_data_dir, "plugin_data", PLUGIN_NAME)

    def _get_mtime(self, path: str) -> float:
        if not path:
            return 0.0
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    def _file_signature(self, path: str) -> str:
        if not path:
            return ""
        try:
            with open(path, "rb") as file:
                return hashlib.sha256(file.read()).hexdigest()
        except OSError:
            return ""

    def _file_stamp(self, path: str) -> Optional[Tuple[int, int, int, int]]:
        if not path:
            return None
        try:
            stat = os.stat(path)
            return stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size
        except OSError:
            return None

    def _load_json_file(self, path: str) -> Dict[str, Any]:
        if not path or not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8-sig") as file:
                data = json.load(file)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.error(f"[我能永远定格你] 读取配置失败: {path} {exc}", exc_info=True)
            return {}

    def _today_key(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime())

    def _to_nonnegative_int(self, value: Any, default: int = 0) -> int:
        try:
            parsed = int(float(str(value).strip()))
        except Exception:
            parsed = default
        return max(0, parsed)

    def _load_usage_stats(self) -> Dict[str, Any]:
        return self._normalize_usage_stats(self._load_json_file(self.usage_stats_path))

    def _normalize_usage_stats(self, stats: Dict[str, Any]) -> Dict[str, Any]:
        today = self._today_key()
        if not isinstance(stats, dict) or stats.get("date") != today:
            return {"date": today, "total": 0, "users": {}}

        users = stats.get("users")
        if not isinstance(users, dict):
            users = {}

        normalized_users = {}
        for raw_user_id, raw_record in users.items():
            user_id = str(raw_user_id or "").strip()
            if not user_id:
                continue
            record = raw_record if isinstance(raw_record, dict) else {"count": raw_record}
            count = self._to_nonnegative_int(record.get("count", 0))
            bonus = self._to_nonnegative_int(record.get("bonus", 0))
            checkin_at = self._to_nonnegative_int(record.get("checkin_at", 0))
            normalized_record = {
                "user_id": user_id,
                "count": count,
                "bonus": bonus,
                "checkin_at": checkin_at,
                "last_at": self._to_nonnegative_int(record.get("last_at", 0)),
            }
            for key in ("display_name", "group_id", "access_level"):
                value = str(record.get(key, "")).strip()
                if value:
                    normalized_record[key] = value
            normalized_users[user_id] = normalized_record

        return {
            "date": today,
            "total": sum(record["count"] for record in normalized_users.values()),
            "users": normalized_users,
        }

    def _current_usage_stats(self) -> Dict[str, Any]:
        with self._usage_lock:
            self._usage_stats = self._normalize_usage_stats(self._usage_stats)
            return self._usage_stats

    def _persist_usage_stats(self) -> None:
        with self._usage_lock:
            os.makedirs(self.data_dir, exist_ok=True)
            tmp_path = f"{self.usage_stats_path}.{uuid.uuid4().hex}.tmp"
            try:
                with open(tmp_path, "w", encoding="utf-8") as file:
                    json.dump(self._current_usage_stats(), file, ensure_ascii=False, indent=4)
                os.replace(tmp_path, self.usage_stats_path)
            except Exception as exc:
                logger.error(f"[我能永远定格你] 生图统计保存失败: {exc}", exc_info=True)
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except OSError:  # silent: 删临时文件失败不阻断统计保存
                    pass

    def _usage_stats_for_page(self) -> Dict[str, Any]:
        with self._usage_lock:
            stats = copy.deepcopy(self._normalize_usage_stats(self._usage_stats))
            self._usage_stats = copy.deepcopy(stats)
        users = sorted(
            stats.get("users", {}).values(),
            key=lambda item: (-self._to_nonnegative_int(item.get("count", 0)), str(item.get("user_id", ""))),
        )
        limit = self._daily_image_limit()
        return {
            "date": stats.get("date", self._today_key()),
            "total": stats.get("total", 0),
            "users": users,
            "quota": {
                "enabled": limit > 0,
                "daily_limit": limit,
                "checkin_enabled": bool(getattr(self.plugin_config, "enable_checkin", False)),
                "checkin_bonus_min": self._to_nonnegative_int(getattr(self.plugin_config, "checkin_bonus_min", 1), 1),
                "checkin_bonus_max": self._to_nonnegative_int(getattr(self.plugin_config, "checkin_bonus_max", 3), 3),
            },
        }


    def _merge_config_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        def merge(base: Any, updates: Any) -> Any:
            if isinstance(base, dict) and isinstance(updates, dict):
                merged = copy.deepcopy(base)
                for key, value in updates.items():
                    merged[key] = merge(merged.get(key), value)
                return merged
            return copy.deepcopy(updates)

        return merge(self.raw_config, payload)

    def _api_key_lines(self, value: Any) -> List[str]:
        if isinstance(value, (list, tuple)):
            values = value
        else:
            values = str(value or "").replace("\r", "\n").split("\n")
        return [str(item).strip() for item in values if str(item).strip()]

    def _mask_api_key(self, value: Any) -> str:
        key = str(value or "").strip()
        if not key:
            return ""
        suffix = key[-4:] if len(key) >= 4 else ""
        return f"{MASKED_API_KEY_PREFIX}{suffix}"

    def _mask_config_api_keys(self, config: Dict[str, Any]) -> None:
        for section in ("providers", "video_providers"):
            providers = config.get(section)
            if not isinstance(providers, list):
                continue
            for provider in providers:
                if not isinstance(provider, dict):
                    continue
                keys = self._api_key_lines(provider.get("api_keys", ""))
                provider["api_keys"] = "\n".join(self._mask_api_key(key) for key in keys)
                provider["api_keys_configured"] = bool(keys)

    def _restore_masked_api_keys(self, payload: Dict[str, Any]) -> None:
        for section in ("providers", "video_providers"):
            incoming = payload.get(section)
            existing = self.raw_config.get(section, [])
            if not isinstance(incoming, list) or not isinstance(existing, list):
                continue
            existing_by_id = {
                str(item.get("id", "")).strip(): self._api_key_lines(item.get("api_keys", ""))
                for item in existing
                if isinstance(item, dict)
            }
            for provider in incoming:
                if not isinstance(provider, dict):
                    continue
                if "api_keys" not in provider:
                    continue
                provider_id = str(provider.get("id", "")).strip()
                old_keys = list(existing_by_id.get(provider_id, []))
                new_keys = self._api_key_lines(provider.get("api_keys", ""))
                restored = []
                unmatched_old_keys = list(old_keys)
                for key in new_keys:
                    if key.startswith(MASKED_API_KEY_PREFIX):
                        matched_index = next(
                            (
                                index
                                for index, old_key in enumerate(unmatched_old_keys)
                                if self._mask_api_key(old_key) == key
                            ),
                            None,
                        )
                        if matched_index is not None:
                            restored.append(unmatched_old_keys.pop(matched_index))
                    else:
                        restored.append(key)
                provider["api_keys"] = restored
                provider.pop("api_keys_configured", None)

    def _sync_native_active_persona_upload(self, config: Dict[str, Any]) -> None:
        persona_config = config.get("persona_config")
        if not isinstance(persona_config, dict):
            return

        upload_refs = self._as_config_list(
            persona_config.get("persona_ref_image") or persona_config.get("persona_ref_images")
        )
        if not any(self._is_native_file_ref(ref) for ref in upload_refs):
            return

        profiles = persona_config.get("profiles")
        if not isinstance(profiles, list) or not profiles:
            profiles = [{
                "id": persona_config.get("active_persona_id") or "default",
                "persona_name": persona_config.get("persona_name", "默认助理"),
                "persona_base_prompt": persona_config.get("persona_base_prompt", ""),
                "persona_ref_image": [],
            }]
            persona_config["profiles"] = profiles

        active_profile = self._find_active_persona_profile(persona_config, profiles)
        if active_profile is not None:
            active_profile["persona_ref_image"] = upload_refs

    def _as_config_list(self, value: Any) -> List[Any]:
        if isinstance(value, list):
            return [item for item in value if item]
        if isinstance(value, tuple):
            return [item for item in value if item]
        return [value] if value else []

    def _find_active_persona_profile(self, persona_config: Dict[str, Any], profiles: List[Any]) -> Optional[Dict[str, Any]]:
        active_id = str(persona_config.get("active_persona_id") or "").strip().lower()
        dict_profiles = [profile for profile in profiles if isinstance(profile, dict)]
        if not dict_profiles:
            return None
        if active_id:
            for profile in dict_profiles:
                if str(profile.get("id") or "").strip().lower() == active_id:
                    return profile
        return dict_profiles[0]

    def _is_native_file_ref(self, image_ref: Any) -> bool:
        return str(image_ref or "").replace("\\", "/").lstrip("/").startswith("files/")

    def _native_file_ref_for_config(self, image_ref: Any) -> str:
        if not image_ref:
            return ""
        normalized = str(image_ref).replace("\\", "/").lstrip("/")
        if normalized.startswith(NATIVE_ACTIVE_PERSONA_FILE_PREFIX):
            return normalized
        if not os.path.isabs(str(image_ref)):
            return ""

        plugin_data_dir = os.path.abspath(self.data_dir)
        abs_ref = os.path.abspath(str(image_ref))
        try:
            common = os.path.commonpath([plugin_data_dir, abs_ref])
        except ValueError:
            return ""
        if common != plugin_data_dir:
            return ""
        rel_ref = os.path.relpath(abs_ref, plugin_data_dir).replace("\\", "/")
        if rel_ref.startswith(NATIVE_ACTIVE_PERSONA_FILE_PREFIX):
            return rel_ref
        return ""

    def _native_active_persona_file_refs(self, persona_config: Dict[str, Any]) -> List[str]:
        profiles = persona_config.get("profiles")
        if not isinstance(profiles, list):
            return []
        active_profile = self._find_active_persona_profile(persona_config, profiles)
        if not active_profile:
            return []
        refs = []
        for ref in self._as_config_list(active_profile.get("persona_ref_image")):
            native_ref = self._native_file_ref_for_config(ref)
            if native_ref and native_ref not in refs:
                refs.append(native_ref)
        return refs

    def _config_for_native_page(self) -> Dict[str, Any]:
        native_config = copy.deepcopy(self.raw_config)

        def mark_template_items(items: Any, template_key: str) -> List[Any]:
            if not isinstance(items, list):
                return []
            marked = []
            for item in items:
                if isinstance(item, dict):
                    item = copy.deepcopy(item)
                    item["__template_key"] = str(item.get("__template_key") or template_key)
                marked.append(item)
            return marked

        native_config["providers"] = mark_template_items(native_config.get("providers", []), "image_provider")
        native_config["video_providers"] = mark_template_items(native_config.get("video_providers", []), "video_provider")

        persona_config = native_config.get("persona_config")
        if isinstance(persona_config, dict):
            persona_config["profiles"] = mark_template_items(persona_config.get("profiles", []), "persona")
            persona_config["persona_ref_image"] = self._native_active_persona_file_refs(persona_config)
        return native_config

    def _has_config_payload(self, config: Dict[str, Any]) -> bool:
        if not isinstance(config, dict):
            return False
        if config.get("providers") or config.get("video_providers") or config.get("presets"):
            return True
        if config.get("verbose_report"):
            return True

        permission_config = config.get("permission_config")
        if isinstance(permission_config, dict) and self._permission_has_payload(permission_config):
            return True

        usage_config = config.get("usage_config")
        if isinstance(usage_config, dict) and self._usage_has_payload(usage_config):
            return True

        cache_config = config.get("cache_config")
        if isinstance(cache_config, dict) and self._cache_has_payload(cache_config):
            return True

        reply_config = config.get("reply_config")
        if isinstance(reply_config, dict) and self._reply_has_payload(reply_config):
            return True

        router_config = config.get("router_config")
        if isinstance(router_config, dict) and self._router_has_payload(router_config):
            return True

        optimizer_config = config.get("optimizer_config")
        if isinstance(optimizer_config, dict) and self._optimizer_has_payload(optimizer_config):
            return True

        persona_config = config.get("persona_config")
        if isinstance(persona_config, dict) and self._persona_has_payload(persona_config):
            return True
        return False

    @staticmethod
    def _permission_has_payload(permission_config: Dict[str, Any]) -> bool:
        for key in (
            "usable_users",
            "access_users",
            "use_whitelist",
            "allowed_users",
            "unlimited_users",
            "user_whitelist",
            "blocked_users",
            "user_blacklist",
            "unlimited_groups",
            "group_whitelist",
        ):
            if str(permission_config.get(key, "")).strip():
                return True
        return False

    def _usage_has_payload(self, usage_config: Dict[str, Any]) -> bool:
        if bool(usage_config.get("enable_daily_limit")):
            return True
        if self._to_nonnegative_int(usage_config.get("daily_image_limit", 20), 20) != 20:
            return True
        if bool(usage_config.get("enable_checkin")):
            return True
        if self._to_nonnegative_int(usage_config.get("checkin_bonus_min", 1), 1) != 1:
            return True
        if self._to_nonnegative_int(usage_config.get("checkin_bonus_max", 3), 3) != 3:
            return True
        return False

    def _cache_has_payload(self, cache_config: Dict[str, Any]) -> bool:
        if bool(cache_config.get("enable_scheduled_cleanup")):
            return True
        if self._to_nonnegative_int(
            cache_config.get("scheduled_cleanup_interval_hours", DEFAULT_CACHE_CLEANUP_INTERVAL_HOURS),
            DEFAULT_CACHE_CLEANUP_INTERVAL_HOURS,
        ) != DEFAULT_CACHE_CLEANUP_INTERVAL_HOURS:
            return True
        if bool(cache_config.get("enable_size_limit_cleanup")):
            return True
        if self._to_nonnegative_int(
            cache_config.get("max_cache_size_mb", DEFAULT_MAX_CACHE_SIZE_MB),
            DEFAULT_MAX_CACHE_SIZE_MB,
        ) != DEFAULT_MAX_CACHE_SIZE_MB:
            return True
        return False

    @staticmethod
    def _reply_has_payload(reply_config: Dict[str, Any]) -> bool:
        reply_defaults = {
            "draw_pending_message": DEFAULT_DRAW_PENDING_MESSAGE,
            "selfie_pending_message": DEFAULT_SELFIE_PENDING_MESSAGE,
            "draw_error_message": DEFAULT_DRAW_ERROR_MESSAGE,
            "selfie_error_message": DEFAULT_SELFIE_ERROR_MESSAGE,
        }
        for key, default in reply_defaults.items():
            value = str(reply_config.get(key, "")).strip()
            if value and value != default:
                return True
        return False

    @staticmethod
    def _router_has_payload(router_config: Dict[str, Any]) -> bool:
        route_defaults = {
            "chain_text2img": "node_1",
            "chain_selfie": "node_1",
            "chain_video": "video_node_1",
        }
        for key, default in route_defaults.items():
            value = router_config.get(key)
            if value not in (None, "", default):
                return True
        return False

    @staticmethod
    def _optimizer_has_payload(optimizer_config: Dict[str, Any]) -> bool:
        optimizer_defaults = {
            "enable_optimizer": True,
            "optimizer_style": "手机日常原生感",
            "chain_optimizer": "node_1",
            "optimizer_model": "gpt-4o-mini",
            "optimizer_timeout": 15,
            "max_batch_count": 0,
            "optimizer_custom_prompt": "",
        }
        for key, default in optimizer_defaults.items():
            value = optimizer_config.get(key)
            if value not in (None, "", default):
                return True
        return False

    @staticmethod
    def _persona_has_payload(persona_config: Dict[str, Any]) -> bool:
        if persona_config.get("active_persona_id") not in (None, "", "default"):
            return True
        profiles = persona_config.get("profiles")
        if isinstance(profiles, list) and profiles:
            for index, profile in enumerate(profiles):
                if not isinstance(profile, dict):
                    continue
                default_name = "默认助理" if index == 0 else f"人设 {index + 1}"
                if str(profile.get("id", "")).strip() not in ("", "default" if index == 0 else f"persona_{index + 1}"):
                    return True
                if str(profile.get("persona_name", "")).strip() not in ("", default_name):
                    return True
                if str(profile.get("persona_base_prompt", "")).strip():
                    return True
                if profile.get("persona_ref_image"):
                    return True
        if str(persona_config.get("persona_name", "")).strip() not in ("", "默认助理"):
            return True
        if str(persona_config.get("persona_base_prompt", "")).strip():
            return True
        if persona_config.get("persona_ref_image") or persona_config.get("persona_ref_images"):
            return True
        return False

    def _load_initial_config(self, fallback_config: Dict[str, Any]) -> Dict[str, Any]:
        native_config = self._clean_runtime_config(dict(fallback_config)) if isinstance(fallback_config, dict) else {}
        native_has_payload = self._has_config_payload(native_config)
        persisted_config = self._clean_runtime_config(self._load_json_file(self.config_path))
        persisted_has_payload = self._has_config_payload(persisted_config)

        if native_has_payload:
            if not persisted_has_payload or self._native_config_mtime >= self._persist_config_mtime:
                return native_config
            return persisted_config
        if persisted_has_payload:
            return persisted_config
        if native_config:
            return native_config
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as file:
                    data = json.load(file)
                if isinstance(data, dict):
                    return self._clean_runtime_config(data)
                logger.warning("[我能永远定格你] 本地配置不是 JSON 对象，已回退到 AstrBot 原生配置。")
            except Exception as exc:
                logger.error(f"[我能永远定格你] 读取本地持久化配置失败: {exc}", exc_info=True)
        return native_config

    def _apply_runtime_config(self, raw_config: Dict[str, Any]) -> None:
        previous = getattr(self, "plugin_config", None)
        candidate = self._clean_runtime_config(raw_config if isinstance(raw_config, dict) else {})
        config = PluginConfig.from_dict(candidate, self.data_dir, cleanup_persona_refs=False)
        for legacy_key in LEGACY_PERSONA_CONFIG_KEYS:
            candidate.pop(legacy_key, None)
        managers = PersonaManager(config), VideoManager(config), PromptOptimizer(config)
        self.raw_config = candidate
        self.plugin_config = config
        self.persona_manager, self.video_manager, self.prompt_optimizer = managers
        schedule_fields = ("enable_scheduled_cleanup", "scheduled_cleanup_interval_hours")
        size_fields = ("enable_size_limit_cleanup", "max_cache_size_mb")
        if previous is None or any(getattr(previous, key) != getattr(config, key) for key in schedule_fields):
            self._restart_cache_cleanup_task()
        if previous is None or any(getattr(previous, key) != getattr(config, key) for key in size_fields):
            self._prune_cache_if_needed("config_reload")

    def _cleanup_persona_refs(self) -> None:
        # Delete retired references only after the new configuration has committed.
        try:
            active_refs = [ref for profile in self.plugin_config.personas for ref in profile.ref_images]
            _cleanup_unused_persona_refs(os.path.join(self.data_dir, "persona_refs"), active_refs)
        except Exception as exc:
            logger.warning(f"[我能永远定格你] 清理未使用的人设参考图失败，已保留文件: {exc}")

    def _prepare_runtime_config(self, raw_config: Dict[str, Any]) -> Dict[str, Any]:
        candidate = self._clean_runtime_config(raw_config if isinstance(raw_config, dict) else {})
        PluginConfig.from_dict(candidate, self.data_dir, cleanup_persona_refs=False)
        for legacy_key in LEGACY_PERSONA_CONFIG_KEYS:
            candidate.pop(legacy_key, None)
        return candidate

    def _persist_config(self) -> None:
        with self._config_lock:
            os.makedirs(self.data_dir, exist_ok=True)
            tmp_path = f"{self.config_path}.{uuid.uuid4().hex}.tmp"
            try:
                with open(tmp_path, "w", encoding="utf-8") as file:
                    json.dump(self.raw_config, file, ensure_ascii=False, indent=4)
                os.replace(tmp_path, self.config_path)
                self._persist_config_mtime = self._get_mtime(self.config_path)
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:  # silent: 删临时配置失败不阻断保存
                        pass

    def _safe_update_context_config(self) -> None:
        if self._native_config is not None and hasattr(self._native_config, "save_config"):
            try:
                previous_native = copy.deepcopy(dict(self._native_config))
            except Exception:
                previous_native = {}
            try:
                native_config = self._config_for_native_page()
                self._native_config.clear()
                self._native_config.update(native_config)
                self._native_config.save_config()
                self._native_config_stamp = self._file_stamp(self._native_config_path)
                self._native_config_mtime = self._get_mtime(self._native_config_path)
                self._native_config_signature = self._file_signature(self._native_config_path)
                return
            except Exception as exc:
                try:
                    self._native_config.clear()
                    self._native_config.update(previous_native)
                except Exception:  # silent: 回滚 native config 失败已由下方 warning 记录
                    pass
                logger.warning(f"[我能永远定格你] AstrBot 原生配置同步失败，已保留本地持久化配置: {exc}")

        if not hasattr(self.context, "update_config"):
            return
        try:
            self.context.update_config(self.raw_config)
        except Exception as exc:
            logger.warning(f"[我能永远定格你] AstrBot 主配置同步失败，已保留本地持久化配置: {exc}")

    def _refresh_from_native_config_if_changed(self) -> None:
        if not self._native_config_path:
            return
        stamp = self._file_stamp(self._native_config_path)
        if stamp is not None and stamp == getattr(self, "_native_config_stamp", None):
            return
        with self._config_lock:
            stamp = self._file_stamp(self._native_config_path)
            if stamp is not None and stamp == getattr(self, "_native_config_stamp", None):
                return
            current_mtime = self._get_mtime(self._native_config_path)
            current_signature = self._file_signature(self._native_config_path)
            if current_signature and current_signature == self._native_config_signature:
                self._native_config_stamp = stamp
                self._native_config_mtime = current_mtime
                return
            native_config = self._clean_runtime_config(self._load_json_file(self._native_config_path))
            if not native_config:
                self._native_config_stamp = stamp
                self._native_config_mtime = current_mtime
                self._native_config_signature = current_signature
                return
            previous_raw = copy.deepcopy(self.raw_config)
            previous_runtime = (
                self.plugin_config,
                self.persona_manager,
                self.video_manager,
                self.prompt_optimizer,
            )
            persisted_candidate = False
            try:
                candidate = self._prepare_runtime_config(native_config)
                if candidate != self.raw_config:
                    self.raw_config = candidate
                    self._persist_config()
                    persisted_candidate = True
                    self._apply_runtime_config(candidate)
                self._native_config_stamp = stamp
                self._native_config_mtime = current_mtime
                self._native_config_signature = current_signature
                if self._native_config is not None:
                    self._native_config.clear()
                    self._native_config.update(self._config_for_native_page())
                logger.info("[我能永远定格你] 已从 AstrBot 原生配置热同步最新设置。")
            except Exception:
                self.raw_config = previous_raw
                (
                    self.plugin_config,
                    self.persona_manager,
                    self.video_manager,
                    self.prompt_optimizer,
                ) = previous_runtime
                if persisted_candidate:
                    try:
                        self._persist_config()
                    except Exception as rollback_exc:
                        logger.error(f"[我能永远定格你] 原生配置热同步回滚持久化失败: {rollback_exc}", exc_info=True)
                raise
            self._cleanup_persona_refs()

    def _read_page_config(self, compact: bool) -> Dict[str, Any]:
        with self._config_lock:
            self._refresh_from_native_config_if_changed()
            return self._config_for_page(compact=compact)

    async def get_config_handler(self):
        self._event_loop = asyncio.get_running_loop()
        config = await asyncio.to_thread(self._read_page_config, False)
        return jsonify(config)

    async def get_page_config_handler(self):
        self._event_loop = asyncio.get_running_loop()
        config = await asyncio.to_thread(self._read_page_config, True)
        return jsonify(config)

    async def get_usage_stats_handler(self):
        self._event_loop = asyncio.get_running_loop()
        await asyncio.to_thread(self._refresh_from_native_config_if_changed)
        stats = await asyncio.to_thread(self._usage_stats_for_page)
        return jsonify({"success": True, "stats": stats})

    async def get_cache_stats_handler(self):
        self._event_loop = asyncio.get_running_loop()
        await asyncio.to_thread(self._refresh_from_native_config_if_changed)
        stats = await asyncio.to_thread(self._cache_stats_for_page)
        return jsonify({"success": True, "stats": stats})

    def _config_for_page(self, compact: bool = False) -> Dict[str, Any]:
        page_config = copy.deepcopy(self.raw_config)
        self._mask_config_api_keys(page_config)
        persona_config = page_config.get("persona_config")
        if not isinstance(persona_config, dict):
            return page_config

        profiles = persona_config.get("profiles")
        if isinstance(profiles, list):
            for profile in profiles:
                if not isinstance(profile, dict):
                    continue
                raw_profile_images = profile.get("persona_ref_image", [])
                if not isinstance(raw_profile_images, list):
                    raw_profile_images = [raw_profile_images] if raw_profile_images else []
                profile["persona_ref_image"] = self._image_refs_for_page(raw_profile_images, compact=compact)

        raw_images = persona_config.get("persona_ref_image", [])
        if not isinstance(raw_images, list):
            raw_images = [raw_images] if raw_images else []
        persona_config["persona_ref_image"] = self._image_refs_for_page(raw_images, compact=compact)
        return page_config

    def _page_persona_refs(self) -> Dict[str, Any]:
        persona = self.raw_config.get("persona_config", {})
        return {
            "profiles": [{
                "id": profile.get("id", ""),
                "persona_ref_image": self._image_refs_for_page(profile.get("persona_ref_image", []), compact=True),
            } for profile in persona.get("profiles", []) if isinstance(profile, dict)],
            "persona_ref_image": self._image_refs_for_page(persona.get("persona_ref_image", []), compact=True),
        }

    def _image_refs_for_page(self, image_refs: Iterable[Any], compact: bool = False) -> List[str]:
        refs = []
        for image_ref in image_refs:
            if not image_ref:
                continue
            page_ref = self._image_ref_for_page(image_ref, compact=compact)
            if page_ref:
                refs.append(page_ref)
        return refs

    def _image_ref_for_page(self, image_ref: str, compact: bool = False) -> str:
        image_ref = str(image_ref)
        resolved_preview = self._resolve_page_image_ref(image_ref)
        if resolved_preview:
            image_ref = resolved_preview
        elif self._is_page_image_preview_ref(image_ref):
            return ""

        if image_ref.lower().startswith(("http://", "https://")):
            parsed = urlparse(image_ref)
            if parsed.username or parsed.password:
                return ""
            return image_ref
        if image_ref.lower().startswith("data:image"):
            try:
                data, content_type = split_data_url(image_ref)
                if len(data) > PAGE_PREVIEW_IMAGE_BYTES or not self._looks_like_image_bytes(data[:32]):
                    return ""
                return f"data:{content_type};base64,{base64.b64encode(data).decode('ascii')}"
            except (ValueError, binascii.Error):
                return ""
        if not self._is_safe_page_image_path(image_ref):
            return ""
        try:
            if compact or os.path.getsize(image_ref) > PAGE_PREVIEW_IMAGE_BYTES:
                return self._local_image_preview_url(image_ref)
            mime_type = mimetypes.guess_type(image_ref)[0] or "image/png"
            with open(image_ref, "rb") as file:
                encoded = base64.b64encode(file.read()).decode("utf-8")
            return f"data:{mime_type};base64,{encoded}"
        except OSError:
            return ""

    def _is_safe_page_image_path(self, image_path: str) -> bool:
        try:
            path = self._validate_local_image_path(image_path, self.data_dir)
            return bool(path and os.path.isfile(path))
        except (OSError, ValueError, TypeError):
            return False

    def _local_image_preview_url(self, image_ref: str) -> str:
        abs_path = os.path.abspath(image_ref)
        token = hashlib.sha256(f"{self._page_image_secret}\0{abs_path}".encode("utf-8")).hexdigest()
        with self._page_image_lock:
            self._page_image_tokens.pop(token, None)
            self._page_image_tokens[token] = abs_path
            while len(self._page_image_tokens) > MAX_PAGE_IMAGE_TOKENS:
                self._page_image_tokens.pop(next(iter(self._page_image_tokens)))
        return f"/{PLUGIN_NAME}/get_image?token={token}"

    def _is_page_image_preview_ref(self, image_ref: str) -> bool:
        return f"{PLUGIN_NAME}/get_image" in str(image_ref)

    def _extract_page_image_token(self, image_ref: str) -> str:
        if not self._is_page_image_preview_ref(image_ref):
            return ""
        try:
            parsed = urlparse(str(image_ref))
            token = parse_qs(parsed.query).get("token", [""])[0]
        except Exception:
            token = ""
        return str(token).strip()

    def _resolve_page_image_ref(self, image_ref: str) -> str:
        token = self._extract_page_image_token(image_ref)
        if not token:
            return ""
        with self._page_image_lock:
            image_path = self._page_image_tokens.get(token, "")
        return image_path if image_path and os.path.exists(image_path) else ""

    def _normalize_saved_page_images(self, config: Dict[str, Any]) -> None:
        persona_config = config.get("persona_config")
        if not isinstance(persona_config, dict):
            return

        def normalize_refs(value: Any) -> List[str]:
            if isinstance(value, list):
                raw_refs = value
            elif value:
                raw_refs = [value]
            else:
                raw_refs = []

            refs = []
            for ref in raw_refs:
                ref_str = str(ref or "")
                if not ref_str:
                    continue
                if self._is_page_image_preview_ref(ref_str):
                    resolved = self._resolve_page_image_ref(ref_str)
                    if not resolved or not self._is_safe_page_image_path(resolved):
                        raise ValueError("参考图预览已失效，请刷新配置页后重试；原配置未修改。")
                    refs.append(resolved)
                    continue
                refs.append(ref_str)
            return refs

        persona_config["persona_ref_image"] = normalize_refs(persona_config.get("persona_ref_image", []))
        profiles = persona_config.get("profiles", [])
        if isinstance(profiles, list):
            for profile in profiles:
                if isinstance(profile, dict):
                    profile["persona_ref_image"] = normalize_refs(profile.get("persona_ref_image", []))

    async def get_image_handler(self):
        token = str(request.args.get("token", "")).strip()
        with self._page_image_lock:
            image_path = self._page_image_tokens.get(token)
        if not image_path or not self._is_safe_page_image_path(image_path):
            return jsonify({"success": False, "message": "参考图预览已失效，请刷新配置页。"}), 404

        if request.args.get("format") == "data_url":
            # AstrBot's Page bridge transports JSON, not binary fetch responses.
            data_url = await asyncio.to_thread(self._image_ref_for_page, image_path)
            if not data_url.startswith("data:image/"):
                return jsonify({"success": False, "message": "参考图无法预览。"}), 413
            return jsonify({"success": True, "data_url": data_url})

        mime_type = mimetypes.guess_type(image_path)[0] or "image/png"
        return await send_file(image_path, mimetype=mime_type)

    async def generate_image_for_plugin_handler(self):
        content_length = getattr(request, "content_length", None)
        if content_length and content_length > MAX_PLUGIN_REQUEST_BYTES:
            return jsonify({"success": False, "message": "请求体超过大小限制。", "images": []}), 413
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"success": False, "message": "请求体必须是 JSON 对象。"}), 400

        try:
            prompt_value = str(payload.get("prompt", payload.get("action", "")) or "")
            extra_params_value = str(payload.get("extra_params", "") or "")
            if len(prompt_value) > MAX_PROMPT_CHARS or len(extra_params_value) > MAX_EXTRA_PARAMS_CHARS:
                return jsonify({"success": False, "message": "提示词或附加参数过长。", "images": []}), 413
            result = await self.generate_images_for_plugin(
                prompt=prompt_value,
                count=payload.get("count", 1),
                aspect_ratio=str(payload.get("aspect_ratio", "") or ""),
                size=str(payload.get("size", payload.get("resolution", "")) or ""),
                extra_params=extra_params_value,
                refs=payload.get("refs", payload.get("user_refs", [])),
                mode="selfie" if "selfie" in payload and self._plugin_bool(payload.get("selfie"), default=False) else str(
                    payload.get("mode", payload.get("chain_type", payload.get("type", ""))) or ""
                ),
                event=None,
                record_usage=False,
                use_gif=self._plugin_bool(payload.get("use_gif"), default=False),
                frame_count=payload.get("frame_count", 9),
                frame_duration_ms=payload.get("frame_duration_ms", 200),
                negative_prompt=str(payload.get("negative_prompt", "") or ""),
                scene_lock=str(payload.get("scene_lock", "") or ""),
                pose_lock=str(payload.get("pose_lock", "") or ""),
            )
            status_code = 200 if result.get("success") else 500
            return jsonify(result), status_code
        except Exception as exc:
            logger.error(f"[我能永远定格你] 插件生图 API 失败: {exc}", exc_info=True)
            return jsonify({
                "success": False,
                "message": f"画图失败 ({self._safe_plugin_error_message(exc)})。",
                "images": [],
            }), 500

    async def _run_config_operation(self, operation: Callable[..., Any], *args: Any) -> Any:
        self._event_loop = asyncio.get_running_loop()
        save_task = asyncio.create_task(asyncio.to_thread(operation, *args))
        try:
            return await asyncio.shield(save_task)
        except asyncio.CancelledError:
            # A disconnected caller must not abandon a half-finished transaction.
            while not save_task.done():
                try:
                    await asyncio.shield(save_task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not save_task.cancelled() and save_task.exception() is not None:
                logger.error(f"[我能永远定格你] 调用方取消后配置操作失败: {save_task.exception()}")
            raise

    async def save_config_handler(self):
        content_length = getattr(request, "content_length", None)
        if content_length and content_length > MAX_CONFIG_REQUEST_BYTES:
            return jsonify({"success": False, "message": "配置请求体超过大小限制。"}), 413
        new_config = await request.get_json(silent=True)
        if not isinstance(new_config, dict):
            return jsonify({"success": False, "message": "配置格式错误：请求体必须是 JSON 对象。"}), 400

        try:
            result = await self._run_config_operation(self._save_page_config, new_config)
        except Exception as exc:
            logger.error(f"[我能永远定格你] 配置保存失败: {exc}", exc_info=True)
            return jsonify({
                "success": False,
                "message": f"配置保存失败: {self._safe_plugin_error_message(exc)}",
            }), 400 if isinstance(exc, ValueError) else 500

        logger.info(f"[我能永远定格你] 配置已持久化并热重载: {self.config_path}")
        return jsonify(result)

    def _save_page_config(self, new_config: Dict[str, Any]) -> Dict[str, Any]:
        with self._config_lock:
            if getattr(self, "_native_config_path", ""):
                self._refresh_from_native_config_if_changed()
            previous_raw = copy.deepcopy(self.raw_config)
            previous_runtime = (
                self.plugin_config,
                self.persona_manager,
                self.video_manager,
                self.prompt_optimizer,
            )
            persisted_candidate = False
            try:
                new_config = copy.deepcopy(new_config)
                self._normalize_saved_page_images(new_config)
                self._restore_masked_api_keys(new_config)
                candidate = self._prepare_runtime_config(self._merge_config_payload(new_config))
                if candidate == self.raw_config:
                    return {"success": True, "message": "配置已保存，热重载生效。", "persona_refs": self._page_persona_refs()}
                self.raw_config = candidate
                self._persist_config()
                persisted_candidate = True
                self._apply_runtime_config(candidate)
                self._safe_update_context_config()
            except Exception:
                self.raw_config = previous_raw
                (
                    self.plugin_config,
                    self.persona_manager,
                    self.video_manager,
                    self.prompt_optimizer,
                ) = previous_runtime
                if persisted_candidate:
                    try:
                        self._persist_config()
                    except Exception as rollback_exc:
                        logger.error(f"[我能永远定格你] 配置回滚持久化失败: {rollback_exc}", exc_info=True)
                raise
            self._cleanup_persona_refs()
            return {"success": True, "message": "配置已保存，热重载生效。", "persona_refs": self._page_persona_refs()}

    async def clear_cache_handler(self):
        self._event_loop = asyncio.get_running_loop()
        await asyncio.to_thread(self._refresh_from_native_config_if_changed)
        result = await asyncio.to_thread(lambda: self._clear_cache_images(reason="webui"))
        stats = await asyncio.to_thread(self._cache_stats_for_page)
        return jsonify({"success": True, "message": "缓存已清理。", "cleanup": result, "stats": stats})
