"""Small lark-channel adapter: disk commit before WS ACK, no RAM dispatch queue."""
from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace

from lark_channel import FeishuChannel
from lark_channel.core.json import JSON
from lark_channel.channel.normalize.mentions import extract_mentions, parse_at_tags, text_has_mention_all
from lark_channel.channel.normalize.registry import parse_message_content
from lark_channel.channel.safety.policy_gate import PolicyGate
from lark_channel.channel.types import PostContent


class DurableFeishuChannel(FeishuChannel):
    def __init__(self, *, inbox, allow_dm, **kwargs):
        self.inbox, self.allow_dm = inbox, allow_dm
        self.inbox_loop = self.inbox_wake = None
        super().__init__(**kwargs)
        # An SDK upgrade must fail visibly instead of silently restoring the
        # asynchronous ACK-before-save behavior.
        if not callable(getattr(self._pipeline, 'normalize', None)):
            raise RuntimeError("lark-channel lacks the required normalize API")
        if 'data' not in inspect.signature(FeishuChannel._on_p2_im_message_receive_v1).parameters:
            raise RuntimeError("lark-channel receive callback contract changed")

    def bind_inbox(self, wake):
        self.inbox_loop, self.inbox_wake = asyncio.get_running_loop(), wake

    def _on_p2_im_message_receive_v1(self, data):
        raw = data if isinstance(data, dict) else json.loads(JSON.marshal(data))
        event = raw.get('event') or {}
        message, sender = event.get('message'), event.get('sender') or {}
        if not isinstance(message, dict):
            raise ValueError("Feishu receive event is missing message")
        if not self.admit(message, sender):
            return
        # Deliberately synchronous: an exception escapes to the WS dispatcher,
        # which returns failure instead of acknowledging an unsaved message.
        self.inbox.receive(dict(event_id=(raw.get('header') or {}).get('event_id'),
                                message=message, sender=sender))
        if self.inbox_loop and not self.inbox_loop.is_closed():
            self.inbox_loop.call_soon_threadsafe(self.inbox_wake.set)

    def admit(self, message, sender):
        identity = self.bot_identity
        if identity is None:
            raise RuntimeError("bot identity is not ready; inbound event was not acknowledged")
        sender_ids = sender.get('sender_id') or {}
        sender_id = sender_ids.get('open_id') or ''
        if not sender_id or sender_id == identity.open_id:
            return False
        chat_type = message.get('chat_type')
        if chat_type in ('public', 'group', 'topic'):
            chat_type = 'group'
        elif chat_type in ('private', 'p2p'):
            chat_type = 'p2p'
        else:
            raise ValueError(f"unsupported inbound chat_type: {chat_type}")
        extraction = extract_mentions(message.get('mentions') or [])
        mentions = list(extraction.mention_list)
        content = parse_message_content(message.get('message_type'), message.get('content'))
        body = getattr(content, 'text', '') or ''
        all_mentioned = extraction.mentioned_all or text_has_mention_all(body)
        if isinstance(content, PostContent):
            extra, at_all, _ = parse_at_tags(body)
            mentions.extend(extra)
            all_mentioned = all_mentioned or at_all
        if chat_type == 'group' and not any(m.open_id == identity.open_id for m in mentions):
            return False
        policy = PolicyGate(self._config.policy)
        policy.set_bot_open_id(identity.open_id)
        msg = SimpleNamespace(sender=SimpleNamespace(**{
            k: sender_ids.get(k) for k in ('open_id', 'user_id', 'union_id')}),
            conversation=SimpleNamespace(chat_type=chat_type, chat_id=message.get('chat_id')),
            mentions=mentions, mentioned_all=all_mentioned)
        if not policy.evaluate(msg).allowed:
            return False
        return chat_type == 'group' or self.allow_dm(sender_id)

    async def dispatch_saved(self, payload, received, handler):
        if self.bot_identity is None:
            if await self.resolve_bot_identity() is None:
                raise RuntimeError('bot identity not ready; saved input remains queued')
        msg = await self._pipeline.normalize(message_event=payload['message'],
                                             sender=payload['sender'], event_id=payload.get('event_id'))
        if msg is None:
            raise RuntimeError("saved message could not be normalized; pending record retained")
        # Arrival time remains the original receipt time across queue/restart.
        msg.link16_received = received
        await handler(msg)
