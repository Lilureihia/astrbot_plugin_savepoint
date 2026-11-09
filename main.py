import asyncio
import base64
import datetime
import json
import os
import shutil
import uuid
from typing import List, Optional

from aiohttp import web
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register

try:
    from astrbot.api.web import error_response, json_response, request as ab_request
except ImportError:
    ab_request = None
    json_response = None
    error_response = None

PLUGIN_ID = "astrbot_plugin_savepoint"
PLUGIN_VERSION = "1.1.0"


@register(
    PLUGIN_ID,
    "白昼夢",
    "手动存入当前节点重要的图文信息",
    PLUGIN_VERSION,
)
class SavePointPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config if config is not None else {}

        self.enable_plugin: bool = bool(self.config.get("enable_plugin", True))
        self.webui_host: str = str(self.config.get("webui_host", "127.0.0.1")).strip() or "127.0.0.1"
        self.webui_port: int = int(self.config.get("webui_port", 8765))
        self.inject_images: bool = bool(self.config.get("inject_images", True))
        self.inject_position: str = str(self.config.get("inject_position", "tail")).strip()

        # 严格遵循 AstrBot 规范的数据持久化路径
        self.data_dir: str = os.path.join("data", "plugin_data", PLUGIN_ID)
        self.images_dir: str = os.path.join(self.data_dir, "images")
        os.makedirs(self.images_dir, exist_ok=True)
        self.db_file: str = os.path.join(self.data_dir, "savepoint_db.json")

        self.plugin_root: str = os.path.dirname(os.path.abspath(__file__))
        self._ensure_native_view_dirs()

        self.db: dict = self._load_db()

        # 1. 注册 AstrBot 官方原生插件视图 API (context.register_web_api)
        self._register_native_web_apis()

        # 2. 启动独立端口服务 (8765)
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._start_webui_task()

        logger.info(
            f"[存档点 SavePoint v{PLUGIN_VERSION}] 插件已就绪！视图目录: views/存档点/index.html"
        )

    def _ensure_native_view_dirs(self):
        """确保 views/存档点/index.html 与 pages/存档点/index.html 存在，触发 AstrBot 原生扩展功能识别"""
        candidates = [
            os.path.join(self.plugin_root, "views", "存档点", "index.html"),
            os.path.join(self.plugin_root, "pages", "存档点", "index.html"),
            os.path.join(self.plugin_root, "webui", "index.html"),
        ]
        src_html = next((p for p in candidates if os.path.exists(p)), None)
        if not src_html:
            return

        for folder in ("views", "pages"):
            target_dir = os.path.join(self.plugin_root, folder, "存档点")
            os.makedirs(target_dir, exist_ok=True)
            target_file = os.path.join(target_dir, "index.html")
            if os.path.abspath(src_html) != os.path.abspath(target_file):
                try:
                    shutil.copyfile(src_html, target_file)
                except Exception as e:
                    logger.warning(f"[存档点] 同步视图目录 {target_file} 异常: {e}")

    def _register_native_web_apis(self):
        reg_fn = getattr(self.context, "register_web_api", None)
        if not callable(reg_fn) or ab_request is None:
            return
        try:
            reg_fn(f"/{PLUGIN_ID}/state", self._ab_get_state, ["GET"], "获取存档点状态")
            reg_fn(f"/{PLUGIN_ID}/tag/save", self._ab_save_tag, ["POST"], "保存TAG")
            reg_fn(f"/{PLUGIN_ID}/tag/delete", self._ab_delete_tag, ["POST"], "删除TAG")
            reg_fn(f"/{PLUGIN_ID}/slot/save", self._ab_save_slot, ["POST"], "保存存档槽")
            reg_fn(f"/{PLUGIN_ID}/slot/activate", self._ab_activate_slot, ["POST"], "激活存档槽")
            reg_fn(f"/{PLUGIN_ID}/slot/copy", self._ab_copy_slot, ["POST"], "复制存档槽")
        except Exception as e:
            logger.warning(f"[存档点] 注册原生 Web API 异常: {e}")

    def _default_db(self) -> dict:
        """通用、去敏的默认初始化模板"""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        return {
            "active_slot_id": "slot_1",
            "tags": {
                "tag_default_example": {
                    "id": "tag_default_example",
                    "tag": "当前场景补充",
                    "title": "默认通用示例（仅面板备注，可随时修改或删除）",
                    "description": "当前处于日常交流场景中，可在此记录需要模型临时知晓的客观背景或参考图说明。",
                    "prompt": "请结合上述补充背景自然地展开对话，保持原有角色性格与设定的一致性。",
                    "image": "",
                    "updated_at": now_str,
                }
            },
            "slots": {
                "slot_1": {
                    "id": "slot_1",
                    "name": "存档槽 1 · 默认主线",
                    "tag_ids": ["tag_default_example"],
                    "slot_note": "",
                    "updated_at": now_str,
                },
                "slot_2": {
                    "id": "slot_2",
                    "name": "存档槽 2 · 分支预设 A",
                    "tag_ids": [],
                    "slot_note": "",
                    "updated_at": now_str,
                },
                "slot_3": {
                    "id": "slot_3",
                    "name": "存档槽 3 · 分支预设 B",
                    "tag_ids": [],
                    "slot_note": "",
                    "updated_at": now_str,
                },
                "slot_4": {
                    "id": "slot_4",
                    "name": "存档槽 4 · 备用快照",
                    "tag_ids": [],
                    "slot_note": "",
                    "updated_at": now_str,
                },
            },
        }

    def _load_db(self) -> dict:
        if not os.path.exists(self.db_file):
            default_data = self._default_db()
            self._save_db(default_data)
            return default_data
        try:
            with open(self.db_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "slots" not in data or "tags" not in data:
                return self._default_db()
            return data
        except Exception:
            return self._default_db()

    def _save_db(self, data: Optional[dict] = None):
        target = data if data is not None else self.db
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            with open(self.db_file, "w", encoding="utf-8") as f:
                json.dump(target, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[存档点] 保存数据库失败: {e}")

    def _get_enriched_state(self) -> dict:
        state_copy = json.loads(json.dumps(self.db))
        for tid, t_obj in state_copy.get("tags", {}).items():
            img_name = t_obj.get("image", "")
            if img_name:
                img_path = os.path.join(self.images_dir, os.path.basename(img_name))
                if os.path.exists(img_path):
                    try:
                        ext = os.path.splitext(img_name)[1].lower().lstrip(".") or "png"
                        mime = f"image/{'jpeg' if ext == 'jpg' else ext}"
                        with open(img_path, "rb") as f:
                            b64 = base64.b64encode(f.read()).decode("ascii")
                        t_obj["image_data_url"] = f"data:{mime};base64,{b64}"
                    except Exception:
                        t_obj["image_data_url"] = ""
        return state_copy

    def _logic_save_tag(self, body: dict) -> tuple:
        tag_id = str(body.get("id", "")).strip() or f"tag_{uuid.uuid4().hex[:8]}"
        tag_name = str(body.get("tag", "")).strip()
        if not tag_name:
            return False, "TAG 名称不能为空", None

        title = str(body.get("title", "")).strip()
        description = str(body.get("description", "")).strip()
        prompt = str(body.get("prompt", "")).strip()
        image_base64 = str(body.get("image_base64", "")).strip()
        clear_image = bool(body.get("clear_image", False))

        existing = self.db.get("tags", {}).get(tag_id, {})
        image_filename = existing.get("image", "")

        if clear_image and image_filename:
            old_path = os.path.join(self.images_dir, os.path.basename(image_filename))
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except Exception:
                    pass
            image_filename = ""

        if image_base64 and "," in image_base64:
            header, b64_data = image_base64.split(",", 1)
            ext = ".png"
            if "jpeg" in header or "jpg" in header:
                ext = ".jpg"
            elif "webp" in header:
                ext = ".webp"
            elif "gif" in header:
                ext = ".gif"

            image_filename = f"{tag_id}_{uuid.uuid4().hex[:6]}{ext}"
            img_save_path = os.path.join(self.images_dir, image_filename)
            with open(img_save_path, "wb") as img_f:
                img_f.write(base64.b64decode(b64_data))

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        self.db.setdefault("tags", {})[tag_id] = {
            "id": tag_id,
            "tag": tag_name,
            "title": title,
            "description": description,
            "prompt": prompt,
            "image": image_filename,
            "updated_at": now_str,
        }

        mount_slot_id = str(body.get("mount_to_slot", "")).strip()
        if mount_slot_id and mount_slot_id in self.db.get("slots", {}):
            slot_tags = self.db["slots"][mount_slot_id].get("tag_ids", [])
            if tag_id not in slot_tags:
                slot_tags.append(tag_id)
                self.db["slots"][mount_slot_id]["tag_ids"] = slot_tags
                self.db["slots"][mount_slot_id]["updated_at"] = now_str

        self._save_db()
        return True, "ok", tag_id

    def _logic_delete_tag(self, body: dict):
        tag_id = str(body.get("id", "")).strip()
        if tag_id in self.db.get("tags", {}):
            tag_obj = self.db["tags"].pop(tag_id)
            img_file = tag_obj.get("image", "")
            if img_file:
                img_path = os.path.join(self.images_dir, os.path.basename(img_file))
                if os.path.exists(img_path):
                    try:
                        os.remove(img_path)
                    except Exception:
                        pass
            for slot in self.db.get("slots", {}).values():
                if tag_id in slot.get("tag_ids", []):
                    slot["tag_ids"].remove(tag_id)
            self._save_db()

    def _logic_save_slot(self, body: dict) -> str:
        slot_id = str(body.get("id", "")).strip() or f"slot_{uuid.uuid4().hex[:6]}"
        name = str(body.get("name", "")).strip() or "未命名存档槽"
        slot_note = str(body.get("slot_note", "")).strip()
        tag_ids = body.get("tag_ids", [])
        if not isinstance(tag_ids, list):
            tag_ids = []
        valid_tag_ids = [tid for tid in tag_ids if tid in self.db.get("tags", {})]

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        self.db.setdefault("slots", {})[slot_id] = {
            "id": slot_id,
            "name": name,
            "slot_note": slot_note,
            "tag_ids": valid_tag_ids,
            "updated_at": now_str,
        }
        self._save_db()
        return slot_id

    def _logic_activate_slot(self, body: dict) -> bool:
        slot_id = str(body.get("slot_id", "")).strip()
        if slot_id == "none" or slot_id in self.db.get("slots", {}):
            self.db["active_slot_id"] = slot_id
            self._save_db()
            return True
        return False

    def _logic_copy_slot(self, body: dict) -> bool:
        from_slot_id = str(body.get("from_slot_id", "")).strip()
        to_slot_id = str(body.get("to_slot_id", "")).strip()
        slots = self.db.get("slots", {})
        if from_slot_id not in slots or to_slot_id not in slots:
            return False
        src = slots[from_slot_id]
        dst = slots[to_slot_id]
        dst["tag_ids"] = list(src.get("tag_ids", []))
        dst["slot_note"] = src.get("slot_note", "")
        dst["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        self._save_db()
        return True

    # ==================== AstrBot 原生 Web API ====================

    async def _ab_get_state(self):
        return json_response({"status": "ok", "data": self._get_enriched_state()})

    async def _ab_save_tag(self):
        body = await ab_request.json(default={})
        ok, msg, tag_id = self._logic_save_tag(body)
        if not ok:
            return error_response(msg, status=400)
        return json_response({"status": "ok", "tag_id": tag_id, "data": self._get_enriched_state()})

    async def _ab_delete_tag(self):
        body = await ab_request.json(default={})
        self._logic_delete_tag(body)
        return json_response({"status": "ok", "data": self._get_enriched_state()})

    async def _ab_save_slot(self):
        body = await ab_request.json(default={})
        slot_id = self._logic_save_slot(body)
        return json_response({"status": "ok", "slot_id": slot_id, "data": self._get_enriched_state()})

    async def _ab_activate_slot(self):
        body = await ab_request.json(default={})
        if self._logic_activate_slot(body):
            return json_response({"status": "ok", "data": self._get_enriched_state()})
        return error_response("目标槽位不存在", status=400)

    async def _ab_copy_slot(self):
        body = await ab_request.json(default={})
        if self._logic_copy_slot(body):
            return json_response({"status": "ok", "data": self._get_enriched_state()})
        return error_response("源槽位或目标槽位不存在", status=400)

    # ==================== 独立端口服务 (8765) ====================

    def _start_webui_task(self):
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._init_webserver())
        except RuntimeError:
            asyncio.create_task(self._init_webserver())

    @web.middleware
    async def _cors_middleware(self, request: web.Request, handler):
        if request.method == "OPTIONS":
            resp = web.Response(status=200)
        else:
            resp = await handler(request)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return resp

    async def _init_webserver(self):
        try:
            app = web.Application(middlewares=[self._cors_middleware], client_max_size=20 * 1024 * 1024)
            app.router.add_get("/", self._handle_index)
            app.router.add_get("/api/state", self._handle_get_state)
            app.router.add_post("/api/tag/save", self._handle_save_tag)
            app.router.add_post("/api/tag/delete", self._handle_delete_tag)
            app.router.add_post("/api/slot/save", self._handle_save_slot)
            app.router.add_post("/api/slot/activate", self._handle_activate_slot)
            app.router.add_post("/api/slot/copy", self._handle_copy_slot)
            app.router.add_get("/api/images/{filename}", self._handle_get_image)
            app.router.add_get("/api/logo", self._handle_get_logo)

            self._runner = web.AppRunner(app)
            await self._runner.setup()
            self._site = web.TCPSite(self._runner, self.webui_host, self.webui_port)
            await self._site.start()
        except Exception as e:
            logger.debug(f"[存档点] 端口 {self.webui_port} 未启动: {e}")

    async def terminate(self):
        if self._runner:
            try:
                await self._runner.cleanup()
            except Exception:
                pass

    async def _handle_index(self, request: web.Request):
        for sub in ("views/存档点", "pages/存档点", "webui"):
            p = os.path.join(self.plugin_root, sub, "index.html")
            if os.path.exists(p):
                return web.FileResponse(p)
        return web.Response(text="SavePoint WebUI index.html not found.", status=404)

    async def _handle_get_logo(self, request: web.Request):
        logo_path = os.path.join(self.plugin_root, "logo.png")
        if os.path.exists(logo_path):
            return web.FileResponse(logo_path)
        return web.Response(status=404)

    async def _handle_get_image(self, request: web.Request):
        filename = request.match_info.get("filename", "")
        safe_name = os.path.basename(filename)
        img_path = os.path.join(self.images_dir, safe_name)
        if safe_name and os.path.exists(img_path):
            return web.FileResponse(img_path)
        return web.Response(status=404)

    async def _handle_get_state(self, request: web.Request):
        return web.json_response({"status": "ok", "data": self._get_enriched_state()})

    async def _handle_save_tag(self, request: web.Request):
        body = await request.json()
        ok, msg, tag_id = self._logic_save_tag(body)
        if not ok:
            return web.json_response({"status": "error", "message": msg}, status=400)
        return web.json_response({"status": "ok", "tag_id": tag_id, "data": self._get_enriched_state()})

    async def _handle_delete_tag(self, request: web.Request):
        body = await request.json()
        self._logic_delete_tag(body)
        return web.json_response({"status": "ok", "data": self._get_enriched_state()})

    async def _handle_save_slot(self, request: web.Request):
        body = await request.json()
        slot_id = self._logic_save_slot(body)
        return web.json_response({"status": "ok", "slot_id": slot_id, "data": self._get_enriched_state()})

    async def _handle_activate_slot(self, request: web.Request):
        body = await request.json()
        if self._logic_activate_slot(body):
            return web.json_response({"status": "ok", "data": self._get_enriched_state()})
        return web.json_response({"status": "error", "message": "目标槽位不存在"}, status=400)

    async def _handle_copy_slot(self, request: web.Request):
        body = await request.json()
        if self._logic_copy_slot(body):
            return web.json_response({"status": "ok", "data": self._get_enriched_state()})
        return web.json_response({"status": "error", "message": "源槽位或目标槽位不存在"}, status=400)

    # ==================== 上下文拦截注入逻辑 ====================

    def _build_active_slot_bundle(self) -> dict:
        """
        提取当前唯一激活存档槽内的加载项：
        - 槽位名称 (slot.name) 与 TAG 面板备注摘要 (tag.title) 仅作为面板管理备注，不注入模型；
        - 仅注入：槽位全局说明 (slot_note)、TAG 标识名 (tag)、补充描述 (description)、核心提示词 (prompt) 及参考图片 (image)。
        """
        active_id = self.db.get("active_slot_id", "")
        if not active_id or active_id == "none":
            return {"text_block": "", "image_paths": [], "slot_name": "未启用"}

        slot = self.db.get("slots", {}).get(active_id)
        if not slot:
            return {"text_block": "", "image_paths": [], "slot_name": "未找到槽位"}

        tags_dict = self.db.get("tags", {})
        mounted_ids = slot.get("tag_ids", [])
        lines: List[str] = []
        image_paths: List[str] = []

        for idx, tid in enumerate(mounted_ids, start=1):
            t_obj = tags_dict.get(tid)
            if not t_obj:
                continue
            tag_label = t_obj.get("tag", "未命名TAG").strip()
            desc_str = t_obj.get("description", "").strip()
            prompt_str = t_obj.get("prompt", "").strip()

            # 仅当存在文本描述或提示词时才组装文本段落
            if desc_str or prompt_str:
                entry_parts = [f"{idx}. 【{tag_label}】"]
                if desc_str:
                    entry_parts.append(f"   - 补充描述：{desc_str}")
                if prompt_str:
                    entry_parts.append(f"   - 核心设定与要求：{prompt_str}")
                lines.append("\n".join(entry_parts))

            img_name = t_obj.get("image", "").strip()
            if img_name:
                full_img_path = os.path.join(self.images_dir, os.path.basename(img_name))
                if os.path.exists(full_img_path):
                    image_paths.append(full_img_path)

        slot_note = slot.get("slot_note", "").strip()
        slot_title = slot.get("name", active_id)
        if not lines and not slot_note:
            return {"text_block": "", "image_paths": image_paths, "slot_name": slot_title}

        header = "\n\n[🔥 当前激活存档点 · 节点重要补充信息]:\n"
        note_line = f"存档全局说明：{slot_note}\n" if slot_note else ""
        body = "\n".join(lines)
        footer = "\n（请在不破坏基础人格的前提下，将上述存档点信息作为当前对话节点的有效背景与视觉设定参考。）"

        return {
            "text_block": f"{header}{note_line}{body}{footer}",
            "image_paths": image_paths,
            "slot_name": slot_title,
        }

    @filter.on_llm_request()
    async def on_llm_req(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self.enable_plugin:
            return

        bundle = self._build_active_slot_bundle()
        text_block = bundle["text_block"]
        image_paths = bundle["image_paths"]

        if text_block:
            orig_sys = getattr(req, "system_prompt", "") or ""
            if self.inject_position == "head":
                req.system_prompt = f"{text_block.strip()}\n\n{orig_sys}".strip()
            else:
                req.system_prompt = f"{orig_sys}\n{text_block}".strip()

        if self.inject_images and image_paths:
            if not hasattr(req, "image_urls") or req.image_urls is None:
                req.image_urls = []
            for img_p in image_paths:
                abs_p = os.path.abspath(img_p)
                if abs_p not in req.image_urls:
                    req.image_urls.append(abs_p)

    @filter.command("sp_status")
    async def cmd_sp_status(self, event: AstrMessageEvent):
        bundle = self._build_active_slot_bundle()
        active_id = self.db.get("active_slot_id", "none")
        slot = self.db.get("slots", {}).get(active_id, {})
        tag_ids = slot.get("tag_ids", []) if slot else []
        tag_names = [
            f"[{self.db.get('tags', {}).get(tid, {}).get('tag', tid)}]"
            for tid in tag_ids
            if tid in self.db.get("tags", {})
        ]
        tags_str = " ".join(tag_names) if tag_names else "暂无"
        msg = (
            f"🔥 【存档点 SavePoint v{PLUGIN_VERSION}】\n"
            f"• 插件状态：{'已开启' if self.enable_plugin else '已关闭'}\n"
            f"• 当前激活槽位：{bundle['slot_name']} ({active_id})\n"
            f"• 槽内已挂载 TAG ({len(tag_names)} 个)：{tags_str}\n"
            f"• 附带参考图片：{len(bundle['image_paths'])} 张"
        )
        yield event.plain_result(msg)
