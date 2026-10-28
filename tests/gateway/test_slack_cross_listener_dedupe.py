"""ENG-1954: one Slack mention must start exactly one turn.

A single mention arrives twice — once through the ``message`` listener and once
through ``app_mention`` — within ~60 ms. In a Slack Connect customer channel the
two envelopes can resolve to *different* workspace ids (the ``message`` event
carries the poster's ``team``; the ``app_mention`` copy can fall back to the
receiving install's ``team_id``), so a team-scoped dedupe key misses and two
parallel turns answer the same message. The logical identity of a message is
(channel, ts), independent of event type and workspace.
"""

import asyncio
import importlib
import sys
from importlib.machinery import PathFinder
from types import ModuleType
from unittest.mock import AsyncMock

from gateway.config import PlatformConfig


def _load_installed_package(name):
    if PathFinder.find_spec(name) is None:
        return None
    prefix = f"{name}."
    displaced = {
        m: sys.modules.pop(m)
        for m in tuple(sys.modules)
        if (m == name or m.startswith(prefix)) and not isinstance(sys.modules[m], ModuleType)
    }
    try:
        return importlib.import_module(name)
    except ImportError:
        sys.modules.update(displaced)
        return None


_load_installed_package("slack_bolt")
_load_installed_package("slack_sdk")

SlackAdapter = importlib.import_module("plugins.platforms.slack.adapter").SlackAdapter

BOT = "U0BCLP7DB7B"
CHANNEL = "C0CUSTOMER1"
HOST_TEAM = "T0HOSTTEAM"
GUEST_TEAM = "T0GUESTTEAM"
TS = "1789412705.144549"


def _adapter(delivered):
    adapter = SlackAdapter(PlatformConfig(enabled=True, token="xoxb-fake"))
    adapter._bot_user_id = BOT
    adapter._resolve_user_name = AsyncMock(return_value="harsha")

    async def _capture(event):
        delivered.append(event)

    adapter.handle_message = _capture
    return adapter


def _event(kind, ts=TS, team: "str | None" = GUEST_TEAM):
    ev = {
        "type": kind,
        "user": "U0GUESTUSER",
        "text": f"<@{BOT}> why did spend drop?",
        "channel": CHANNEL,
        "channel_type": "channel",
        "ts": ts,
        "client_msg_id": f"cmid-{ts}",
    }
    if team:
        ev["team"] = team
    return ev


def _body(event_id):
    return {"team_id": HOST_TEAM, "event_id": event_id, "is_ext_shared_channel": False}


def _run(adapter, *pairs):
    async def scenario():
        await asyncio.gather(*(adapter._handle_slack_message(e, b) for e, b in pairs))
    asyncio.run(scenario())


def test_message_and_app_mention_for_one_ts_start_one_turn():
    delivered = []
    adapter = _adapter(delivered)
    _run(adapter,
         (_event("message", team=GUEST_TEAM), _body("Ev1")),
         (_event("app_mention", team=None), _body("Ev2")))
    assert len(delivered) == 1


def test_same_team_cross_listener_still_one_turn():
    delivered = []
    adapter = _adapter(delivered)
    _run(adapter,
         (_event("message", team=HOST_TEAM), _body("Ev1")),
         (_event("app_mention", team=HOST_TEAM), _body("Ev2")))
    assert len(delivered) == 1


def test_slack_retry_of_same_event_is_one_turn():
    delivered = []
    adapter = _adapter(delivered)
    _run(adapter,
         (_event("app_mention"), _body("Ev1")),
         (_event("app_mention"), _body("Ev1")))
    assert len(delivered) == 1


def test_distinct_messages_in_thread_each_start_a_turn():
    delivered = []
    adapter = _adapter(delivered)
    _run(adapter,
         (_event("message", ts=TS), _body("Ev1")),
         (_event("message", ts="1789412790.000100"), _body("Ev3")))
    assert len(delivered) == 2
