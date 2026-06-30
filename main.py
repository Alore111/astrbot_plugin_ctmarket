"""
CT 群聊交易市场（归档子模块）

职责：
- 监听群聊消息，根据用户配置的规则决定是否入库
- 将命中的消息归档到 SQLite，供后续检索/展示
- 暴露插件 Web API，供插件 Pages（WebUI）读取归档数据
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register
from quart import jsonify, request

try:
    from .rules import CompiledRule, compile_rules, match_first_rule
    from .storage import MessageStore, serialize_message_chain, serialize_raw_message
except ImportError:
    from rules import CompiledRule, compile_rules, match_first_rule
    from storage import MessageStore, serialize_message_chain, serialize_raw_message


PLUGIN_NAME = "astrbot_plugin_ctmarket"


@register("CT群聊交易市场", "Alore", "监听群聊内容并归档，提供 WebUI 查看记录。", "0.1.0")
class CTMarketPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._data_dir = self._get_data_dir()
        self._store = self._create_store()
        self._compiled_rules: list[CompiledRule] = []
        self._rules_fingerprint = ""
        self._refresh_rules_cache()
        self._register_web_api()

    async def initialize(self):
        return

    async def terminate(self):
        return

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """
        群聊消息监听入口。

        注意：这是高频入口，避免在这里做阻塞 IO 和过多日志输出。
        """

        if not bool(self.config.get("enable", False)):
            return

        message_obj = event.message_obj
        group_id = str(getattr(message_obj, "group_id", "") or "")
        if not group_id:
            return

        text = event.message_str or ""
        rule_name = match_first_rule(
            rules=self._get_compiled_rules(),
            group_id=group_id,
            text=text,
        )
        if not rule_name:
            return

        created_at = int(getattr(message_obj, "timestamp", 0) or 0)
        message_id = str(getattr(message_obj, "message_id", "") or "")
        self_id = str(getattr(message_obj, "self_id", "") or "")
        sender = getattr(message_obj, "sender", None)
        sender_id = str(getattr(sender, "user_id", "") or getattr(sender, "id", "") or "")
        sender_name = str(event.get_sender_name() or "")

        store_chain = bool(self._get_storage_config().get("store_message_chain", False))
        message_chain_json = serialize_message_chain(event.get_messages()) if store_chain else None
        raw_message_json = serialize_raw_message(getattr(message_obj, "raw_message", None))

        try:
            await asyncio.to_thread(
                self._store.insert_message,
                created_at=created_at,
                platform="",
                self_id=self_id,
                group_id=group_id,
                message_id=message_id,
                sender_id=sender_id,
                sender_name=sender_name,
                message_str=text,
                message_chain_json=message_chain_json,
                raw_message_json=raw_message_json,
                rule_name=rule_name,
            )
        except Exception:
            logger.exception(f"[{PLUGIN_NAME}] insert_failed group_id={group_id} message_id={message_id}")

    def _get_data_dir(self) -> Path:
        try:
            p = StarTools.get_data_dir()
            if isinstance(p, Path):
                return p
        except Exception:
            pass

        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            return get_astrbot_data_path() / "plugin_data" / PLUGIN_NAME
        except Exception:
            return Path("data") / "plugin_data" / PLUGIN_NAME

    def _get_storage_config(self) -> dict:
        raw = self.config.get("storage", {})
        return raw if isinstance(raw, dict) else {}

    def _get_webui_config(self) -> dict:
        raw = self.config.get("webui", {})
        return raw if isinstance(raw, dict) else {}

    def _get_rules_config(self) -> object:
        raw_rules = self.config.get("rules")
        if isinstance(raw_rules, list) and raw_rules:
            return raw_rules

        rules_json = self.config.get("rules_json")
        if not isinstance(rules_json, str) or not rules_json.strip():
            return raw_rules

        try:
            parsed = json.loads(rules_json)
        except json.JSONDecodeError as e:
            logger.warning(
                f'[{PLUGIN_NAME}] rules_json_parse_failed err="{e.msg}" line={e.lineno} col={e.colno}'
            )
            return []

        if not isinstance(parsed, list):
            logger.warning(f"[{PLUGIN_NAME}] rules_json_invalid_root type={type(parsed).__name__}")
            return []
        return parsed

    def _get_rules_fingerprint(self) -> str:
        """
        计算 rules 配置的指纹，用于缓存“编译后的规则”。

        规则编译包含正则编译，频繁进行会造成不必要开销；同时也避免重复输出无效正则告警。
        """

        raw = {
            "rules": self.config.get("rules"),
            "rules_json": self.config.get("rules_json"),
        }
        try:
            payload = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
        except TypeError:
            payload = str(raw)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def _refresh_rules_cache(self) -> None:
        raw_rules = self._get_rules_config()
        new_fingerprint = self._get_rules_fingerprint()
        if new_fingerprint == self._rules_fingerprint:
            return

        def warn(msg: str) -> None:
            logger.warning(f"[{PLUGIN_NAME}] {msg}")

        compiled = compile_rules(raw_rules, warn=warn)
        self._compiled_rules = compiled
        self._rules_fingerprint = new_fingerprint
        logger.info(f"[{PLUGIN_NAME}] rules_loaded count={len(compiled)}")

    def _get_compiled_rules(self) -> list[CompiledRule]:
        self._refresh_rules_cache()
        return self._compiled_rules

    def _create_store(self) -> MessageStore:
        storage_cfg = self._get_storage_config()
        db_filename = str(storage_cfg.get("db_filename") or "records.sqlite3")
        max_records = int(storage_cfg.get("max_records") or 0)
        db_path = self._data_dir / db_filename
        store_chain = bool(storage_cfg.get("store_message_chain", False))
        logger.info(
            f"[{PLUGIN_NAME}] storage_ready data_dir={self._data_dir} db={db_path} max_records={max_records} store_chain={store_chain}"
        )
        return MessageStore(db_path, max_records=max_records)

    def _register_web_api(self) -> None:
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/records",
            self.api_records,
            ["GET"],
            "List archived group messages",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/groups",
            self.api_groups,
            ["GET"],
            "List archived groups",
        )

    async def api_records(self):
        """分页读取归档消息记录。"""

        webui_cfg = self._get_webui_config()
        default_page_size = int(webui_cfg.get("default_page_size") or 50)
        max_page_size = int(webui_cfg.get("max_page_size") or 200)

        try:
            limit = int(request.args.get("limit", default_page_size))
        except ValueError:
            limit = default_page_size
        try:
            offset = int(request.args.get("offset", 0))
        except ValueError:
            offset = 0
        limit = max(1, min(limit, max_page_size))
        offset = max(0, offset)
        group_id = (request.args.get("group_id") or "").strip() or None
        q = (request.args.get("q") or "").strip() or None

        total = await asyncio.to_thread(self._store.count_messages, group_id=group_id, q=q)
        items = await asyncio.to_thread(
            self._store.list_messages,
            limit=limit,
            offset=offset,
            group_id=group_id,
            q=q,
        )
        return jsonify(
            {
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": [
                    {
                        "id": i.id,
                        "created_at": i.created_at,
                        "platform": i.platform,
                        "self_id": i.self_id,
                        "group_id": i.group_id,
                        "message_id": i.message_id,
                        "sender_id": i.sender_id,
                        "sender_name": i.sender_name,
                        "message_str": i.message_str,
                        "rule_name": i.rule_name,
                    }
                    for i in items
                ],
            }
        )

    async def api_groups(self):
        """列出已归档过的 group_id（用于 WebUI 下拉筛选）。"""

        items = await asyncio.to_thread(self._store.list_groups)
        return jsonify({"items": items})
