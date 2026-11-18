# 我能永远定格你 · Astrbot_amoupup_photo

> **我能永远定格你**（独立生图插件，AstrBot v4）：文生图、图生图、人设自拍、GIF 动图、视频生成一站搞定。
> **与 amo（Astrbot_amoupup_forever）只做握手接入，不进入 amo 代码库。**

- 当前版本：**3.6.0**（[更新日志](CHANGELOG.md#360)）
- 插件 id：`Astrbot_amoupup_photo`
- 作者：彩虹upup
- 跑测试：`pytest tests -q`（插件根目录放 PYTHONPATH）
- 命令兼容：新命令 `/定格帮助`，旧命令 `/万象帮助` 保留为别名，老用户肌肉记忆不断。

## 简介

配置好画图模型接口后，在群聊或私聊里发一句描述，就能让机器人画图、按参考图改图、按固定人设自拍，或者提交视频生成任务。你可以把它理解成一个「画图工作台」：模型、链路、人设、预设、额度和缓存都在配置页里管。

## 主要功能

- **文生图**：发一句描述，模型直接出图。
- **图生图 / 改图**：带参考图和提示词，按图修改或重绘。
- **原生 GIF 动图**：`/gif` 支持文生动图与参考图动图，LLM 工具也能自主选择 GIF。
- **人设自拍**：配置页上传人设参考图，`/自拍` 生成固定角色的日常照片。
- **多个人设**：每组人设独立名称、基础描述和参考图组，可随时切换。
- **副脑优化**：把简单中文描述改写成更适合画图模型的英文提示词。
- **多模型热切换**：画图、自拍、视频链路都可以放多个模型，由管理员随时切换。
- **预设指令管理**：查看预设名/详情，管理员可增删预设。
- **视频生成**：提交后后台等待，完成自动推送结果。
- **权限控制**：可使用人员白名单限制使用者；用户白名单只负责不限次数。
- **指令回复自定义**：等待文案、报错文案可单独改，留空恢复默认。
- **成功结果信息**：可显示生成耗时与请求模型，两个开关独立。
- **QQ 图片处理**：自动下载 QQ 图片到本地，减少链接失效/防盗链/400。
- **缓存清理**：配置页一键清理，支持定时清理、容量上限和管理员指令。

## 安装

1. 把整个目录复制到 `astrbot/data/plugins/Astrbot_amoupup_photo/`，或在 AstrBot 插件市场 / GitHub 仓库安装：

   ```text
   https://github.com/CAIHONHUPUP/Astrbot_amoupup_photo/
   ```

2. 重启 core 或重载插件，日志出现「我能永远定格你 v3.6.0」即加载成功。
3. 打开配置页填入画图模型（OpenAI / Gemini / 自定义端点均可），保存。

插件元数据声明支持 AstrBot `>=4.16,<5`；本轮部署验证使用 AstrBot `4.29.1` / Python `3.12.15`，不代表已逐一验证所有旧版宿主。运行依赖见 `requirements.txt`；配置页已附带构建资源，安装和升级都不需要 Node.js。

## 常用指令

| 指令 | 说明 |
| --- | --- |
| `/画 <描述>` | 文生图 |
| `/自拍 [描述]` | 按当前人设自拍 |
| `/视频 <描述>` | 提交视频生成任务 |
| `/gif <描述>` | 生成 GIF 动图 |
| `/定格帮助`（别名 `/万象帮助`） | 查看插件帮助 |
| `/切换人设 <名称>` | 管理员切换当前人设 |

完整指令与参数以 `/定格帮助`（或 `/万象帮助`）输出为准。

## 与 amo 的握手

amo（`Astrbot_amoupup_forever`）用 `context.get_registered_star(...)` 找本插件：**优先新 id `Astrbot_amoupup_photo`，找不到时回退旧 id `astrbot_plugin_omnidraw`（legacy fallback，仅回退用，不是当前名字）**，所以改名前后两个版本都能握上手，且同一时刻只取一个实例。

- 握手能力：`generate_images_for_plugin(...)`、`_apply_runtime_config(...)`、`_persist_config()`
- 今日穿搭 sidecar：`data/plugin_data/Astrbot_amoupup_photo/amo_outfit.json`（旧目录只读回退，不改不删）
- 三视图/参考图同步走插件自己的配置入口，amo 不直接改配置文件

## 数据与配置

- 原生配置（AstrBot 按插件名生成）：`data/config/Astrbot_amoupup_photo_config.json`
- 插件数据目录：`data/plugin_data/Astrbot_amoupup_photo/`（持久化配置、使用统计、人设参考图、缓存）
- 持久化配置文件名：`omnidraw_persist_config.json`（沿用旧文件名，便于直接搬数据）
- 配置页：`pages/插件配置/`，静态资源已构建，无需 Node.js

**从旧版（`astrbot_plugin_omnidraw`，legacy fallback）升级**：先备份旧插件目录、`data/config/astrbot_plugin_omnidraw_config.json` 和 `data/plugin_data/astrbot_plugin_omnidraw/`（旧数据路径，legacy fallback），再把配置与数据复制到新名字下。旧字段转换、归一化和安全过滤在加载时自动执行。

## 目录结构

    Astrbot_amoupup_photo/
    ├── main.py               入口：插件类 + 生命周期 + mixin 组装
    ├── main_config.py        配置与页面 API
    ├── main_commands.py      聊天指令
    ├── main_tasks.py         视频等后台任务
    ├── main_cache.py         图片缓存与租约
    ├── models.py             数据模型与配置归一化（PLUGIN_NAME / 版本号）
    ├── constants.py          常量单源
    ├── core/                 链路、解析、人设、副脑、视频
    ├── providers/            OpenAI / Gemini / 自定义端点
    ├── pages/插件配置/        Web 配置页（静态资源已构建）
    ├── docs/                 设计与验证文档
    └── tests/                单元与页面回归测试

## 测试

    PYTHONPATH=. python -m pytest tests -q

页面回归（可选，需 Playwright）：`python tests/page_ui_smoke.py --channel chrome`。

## 作者与致谢

- 作者：彩虹upup
- **感谢万象画卷作者雪碧**：本插件的生图链路、人设自拍、GIF/视频与配置页均源自万象画卷，改名与接口兼容改造在此之上完成。

## 许可

配置页的 React 组件复用自 [CookSleep/gpt_image_playground](https://github.com/CookSleep/gpt_image_playground)（MIT）；固定来源版本、逐文件 SHA-256 与许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
