from __future__ import annotations

import json
import asyncio
import logging
from time import time
from contextlib import suppress
from typing import Any, Literal, TYPE_CHECKING
from collections import deque

import aiohttp

from translate import _
from exceptions import MinerException, WebsocketClosed
from constants import PING_INTERVAL, PING_TIMEOUT, MAX_WEBSOCKETS, WS_TOPICS_LIMIT
from utils import (
    CHARS_ASCII,
    task_wrapper,
    create_nonce,
    json_minify,
    format_traceback,
    AwaitableValue,
    ExponentialBackoff,
)

if TYPE_CHECKING:
    from collections import abc

    from twitch import Twitch
    from gui import WebsocketStatus
    from constants import JsonType, WebsocketTopic


WSMsgType = aiohttp.WSMsgType
logger = logging.getLogger("TwitchDrops")
ws_logger = logging.getLogger("TwitchDrops.websocket")


class Websocket:
    def __init__(self, pool: WebsocketPool, index: int):
        self._pool: WebsocketPool = pool
        self._twitch: Twitch = pool._twitch
        self._ws_gui: WebsocketStatus = self._twitch.gui.websockets
        self._state_lock = asyncio.Lock()
        # websocket index
        self._idx: int = index
        # current websocket connection
        self._ws: AwaitableValue[aiohttp.ClientWebSocketResponse] = AwaitableValue()
        # set when the websocket needs to be closed or reconnect
        self._closed = asyncio.Event()
        self._reconnect_requested = asyncio.Event()
        # set when the topics changed
        self._topics_changed = asyncio.Event()
        # ping timestamps
        self._next_ping: float = time()
        self._max_pong: float = self._next_ping + PING_TIMEOUT.total_seconds()
        # main task, responsible for receiving messages, sending them, and websocket ping
        self._handle_task: asyncio.Task[None] | None = None
        # topics stuff
        self.topics: dict[str, WebsocketTopic] = {}
        self._submitted: set[WebsocketTopic] = set()
        # notify GUI
        self.set_status(_("gui", "websocket", "disconnected"))

    @property
    def connected(self) -> bool:
        return self._ws.has_value()

    def wait_until_connected(self):
        return self._ws.wait()

    def set_status(self, status: str | None = None, refresh_topics: bool = False):
        self._twitch.gui.websockets.update(
            self._idx, status=status, topics=(len(self.topics) if refresh_topics else None)
        )

    def request_reconnect(self):
        # reset our ping interval, so we send a PING after reconnect right away
        self._next_ping = time()
        self._reconnect_requested.set()

    async def start(self):
        async with self._state_lock:
            self.start_nowait()
            await self.wait_until_connected()

    def start_nowait(self):
        if self._handle_task is None or self._handle_task.done():
            self._handle_task = asyncio.create_task(self._handle())

    async def stop(self, *, remove: bool = False):
        async with self._state_lock:
            if self._closed.is_set():
                return
            self._closed.set()
            ws = self._ws.get_with_default(None)
            if ws is not None:
                self.set_status(_("gui", "websocket", "disconnecting"))
                await ws.close()
            if self._handle_task is not None:
                with suppress(asyncio.TimeoutError, asyncio.CancelledError):
                    await asyncio.wait_for(self._handle_task, timeout=2)
                self._handle_task = None
            if remove:
                self.topics.clear()
                self._topics_changed.set()
                self._twitch.gui.websockets.remove(self._idx)

    def stop_nowait(self, *, remove: bool = False):
        # weird syntax but that's what we get for using a decorator for this
        # return type of 'task_wrapper' is a coro, so we need to instance it for the task
        asyncio.create_task(task_wrapper(self.stop)(remove=remove))

    async def _backoff_connect(
        self, ws_url: str, **kwargs
    ) -> abc.AsyncGenerator[aiohttp.ClientWebSocketResponse, None]:
        session = await self._twitch.get_session()
        backoff = ExponentialBackoff(**kwargs)
        if self._twitch.settings.proxy:
            proxy = self._twitch.settings.proxy
        else:
            proxy = None
        for delay in backoff:
            try:
                async with session.ws_connect(ws_url, proxy=proxy) as websocket:
                    yield websocket
                    backoff.reset()
            except (
                asyncio.TimeoutError,
                aiohttp.ClientResponseError,
                aiohttp.ClientConnectionError,
            ):
                ws_logger.info(
                    f"Websocket[{self._idx}] connection problem (sleep: {round(delay)}s)"
                )
                await asyncio.sleep(delay)
            except RuntimeError:
                ws_logger.warning(
                    f"Websocket[{self._idx}] exiting backoff connect loop "
                    "because session is closed (RuntimeError)"
                )
                break

    @task_wrapper(critical=True)
    async def _handle(self):
        # ensure we're logged in before connecting
        self.set_status(_("gui", "websocket", "initializing"))
        await self._twitch.wait_until_login()
        self.set_status(_("gui", "websocket", "connecting"))
        ws_logger.info(f"Websocket[{self._idx}] connecting...")
        self._closed.clear()
        self._topics_changed.clear()  # Reset topics changed flag at start
        # Connect/Reconnect loop
        async for websocket in self._backoff_connect(
            "wss://pubsub-edge.twitch.tv/v1", maximum=3*60  # 3 minutes maximum backoff time
        ):
            self._ws.set(websocket)
            self._reconnect_requested.clear()
            # NOTE: _topics_changed doesn't start set,
            # because there's no initial topics we can sub to right away
            self.set_status(_("gui", "websocket", "connected"))
            ws_logger.info(f"Websocket[{self._idx}] connected.")
            try:
                try:
                    while not self._reconnect_requested.is_set():
                        await self._handle_ping()
                        await self._handle_topics()
                        await self._handle_recv()
                finally:
                    self._ws.clear()
                    self._submitted.clear()
                    # set _topics_changed to let the next WS connection resub to the topics
                    self._topics_changed.set()
                # A reconnect was requested
            except WebsocketClosed as exc:
                if exc.received:
                    # server closed the connection, not us - reconnect
                    ws_logger.warning(
                        f"Websocket[{self._idx}] closed unexpectedly: {websocket.close_code}"
                    )
                elif self._closed.is_set():
                    # we closed it - exit
                    ws_logger.info(f"Websocket[{self._idx}] stopped.")
                    self.set_status(_("gui", "websocket", "disconnected"))
                    return
            except Exception:
                ws_logger.exception(f"Exception in Websocket[{self._idx}]")
            self.set_status(_("gui", "websocket", "reconnecting"))
            ws_logger.warning(f"Websocket[{self._idx}] reconnecting...")

    async def _handle_ping(self):
        now = time()
        if now >= self._next_ping:
            self._next_ping = now + PING_INTERVAL.total_seconds()
            self._max_pong = now + PING_TIMEOUT.total_seconds()  # wait for a PONG for up to 10s
            await self.send({"type": "PING"})
        elif now >= self._max_pong:
            # it's been more than 10s and there was no PONG
            ws_logger.warning(f"Websocket[{self._idx}] didn't receive a PONG, reconnecting...")
            self.request_reconnect()

    async def _handle_topics(self):
        if not self._topics_changed.is_set():
            # nothing to do
            return
        self._topics_changed.clear()
        self.set_status(refresh_topics=True)
        auth_state = await self._twitch.get_auth()
        current: set[WebsocketTopic] = set(self.topics.values())
        # handle removed topics
        removed = self._submitted.difference(current)
        if removed:
            topics_list = list(map(str, removed))
            ws_logger.debug(f"Websocket[{self._idx}]: Removing topics: {', '.join(topics_list)}")
            await self.send(
                {
                    "type": "UNLISTEN",
                    "data": {
                        "topics": topics_list,
                        "auth_token": auth_state.access_token,
                    }
                }
            )
            self._submitted.difference_update(removed)
        # handle added topics
        added = current.difference(self._submitted)
        if added:
            topics_list = list(map(str, added))
            ws_logger.debug(f"Websocket[{self._idx}]: Adding topics: {', '.join(topics_list)}")
            await self.send(
                {
                    "type": "LISTEN",
                    "data": {
                        "topics": topics_list,
                        "auth_token": auth_state.access_token,
                    }
                }
            )
            self._submitted.update(added)

    async def _gather_recv(self, messages: list[JsonType], timeout: float = 0.5):
        """
        Gather incoming messages over the timeout specified.
        Note that there's no return value - this modifies `messages` in-place.
        """
        ws = self._ws.get_with_default(None)
        assert ws is not None
        while True:
            raw_message: aiohttp.WSMessage = await ws.receive(timeout=timeout)
            ws_logger.debug(f"Websocket[{self._idx}] received: {raw_message}")
            if raw_message.type is WSMsgType.TEXT:
                message: JsonType = json.loads(raw_message.data)
                messages.append(message)
            elif raw_message.type is WSMsgType.CLOSE:
                raise WebsocketClosed(received=True)
            elif raw_message.type is WSMsgType.CLOSED:
                raise WebsocketClosed(received=False)
            elif raw_message.type is WSMsgType.CLOSING:
                pass  # skip these
            elif raw_message.type is WSMsgType.ERROR:
                ws_logger.error(
                    f"Websocket[{self._idx}] error: {format_traceback(raw_message.data)}"
                )
                raise WebsocketClosed()
            else:
                ws_logger.error(f"Websocket[{self._idx}] error: Unknown message: {raw_message}")

    def _handle_message(self, message):
        """Process a message, optimized for reduced overhead"""
        # Get topic directly from the message
        data = message.get("data", {})
        topic_key = data.get("topic")
        
        if not topic_key:
            return
            
        # Look up the topic handler
        topic = self.topics.get(topic_key)
        if topic is not None:
            # Create task to handle message processing without blocking
            try:
                # Parse JSON message once
                msg_data = json.loads(data.get("message", "{}"))
                asyncio.create_task(topic(msg_data))
            except json.JSONDecodeError:
                ws_logger.warning(f"Websocket[{self._idx}] received invalid JSON in message")

    async def _handle_recv(self):
        """
        Handle receiving messages from the websocket with improved efficiency.
        """
        # Use a short timeout to avoid blocking the event loop
        messages: list[JsonType] = []
        
        # Gather messages with timeout
        with suppress(asyncio.TimeoutError):
            await self._gather_recv(messages, timeout=0.5)
            
        # Process all messages by type with batch processing
        # Group similar message types to reduce duplicate logic
        message_count = len(messages)
        
        # Early return if no messages
        if not message_count:
            return
            
        # Track message types for debugging performance
        if message_count > 20:
            ws_logger.debug(f"Websocket[{self._idx}] processing {message_count} messages")
        
        # Process messages by type for better efficiency
        for message in messages:
            msg_type = message.get("type", "")
            
            if msg_type == "MESSAGE":
                # Handle topic messages (most common)
                self._handle_message(message)
                
            elif msg_type == "PONG":
                # Update ping timestamp
                self._max_pong = self._next_ping
                
            elif msg_type == "RESPONSE":
                # No special handling needed
                pass
                
            elif msg_type == "RECONNECT":
                # Handle reconnect request
                ws_logger.warning(f"Websocket[{self._idx}] requested reconnect")
                self.request_reconnect()
                break  # Exit loop since we're reconnecting
                
            else:
                # Unknown message type
                ws_logger.warning(f"Websocket[{self._idx}] unknown message type: {msg_type}")

    def add_topics(self, topics_set: set[WebsocketTopic]):
        changed: bool = False
        while topics_set and len(self.topics) < WS_TOPICS_LIMIT:
            topic = topics_set.pop()
            self.topics[str(topic)] = topic
            changed = True
        if changed:
            self._topics_changed.set()

    def remove_topics(self, topics_set: set[str]):
        existing = topics_set.intersection(self.topics.keys())
        if not existing:
            # nothing to remove from here
            return
        topics_set.difference_update(existing)
        for topic in existing:
            del self.topics[topic]
        self._topics_changed.set()

    async def send(self, message: JsonType):
        ws = self._ws.get_with_default(None)
        assert ws is not None
        if message["type"] != "PING":
            message["nonce"] = create_nonce(CHARS_ASCII, 30)
        await ws.send_json(message, dumps=json_minify)
        ws_logger.debug(f"Websocket[{self._idx}] sent: {message}")


class WebsocketPool:
    def __init__(self, twitch: Twitch):
        self._twitch: Twitch = twitch
        self._running = asyncio.Event()
        self.websockets: list[Websocket] = []
        self._topics_lock = asyncio.Lock()  # Lock for thread-safe topic operations
        self._connection_count = 0  # Track active connections for better resource management
        
    @property
    def running(self) -> bool:
        return self._running.is_set()
        
    @property
    def connection_count(self) -> int:
        return self._connection_count
        
    def _increment_connections(self):
        self._connection_count += 1
        
    def _decrement_connections(self):
        self._connection_count = max(0, self._connection_count - 1)

    def wait_until_connected(self) -> abc.Coroutine[Any, Any, Literal[True]]:
        return self._running.wait()
        
    async def start(self):
        self._running.set()
        # Start websockets in parallel
        if self.websockets:
            await asyncio.gather(*(ws.start() for ws in self.websockets))

    async def stop(self, *, clear_topics: bool = False):
        self._running.clear()
        # Stop all websockets in parallel to speed up shutdown
        if self.websockets:
            await asyncio.gather(*(ws.stop(remove=clear_topics) for ws in self.websockets))
        # Reset connection count
        self._connection_count = 0

    async def add_topics(self, topics: abc.Iterable[WebsocketTopic]):
        # Use lock to ensure thread safety during topic operations
        async with self._topics_lock:
            # Convert to set to ensure no duplicates
            topics_set = set(topics)
            if not topics_set:
                # Nothing to add
                return
                
            # Quickly check if topics already exist
            existing_topics = set()
            for ws in self.websockets:
                existing_topics.update(ws.topics.values())
            topics_set.difference_update(existing_topics)
            
            if not topics_set:
                # None left to add
                return
                
            # Optimize websocket usage - first try to add to existing connections
            for ws in self.websockets:
                if ws.connected and len(ws.topics) < WS_TOPICS_LIMIT:
                    # Add as many topics as possible to this websocket
                    ws.add_topics(topics_set)
                    # If all topics have been assigned, we're done
                    if not topics_set:
                        return
            
            # If we still have topics to add, create new websockets as needed
            for ws_idx in range(len(self.websockets), MAX_WEBSOCKETS):
                # Create new websocket
                ws = Websocket(self, ws_idx)
                if self.running:
                    ws.start_nowait()
                self.websockets.append(ws)
                self._increment_connections()
                
                # Add topics to this new websocket
                ws.add_topics(topics_set)
                
                # If all topics assigned, we're done
                if not topics_set:
                    return
                    
            # If we reach here, there were leftover topics
            raise MinerException("Maximum topics limit has been reached")

    async def remove_topics(self, topics: abc.Iterable[str]):
        async with self._topics_lock:
            # Convert to set for efficient operations
            topics_set = set(topics)
            if not topics_set:
                # Nothing to remove
                return
                
            # Remove topics from all websockets
            for ws in self.websockets:
                ws.remove_topics(topics_set)
                
            # Optimize websocket pool - consolidate topics to fewer connections if possible
            self._optimize_pool()
    
    def _optimize_pool(self):
        """Consolidate topics to minimize the number of active connections"""
        # If we have fewer than 2 websockets, no optimization needed
        if len(self.websockets) < 2:
            return
            
        # Calculate total topics and required websockets
        total_topics = sum(len(ws.topics) for ws in self.websockets)
        required_websockets = (total_topics + WS_TOPICS_LIMIT - 1) // WS_TOPICS_LIMIT
        
        # If we have more websockets than needed, consolidate
        if len(self.websockets) > required_websockets:
            recycled_topics: list[WebsocketTopic] = []
            
            # Sort websockets by topic count (ascending)
            sorted_websockets = sorted(self.websockets, key=lambda ws: len(ws.topics))
            
            # Remove websockets that are no longer needed
            while len(sorted_websockets) > required_websockets:
                ws = sorted_websockets.pop(0)  # Remove the one with fewest topics
                self.websockets.remove(ws)
                recycled_topics.extend(ws.topics.values())
                ws.stop_nowait(remove=True)
                self._decrement_connections()
                
            # Re-add recycled topics to remaining websockets
            if recycled_topics:
                asyncio.create_task(self.add_topics(recycled_topics))
