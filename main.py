from __future__ import annotations

import asyncio
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register
from quart import jsonify, request

from rules import compile_rules, match_first_rule
from storage import MessageStore, serialize_message_chain, serialize_raw_message


PLUGIN_NAME = "astrbot_plugin_ctmarket"


@register("CT群聊交易市场", "Alore", "监听群聊内容并归档，提供 WebUI 查看记录。", "0.1.0")
class CTMarketPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._data_dir = self._get_data_dir()
        self._store = self._create_store()
        self._register_web_api()

    async def initialize(self):
        return

    async def terminate(self):
        return

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        if not bool(self.config.get("enable", False)):
            return

        message_obj = event.message_obj
        group_id = str(getattr(message_obj, "group_id", "") or "")
        if not group_id:
            return

        text = event.message_str or ""
        rule_name = match_first_rule(
            rules=compile_rules(self.config.get("rules")),
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

    def _create_store(self) -> MessageStore:
        storage_cfg = self._get_storage_config()
        db_filename = str(storage_cfg.get("db_filename") or "records.sqlite3")
        max_records = int(storage_cfg.get("max_records") or 0)
        db_path = self._data_dir / db_filename
        logger.info(f"[{PLUGIN_NAME}] data_dir={self._data_dir} db={db_path}")
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
        webui_cfg = self._get_webui_config()
        default_page_size = int(webui_cfg.get("default_page_size") or 50)
        max_page_size = int(webui_cfg.get("max_page_size") or 200)

        limit = int(request.args.get("limit", default_page_size))
        offset = int(request.args.get("offset", 0))
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
        items = await asyncio.to_thread(self._store.list_groups)
        return jsonify({"items": items})
