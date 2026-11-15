"""提示词副脑优化器。"""
import json
import re
import aiohttp
from typing import Optional
from astrbot.api import logger
from ..models import PluginConfig
from ..constants import APIType
try:
    from ..providers.base import (
        build_chat_completions_endpoint,
        next_api_key,
        read_response_text_limited,
    )
except ImportError:
    # [patched] 兼容旧版 providers/base.py：旧版本没有 read_response_text_limited
    from ..providers.base import build_chat_completions_endpoint, next_api_key

    async def read_response_text_limited(response, max_bytes: int = 256 * 1024) -> str:
        text = await response.text()
        if max_bytes and len(text.encode("utf-8", errors="ignore")) > max_bytes:
            raise ValueError(f"响应超过大小限制 {max_bytes // 1024}KB")
        return text


MAX_OPTIMIZER_RESPONSE_BYTES = 256 * 1024


def apply_scene_pose_lock(items, scene_lock: str = "", pose_lock: str = ""):
    """[amo patch] 场景/姿势锁：解析出 JSON 后直接用给定的英文句子覆盖，副脑改不回去。

    ``scene_lock`` / ``pose_lock`` 由 amo 按日程规则表生成；只有真的传了才覆盖。
    返回同一个 list（原地改），方便调用方接在解析之后。
    """
    scene_lock = str(scene_lock or "").strip()
    pose_lock = str(pose_lock or "").strip()
    if not items or (not scene_lock and not pose_lock):
        return items
    changed = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        if scene_lock:
            item["environment_and_scene"] = scene_lock
        if pose_lock:
            item["pose_and_action"] = pose_lock
        changed += 1
    if changed:
        logger.info(
            "🔒 [副脑] 场景/姿势已按 amo 日程锁定（scene=%s, pose=%s）"
            % (bool(scene_lock), bool(pose_lock))
        )
    return items


def locked_prompt(raw_action: str, scene_lock: str = "", pose_lock: str = "") -> list:
    """副脑关闭/未配置/超时时也要保住场景与姿势锁。

    以前这些早退分支直接 return [raw_action] * count，amo 传进来的 scene_lock/pose_lock
    全部丢掉，出图与日程不符，但 amo 日志还打印“已锁定”，看起来像生效。
    """
    text = str(raw_action or "").strip()
    locks = [str(part or "").strip() for part in (scene_lock, pose_lock)]
    locks = [part for part in locks if part and part not in text]
    if locks:
        text = (text + ", " + ", ".join(locks)) if text else ", ".join(locks)
    return [text]


class PromptOptimizer:
    def __init__(self, config: PluginConfig):
        self.config = config

    async def optimize(self, raw_action: str, count: int = 1, session: Optional[aiohttp.ClientSession] = None, has_reference: bool = False, selfie: bool = False, external_camera: bool = False, locked_outfit: str = "", scene_lock: str = "", pose_lock: str = "") -> list:
        if not getattr(self.config, "enable_optimizer", True):
            return locked_prompt(raw_action, scene_lock, pose_lock) * count

        if not raw_action or raw_action.strip() == "":
            return locked_prompt(raw_action, scene_lock, pose_lock) * count

        chain = self.config.chains.get("optimizer", [])
        provider = self.config.get_provider(chain[0]) if chain else (self.config.providers[0] if self.config.providers else None)
        if not provider or not provider.base_url:
            return locked_prompt(raw_action, scene_lock, pose_lock) * count
        if provider.api_type == APIType.GEMINI_OFFICIAL:
            logger.warning("⚠️ [副脑] Gemini 官方节点不兼容 OpenAI Chat 优化协议，已跳过副脑优化。")
            return locked_prompt(raw_action, scene_lock, pose_lock) * count

        endpoint = build_chat_completions_endpoint(provider.base_url)
        api_key = next_api_key(
            provider.id,
            provider.api_keys,
            scope=f"optimizer:{provider.api_type}:{provider.base_url}",
        )
        if not endpoint or not api_key:
            return locked_prompt(raw_action, scene_lock, pose_lock) * count
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        # ==========================================
        # 🚀 动态风格插槽系统 (Dynamic Style Engine)
        # ==========================================
        style_choice = getattr(self.config, "optimizer_style", "手机日常原生感")
        custom_prompt = getattr(self.config, "optimizer_custom_prompt", "").strip()
        custom_style_hint = custom_prompt
        style_data, _quality_guardrails = self._build_style_data(style_choice, custom_prompt)
        style_data, _ref_rule, _scene_lock, _pose_lock = self._apply_reference_rules(
            style_data, has_reference, selfie, external_camera, locked_outfit, scene_lock, pose_lock
        )
        sys_prompt = self._build_system_prompt(style_data, _ref_rule, count, _quality_guardrails)

        payload = {
            "model": self.config.optimizer_model or provider.model,
            "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": raw_action}],
            "max_tokens": 4000 if count > 1 else 2500,
            "temperature": 0.8,
            "response_format": {"type": "json_object"}
        }

        session_obj = session
        close_session = False
        if session_obj is None:
            session_obj = aiohttp.ClientSession()
            close_session = True

        try:
            try:
                timeout_val = self.config.optimizer_timeout * (1.5 if count > 1 else 1.0)
                logger.info(f"🧠 [副脑] 正在以【{style_choice}】风格重构提示词 (模型: {self.config.optimizer_model})")

                async with session_obj.post(endpoint, headers=headers, json=payload, timeout=timeout_val) as resp:
                    resp.raise_for_status()
                    response_text = await read_response_text_limited(
                        resp,
                        max_bytes=MAX_OPTIMIZER_RESPONSE_BYTES,
                    )
                    data = json.loads(response_text)

                    if isinstance(data, dict) and "choices" in data and len(data["choices"]) > 0:
                        raw_content = data["choices"][0]["message"]["content"].strip()
                        items = self._parse_items(raw_content, count, _scene_lock, _pose_lock)

                        results = []
                        anti_collage = "single image, one natural coherent frame, no grid, no collage, no split screen, no multiple views"

                        for item in items[:count]:
                            if isinstance(item, dict):
                                parts = []
                                for k in ["subject_appearance", "clothing_and_accessories", "pose_and_action", "environment_and_scene", "lighting_and_mood", "technical_specs", "realism_and_quality_guardrails"]:
                                    val = item.get(k, "")
                                    if val and isinstance(val, str):
                                        parts.append(val.strip())

                                master_prompt = f"{anti_collage}, " + ", ".join(parts)
                                master_prompt = re.sub(r'\s+', ' ', master_prompt)
                                results.append(master_prompt)

                        while len(results) < count:
                            results.append(results[0] if results
                                           else locked_prompt(raw_action, _scene_lock, _pose_lock)[0])

                        logger.info(f"✨ [副脑] 成功重构并提取 {len(results[:count])} 组【{style_choice}】提示词！")
                        return results[:count]

            except Exception as e:
                logger.warning(f"⚠️ [副脑降级] ({str(e)})")
                return locked_prompt(raw_action, _scene_lock, _pose_lock) * count

            return locked_prompt(raw_action, _scene_lock, _pose_lock) * count
        finally:
            if close_session and session_obj is not None:
                await session_obj.close()

    def _build_style_data(self, style_choice: str, custom_prompt: str):
        """组装风格插槽，返回 (style_data, universal_quality_guardrails)。"""
        if custom_prompt and re.search(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", custom_prompt):
            custom_style_hint = "the custom visual style described by the user, translated into natural English"

        universal_quality_guardrails = (
            "single coherent image, physically plausible perspective, natural proportions, believable camera distance, "
            "realistic hands and fingers, no extra limbs, no warped joints, no broken anatomy, no duplicated face, "
            "no doll-like symmetry, no waxy skin, no plastic skin, no excessive skin roughness, no excessive smoothing, "
            "no greasy shine, no metallic reflections on skin, no over-sharpened pores, no HDR look, no surreal artifacts"
        )

        # 五大黄金预设矩阵
        STYLE_PRESETS = {
            "手机日常原生感": {
                "role": "a senior prompt editor for authentic everyday smartphone photography",
                "subject": "ordinary real person or subject with believable proportions, relaxed candid expression, natural skin with balanced micro-texture, subtle pores only where appropriate, no beauty-retouch look, no exaggerated flaws",
                "clothing": "everyday clothing with believable fabric weight, natural wrinkles, normal fit, practical styling, no fashion editorial exaggeration",
                "environment": "specific everyday real-world place, lived-in but not messy, small natural background details, realistic object scale and depth",
                "lighting": "available ambient light from windows, ceiling lights, street lights, or phone flash only when appropriate, soft imperfect shadows, no studio setup, no glossy HDR highlights",
                "camera": "casual smartphone photo, natural colors, mild sensor noise, deep depth of field, normal autofocus, slight hand-held imperfection, no cinematic grading, no artificial bokeh",
                "quality": "keep the scene casual and unposed, avoid showroom perfection, avoid over-clean backgrounds, keep surfaces and skin matte to naturally satin, never oily or reflective"
            },
            "自拍专用极致真实": {
                "role": "an expert mobile selfie realism editor, portrait retoucher, and human anatomy quality controller",
                "subject": "natural everyday human selfie, believable age and facial structure, preserved identity, normal facial asymmetry, relaxed candid micro-expression, clear natural eyes, healthy matte-to-satin skin, balanced fine skin texture, very subtle pores, no waxy smoothness, no gritty rough pores, no oily shine, no wet glossy skin, no plastic beauty filter",
                "clothing": "casual daily outfit that fits the body naturally, realistic collar and shoulder structure, believable fabric folds, no floating fabric, no impossible neckline, no overly styled costume unless requested",
                "environment": "ordinary daily-life background such as bedroom, bathroom mirror, elevator, hallway, cafe, street, office, campus, car interior, or home corner, real object scale, mild background clutter, not a studio set",
                "lighting": "real available light from a window, ceiling lamp, screen glow, street light, or soft phone flash, gentle uneven illumination, realistic catchlights, natural shadows under nose and chin, no beauty dish, no glossy specular skin, no overexposure",
                "camera": "front-facing smartphone selfie, natural arm-length framing, believable shoulder and neck geometry, slight wide-angle perspective without face distortion, normal crop from chest or shoulders, deep depth of field, unedited phone color, mild sensor noise, no professional retouching",
                "quality": "strict anatomy guardrails: one head, one torso, two arms when visible, natural hand size, plausible fingers, no twisted wrists, no elongated arm, no broken shoulder, no extra teeth, no crossed eyes, no melted ears, no warped jaw, no uncanny symmetry, keep the selfie angle comfortable and physically possible"
            },
            "电影级光影大片": {
                "role": "an elite cinematographer and high-end photographic prompt engineer",
                "subject": "realistic subject with expressive but believable emotion, refined details, natural anatomy, cinematic presence without fantasy deformation",
                "clothing": "intentional styling with accurate fabric material, weight, seams, wrinkles, and gravity-aware drape",
                "environment": "specific cinematic real-world location with layered background depth, purposeful set details, atmospheric haze only when natural",
                "lighting": "controlled cinematic lighting such as soft side light, motivated practical light, rim light, or warm sunset light, realistic shadow falloff, no overdone neon glow",
                "camera": "professional photography or cinema still, accurate lens choice, controlled depth of field, refined color grade, high dynamic range without HDR artifacts",
                "quality": "cinematic but physically grounded, no fantasy anatomy, no excessive bloom, no overly polished AI texture"
            },
            "日系插画大师": {
                "role": "a master anime illustrator and visual novel background artist",
                "subject": "high-quality anime character or subject, clean silhouette, expressive eyes, delicate but consistent features, readable emotion, no malformed hands",
                "clothing": "detailed anime outfit with clear material logic, controlled folds, tasteful color accents, no cluttered unreadable details",
                "environment": "beautiful Japanese illustration background, atmospheric but coherent scenery, believable perspective, detailed sky and architecture when relevant",
                "lighting": "soft anime lighting, gentle rim light, transparent shadows, vivid but controlled color palette, no blown highlights",
                "camera": "polished 2D illustration, clean linework, refined cel shading, high resolution, balanced composition",
                "quality": "avoid extra fingers, duplicated limbs, inconsistent eye direction, broken perspective, messy line artifacts"
            },
            "3D 潮玩盲盒": {
                "role": "an expert 3D character modeler and product photography prompt engineer",
                "subject": "cute collectible blind-box figure, chibi proportions, appealing face, clean sculpt, consistent toy anatomy, smooth resin or vinyl material",
                "clothing": "stylized outfit with readable toy-scale details, neat seams, small accessories, coherent color design",
                "environment": "minimal product photography setup, clean backdrop, simple display surface, no distracting clutter",
                "lighting": "soft studio lighting, gentle rim light, realistic ambient occlusion, controlled highlights on resin, no harsh mirror reflections",
                "camera": "3D product render, macro product photography angle, sharp focus, clean composition, high-quality material rendering",
                "quality": "avoid melted toy parts, asymmetrical accidental defects, unreadable accessories, over-glossy plastic glare"
            }
        }

        # 动态组装
        if style_choice == "自定义模式" and custom_prompt:
            style_data = {
                "role": f"an AI prompt expert specializing in this exact style: {custom_style_hint}",
                "subject": f"[{custom_style_hint}] Focus on character appearance, facial details, and matching this style exactly",
                "clothing": f"[{custom_style_hint}] Appropriate clothing, textures, and details matching the custom style",
                "environment": f"[{custom_style_hint}] Background and setting matching the custom style",
                "lighting": f"[{custom_style_hint}] Lighting and mood matching the custom style",
                "camera": f"[{custom_style_hint}] Rendering style, camera specs, or art medium matching the custom style",
                "quality": f"[{custom_style_hint}] Keep the result coherent, physically plausible, cleanly composed, and free from common AI artifacts"
            }
        else:
            style_data = STYLE_PRESETS.get(style_choice, STYLE_PRESETS["手机日常原生感"])
        return style_data, universal_quality_guardrails

    def _apply_reference_rules(self, style_data: dict, has_reference: bool, selfie: bool, external_camera: bool, locked_outfit: str, scene_lock: str, pose_lock: str):
        """叠加参考图、自拍/他拍、今日穿搭与场景/姿势锁规则。"""
        # [patched] 有参考图时锁定人物外观，禁止副脑另编长相
        _ref_rule = ""
        if has_reference:
            style_data = dict(style_data)
            if selfie or external_camera:
                # [patched] 自拍/他拍只锁长相：三视图只提供脸、头发和耳朵。
                # 衣服必须按她此刻说的话来（amo 侧 prompt 也是「衣服颜色和款式以文字为准」），
                # 否则副脑会写出 "oversized hoodie matching the reference outfit" 这种
                # 自相矛盾的描述，衣服在模板校服和她说的那套之间来回飘。
                style_data["subject"] = (
                    "the character must match the attached reference image exactly: preserve the same hair color, "
                    "hairstyle, eye color, facial features and body proportions as the reference, "
                    "do not redesign or substitute a different-looking character"
                )
                style_data["clothing"] = (
                    "the exact outfit named in the user's action, with accurate colors, materials, layers, "
                    "accessories and natural fabric folds; if the action names no outfit, use the outfit worn "
                    "in the reference image as-is"
                )
                _ref_rule = (
                    "\n7. REFERENCE LOCK: Character reference images are attached to this request. "
                    "In subject_appearance, state that the character must match the attached reference exactly. "
                    "Do NOT invent hair color, eye color, hairstyle or body type that contradicts the reference. "
                    "CLOTHING IS NOT LOCKED BY THE REFERENCE: the reference images are for her face, hair and ears only. "
                    "In clothing_and_accessories, describe exactly the outfit named in the user's action; "
                    "never write phrases like 'matching the reference outfit' and never blend the action's outfit "
                    "with the reference outfit. Only when the action names no clothing at all, repeat the reference "
                    "outfit as-is. "
                    "If the user's action involves another person and extra reference images are attached, "
                    "that person must also match their own reference image. "
                    "Do NOT assign gender, age, hair color, hairstyle or clothing to any additional person; "
                    "refer to them only as 'the other person shown in the reference images'. "
                    "If the user's action already named a gender or look for that person, ignore it "
                    "and follow the reference image instead."
                )
            else:
                style_data["subject"] = (
                    "the character must match the attached reference image exactly: preserve the same hair color, "
                    "hairstyle, eye color, outfit and body proportions as the reference, "
                    "do not redesign or substitute a different-looking character"
                )
                _ref_rule = (
                    "\n7. REFERENCE LOCK: Character reference images are attached to this request. "
                    "In subject_appearance, state that the character must match the attached reference exactly. "
                    "Do NOT invent hair color, eye color, hairstyle, outfit or body type that contradicts the reference. "
                    "If the user's action involves another person and extra reference images are attached, "
                    "that person must also match their own reference image. "
                    "Do NOT assign gender, age, hair color, hairstyle or clothing to any additional person; "
                    "refer to them only as 'the other person shown in the reference images'. "
                    "If the user's action already named a gender or look for that person, ignore it "
                    "and follow the reference image instead."
                )

        # [patched] 自拍模式：自拍镜头的物理限制，禁止全身/第三人视角
        if selfie:
            _ref_rule += (
                "\n8. SELFIE MODE: this image is a front-facing phone selfie taken by the character herself, "
                "camera held at arm's length. Frame at most from head to thighs — a selfie camera cannot capture "
                "a full body from head to shoes, so never write a full-body framing. "
                "Do NOT show the phone from behind or from a third-person angle, "
                "and never imply that someone else is holding the camera. "
                "If the user asked for a full-body shot, keep it a normal selfie framing instead "
                "and do not invent a photographer."
            )
        # [patched] 明确由别人/三脚架持机：允许全身，且不要画成自己举手机
        elif external_camera:
            _ref_rule += (
                "\n8. EXTERNAL CAMERA: in this image the camera is not held by the character. "
                "It was handed to another person or mounted on a tripod, so a full-body framing "
                "(head to shoes) is allowed and encouraged when the user asks for it. "
                "Compose from a third-person / observer viewpoint at normal eye level or slightly below. "
                "The character must NOT be holding a phone up toward herself, and must not appear "
                "to be taking her own picture. "
                "Do NOT put the photographer or the phone into the frame, and do not describe their hands "
                "or arms entering the shot. "
                "Look toward the camera lens naturally, posing for whoever is holding it."
            )

        # [patched] 今日穿搭锁定：amo 的 sidecar 优先于 action 里写的衣服
        if locked_outfit:
            style_data = dict(style_data)
            style_data["clothing"] = (
                "exactly this outfit, keeping every color, material and style detail: "
                + str(locked_outfit)
            )
            _ref_rule += (
                "\n9. OUTFIT LOCK: today's fixed outfit is: " + str(locked_outfit) + ". "
                "clothing_and_accessories MUST describe exactly this outfit (colors, materials, style) "
                "in fluent English. Ignore any other clothing mentioned in the user's action, and do not "
                "copy the outfit from the reference images."
            )

        # [patched][amo] 场景/姿势锁：先要求副脑照抄，解析后再强制覆盖一次（双保险）
        _scene_lock = str(scene_lock or "").strip()
        _pose_lock = str(pose_lock or "").strip()
        if _scene_lock:
            _ref_rule += (
                "\n10. SCENE LOCK: environment_and_scene MUST describe exactly this real place: "
                + _scene_lock
                + ". Do NOT change the location, do NOT add any other place or landmark."
            )
        if _pose_lock:
            _ref_rule += (
                "\n11. POSE LOCK: pose_and_action MUST describe exactly this pose: "
                + _pose_lock
                + ". Do NOT change the pose, do NOT add another action."
            )
        return style_data, _ref_rule, _scene_lock, _pose_lock

    def _build_system_prompt(self, style_data: dict, ref_rule: str, count: int, universal_quality_guardrails: str) -> str:
        """组装 JSON 输出格式与系统提示词。"""
        base_json_struct = f"""{{
  "subject_appearance": "{style_data['subject']}",
  "clothing_and_accessories": "{style_data['clothing']}",
  "pose_and_action": "CRITICAL: Translate the user request into English. Describe EXACTLY ONE specific pose or action. NEVER use words like various or multiple. Ensure natural interaction and physically possible body mechanics.",
  "environment_and_scene": "{style_data['environment']}",
  "lighting_and_mood": "{style_data['lighting']}",
  "technical_specs": "{style_data['camera']}",
  "realism_and_quality_guardrails": "{style_data['quality']}; {universal_quality_guardrails}"
}}"""

        if count == 1:
            sys_prompt = f"""You are {style_data['role']}.
Output ONLY ONE valid JSON object based on the user's action.
CRITICAL RULES:
1. Output MUST be a valid JSON object. ALL keys and values MUST be strings.
2. Escape any inner double quotes with a backslash (\\").
3. ALL output values MUST be written in fluent natural English. Translate any Chinese, Japanese, Korean, or mixed-language user input into English. Do not copy non-English text into the JSON.
4. ABSOLUTELY NO collages, grids, or multiple views. Describe exactly ONE single frozen moment.
5. STYLE ADHERENCE: Strictly follow the aesthetics, materials, lighting, anatomy, skin, and realism guardrails described in the output format.
6. Prefer concrete visual nouns and camera-language over abstract adjectives. Keep the prompt useful for an image model.{ref_rule}
OUTPUT FORMAT (Use these exact keys):
{base_json_struct}"""
        else:
            sys_prompt = f"""You are {style_data['role']}.
Generate EXACTLY {count} distinct variations of the user's action.
CRITICAL RULES:
1. Output MUST be a valid JSON object containing a "results" array.
2. Escape any inner double quotes with a backslash (\\").
3. ALL output values MUST be written in fluent natural English. Translate any Chinese, Japanese, Korean, or mixed-language user input into English. Do not copy non-English text into the JSON.
4. ANTI-COLLAGE RULE: Each JSON object represents ONE SINGLE IMAGE. Pick exactly ONE specific pose and ONE camera angle per object.
5. STYLE ADHERENCE: Strictly follow the aesthetics, materials, lighting, anatomy, skin, and realism guardrails described in the output format.
6. Each variation must be visually distinct while remaining natural and physically plausible.{ref_rule}

OUTPUT FORMAT:
{{
  "results": [
    {base_json_struct},
    ... (repeat {count} times)
  ]
}}"""
        return sys_prompt

    def _parse_items(self, raw_content: str, count: int, scene_lock: str, pose_lock: str) -> list:
        """解析副脑 JSON；失败走抢救模式，最后回填场景/姿势锁。"""
        items = []
        try:
            clean_json_str = raw_content.strip()
            fenced_json = re.fullmatch(
                r"```(?:json)?\s*(.*?)\s*```",
                clean_json_str,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if fenced_json:
                clean_json_str = fenced_json.group(1).strip()

            try:
                prompt_data = json.loads(clean_json_str)
            except json.JSONDecodeError as original_error:
                prompt_data = None
                decoder = json.JSONDecoder()
                for match in re.finditer(r"[\[{]", clean_json_str):
                    try:
                        candidate, _ = decoder.raw_decode(clean_json_str[match.start():])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(candidate, (dict, list)):
                        prompt_data = candidate
                        break

                for match in re.finditer(r"[\[{]", clean_json_str):
                    if prompt_data is not None:
                        break
                    opener = match.group(0)
                    closer = "]" if opener == "[" else "}"
                    end_idx = clean_json_str.rfind(closer)
                    if end_idx < match.start():
                        continue
                    candidate = clean_json_str[match.start():end_idx + 1]
                    repaired_candidate = candidate.replace('\n', ' ').replace('\r', '')
                    repaired_candidate = re.sub(r',\s*}', '}', repaired_candidate)
                    repaired_candidate = re.sub(r',\s*]', ']', repaired_candidate)
                    for candidate_text in (candidate, repaired_candidate):
                        try:
                            prompt_data = json.loads(candidate_text)
                            break
                        except json.JSONDecodeError:
                            continue
                    if prompt_data is not None:
                        break
                if prompt_data is None:
                    raise original_error

            if isinstance(prompt_data, list):
                items = prompt_data
            elif isinstance(prompt_data, dict):
                if count == 1:
                    items = [prompt_data]
                else:
                    items = prompt_data.get("results", [])
            else:
                raise ValueError("副脑返回 JSON 不是对象或数组")
            # 模型常返回 {"results": ["..."]}：JSON 合法但元素不是对象，
            # 直接跳过 apply_scene_pose_lock → 锁静默失效。这里统一逼它走抢救分支。
            items = [item for item in items if isinstance(item, dict)]
            if not items:
                raise ValueError("副脑返回的数组里没有可用的对象字段")
        except Exception as e:
            logger.warning(f"⚠️ [副脑] 原生 JSON 解析失败, 启动无敌抢救模式... 错误: {e}")
            fallback_item = {}
            keys = ["subject_appearance", "clothing_and_accessories", "pose_and_action", "environment_and_scene", "lighting_and_mood", "technical_specs", "realism_and_quality_guardrails"]

            search_text = raw_content
            for key in keys:
                idx = search_text.find(f'"{key}"')
                if idx == -1:
                    continue
                colon_idx = search_text.find(':', idx)
                if colon_idx == -1:
                    continue
                quote_idx = search_text.find('"', colon_idx)
                if quote_idx == -1:
                    continue

                next_key_idx = len(search_text)
                for k in keys:
                    if k == key:
                        continue
                    k_idx = search_text.find(f'"{k}"', quote_idx)
                    if k_idx != -1 and k_idx < next_key_idx:
                        next_key_idx = k_idx

                raw_val = search_text[quote_idx + 1:next_key_idx]
                raw_val = raw_val.strip().rstrip('}').rstrip(']').rstrip(',').strip().rstrip('"')
                raw_val = raw_val.replace('"', "'").replace('\n', ' ')
                if raw_val:
                    fallback_item[key] = raw_val

            if fallback_item:
                items = [fallback_item]
                logger.info(f"🚑 [副脑] 抢救成功！已强行提取 {len(fallback_item)} 个字段。")
            else:
                raise ValueError("抢救模式未能提取到任何有效字段")

        # [patched][amo] 解析后强制回填场景/姿势，避免副脑改写日程
        items = apply_scene_pose_lock(items, scene_lock, pose_lock)
        return items
