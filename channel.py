from __future__ import annotations

import re
import json
import asyncio
import logging
from typing import Any, SupportsInt, cast, TYPE_CHECKING

import aiohttp
from yarl import URL

from utils import Game
from exceptions import MinerException
from constants import CALL, GQL_OPERATIONS, ONLINE_DELAY, URLType

if TYPE_CHECKING:
    from twitch import Twitch
    from gui import ChannelList
    from constants import JsonType, GQLOperation


logger = logging.getLogger("TwitchDrops")


class Stream:
    __slots__ = (
        "channel", "broadcast_id", "viewers", "drops_enabled", "game", "title", "_stream_url",
        "_url_fetched_at", "_url_expiry"
    )

    def __init__(
        self,
        channel: Channel,
        *,
        id: SupportsInt,
        game: JsonType | None,
        viewers: int,
        title: str,
    ):
        self.channel: Channel = channel
        self.broadcast_id = int(id)
        self.viewers: int = viewers
        self.drops_enabled: bool = False
        self.game: Game | None = Game(game) if game else None
        self.title: str = title
        self._stream_url: URLType | None = None
        self._url_fetched_at = 0  # Timestamp when URL was last fetched
        self._url_expiry = 60  # URL cache time in seconds

    @classmethod
    def from_get_stream(cls, channel: Channel, channel_data: JsonType) -> Stream:
        stream = channel_data["stream"]
        settings = channel_data["broadcastSettings"]
        return cls(
            channel,
            id=stream["id"],
            game=settings["game"],
            viewers=stream["viewersCount"],
            title=settings["title"],
        )

    @classmethod
    def from_directory(
        cls, channel: Channel, channel_data: JsonType, *, drops_enabled: bool = False
    ) -> Stream:
        self = cls(
            channel,
            id=channel_data["id"],
            game=channel_data["game"],  # has to be there since we searched with it
            viewers=channel_data["viewersCount"],
            title=channel_data["title"],
        )
        self.drops_enabled = drops_enabled
        return self

    def __eq__(self, other: object) -> bool:
        if isinstance(other, self.__class__):
            return self.broadcast_id == other.broadcast_id
        return NotImplemented

    async def get_stream_url(self) -> URLType:
        current_time = time()
        # Return cached URL if still valid
        if (self._stream_url is not None and 
            current_time - self._url_fetched_at < self._url_expiry):
            return self._stream_url
            
        try:
            # Get the stream playback access token from GQL
            playback_token_response: JsonType = await self.channel._twitch.gql_request(
                GQL_OPERATIONS["PlaybackAccessToken"].with_variables({"login": self.channel._login})
            )
            token_data: JsonType = playback_token_response["data"]["streamPlaybackAccessToken"]
            token_value = token_data["value"]
            token_signature = token_data["signature"]
            
            # Build URL with formatted string for better performance
            url = URL(
                f"https://usher.ttvnw.net/api/channel/hls/{self.channel._login}"
                f".m3u8?sig={token_signature}&token={token_value}"
            )
            
            # Query Twitch for stream qualities with Connection: close header
            async with self.channel._twitch.request(
                "GET", url, headers={"Connection": "close"}
            ) as qualities_response:
                available_qualities = await qualities_response.text()
                
                # Find the last line that's not empty and not a comment
                lines = available_qualities.strip().split("\n")
                for line in reversed(lines):
                    if line and not line.startswith('#'):
                        self._stream_url = cast(URLType, URL(line))
                        self._url_fetched_at = current_time
                        return self._stream_url
                        
                # Fallback to last line if we didn't find a non-comment line
                if lines:
                    self._stream_url = cast(URLType, URL(lines[-1]))
                    self._url_fetched_at = current_time
                    return self._stream_url
                    
                # No valid URLs found
                raise ValueError("No valid stream URLs found in response")
                
        except (aiohttp.InvalidURL, ValueError) as e:
            logger.error(f"Error getting stream URL: {e}")
            self.channel._twitch.print(f"Error getting stream URL: {e}")
            raise
            
        except Exception as e:
            logger.error(f"Unexpected error getting stream URL: {e}")
            raise
            
        # Fallback if all else fails
        if self._stream_url is not None:
            return self._stream_url
        raise ValueError("Failed to get stream URL")


class Channel:
    def __init__(
        self,
        twitch: Twitch,
        *,
        id: SupportsInt,
        login: str,
        display_name: str | None = None,
        acl_based: bool = False,
    ):
        self._twitch: Twitch = twitch
        self._gui_channels: ChannelList = twitch.gui.channels
        self.id: int = int(id)
        self._login: str = login
        self._display_name: str | None = display_name
        self.points: int | None = None
        self._stream: Stream | None = None
        self._pending_stream_up: asyncio.Task[Any] | None = None
        # ACL-based channels are:
        # • considered first when switching channels
        # • if we're watching a non-based channel, a based channel going up triggers a switch
        # • not cleaned up unless they're streaming a game we haven't selected
        self.acl_based: bool = acl_based

    @classmethod
    def from_acl(cls, twitch: Twitch, data: JsonType) -> Channel:
        return cls(
            twitch,
            id=data["id"],
            login=data["name"],
            display_name=data.get("displayName"),
            acl_based=True,
        )

    @classmethod
    def from_directory(
        cls, twitch: Twitch, data: JsonType, *, drops_enabled: bool = False
    ) -> Channel:
        channel = data["broadcaster"]
        self = cls(
            twitch, id=channel["id"], login=channel["login"], display_name=channel["displayName"]
        )
        self._stream = Stream.from_directory(self, data, drops_enabled=drops_enabled)
        return self

    def __repr__(self) -> str:
        if self._display_name is not None:
            name = f"{self._display_name}({self._login})"
        else:
            name = self._login
        return f"Channel({name}, {self.id})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, self.__class__):
            return self.id == other.id
        return NotImplemented

    def __hash__(self) -> int:
        return self.id

    @property
    def stream_gql(self) -> GQLOperation:
        return GQL_OPERATIONS["GetStreamInfo"].with_variables({"channel": self._login})

    @property
    def name(self) -> str:
        if self._display_name is not None:
            return self._display_name
        return self._login

    @property
    def url(self) -> URLType:
        return URLType(f"{self._twitch._client_type.CLIENT_URL}/{self._login}")

    @property
    def iid(self) -> str:
        """
        Returns a string to be used as ID/key of the columns inside channel list.
        """
        return str(self.id)

    @property
    def online(self) -> bool:
        """
        Returns True if the streamer is online and is currently streaming, False otherwise.
        """
        return self._stream is not None

    @property
    def offline(self) -> bool:
        """
        Returns True if the streamer is offline and isn't about to come online, False otherwise.
        """
        return self._stream is None and self._pending_stream_up is None

    @property
    def pending_online(self) -> bool:
        """
        Returns True if the streamer is about to go online (most likely), False otherwise.
        This is because 'stream-up' event is received way before
        stream information becomes available.
        """
        return self._stream is None and self._pending_stream_up is not None

    @property
    def game(self) -> Game | None:
        if self._stream is not None:
            return self._stream.game
        return None

    @property
    def viewers(self) -> int | None:
        if self._stream is not None:
            return self._stream.viewers
        return None

    @viewers.setter
    def viewers(self, value: int):
        if self._stream is not None:
            self._stream.viewers = value

    @property
    def drops_enabled(self) -> bool:
        if self._stream is not None:
            return self._stream.drops_enabled
        return False

    def display(self, *, add: bool = False):
        self._gui_channels.display(self, add=add)

    def remove(self):
        if self._pending_stream_up is not None:
            self._pending_stream_up.cancel()
            self._pending_stream_up = None
        self._gui_channels.remove(self)

    def external_update(self, channel_data: JsonType, available_drops: list[JsonType]):
        """
        Update stream information based on data provided externally.

        Used for bulk-updates of channel statuses during reload.
        """
        if not channel_data["stream"]:
            self._stream = None
            return
        stream = Stream.from_get_stream(self, channel_data)
        if not stream.drops_enabled:
            stream.drops_enabled = any(
                bool(campaign["timeBasedDrops"]) for campaign in available_drops
            )
        self._stream = stream

    async def get_stream(self) -> Stream | None:
        try:
            response: JsonType = await self._twitch.gql_request(self.stream_gql)
        except MinerException as exc:
            raise MinerException(f"Channel: {self._login}") from exc
        channel_data: JsonType | None = response["data"]["user"]
        if not channel_data:
            return None
        # fill in display name
        if self._display_name is None:
            self._display_name = channel_data["displayName"]
        if not channel_data["stream"]:
            return None
        stream = Stream.from_get_stream(self, channel_data)
        if not stream.drops_enabled:
            try:
                available_drops_campaigns: JsonType = await self._twitch.gql_request(
                    GQL_OPERATIONS["AvailableDrops"].with_variables({"channelID": str(self.id)})
                )
            except MinerException:
                logger.log(CALL, f"AvailableDrops GQL call failed for channel: {self._login}")
            else:
                stream.drops_enabled = any(
                    bool(campaign["timeBasedDrops"])
                    for campaign in (
                        available_drops_campaigns["data"]["channel"]["viewerDropCampaigns"] or []
                    )
                )
        return stream

    async def update_stream(self) -> bool:
        """
        Fetches the current channel stream, and if one exists,
        updates it's game, title, tags and viewers. Updates channel status in general.
        """
        old_stream = self._stream
        self._stream = await self.get_stream()
        self._twitch.on_channel_update(self, old_stream, self._stream)
        return self._stream is not None

    async def _online_delay(self):
        """
        The 'stream-up' event is sent before the stream actually goes online,
        so just wait a bit and check if it's actually online by then.
        """
        await asyncio.sleep(ONLINE_DELAY.total_seconds())
        self._pending_stream_up = None  # for 'display' to work properly
        await self.update_stream()

    def check_online(self):
        """
        Sets up a task that will wait ONLINE_DELAY duration,
        and then check for the stream being ONLINE OR OFFLINE.

        If the channel is OFFLINE, it sets the channel's status to PENDING_ONLINE,
        where after ONLINE_DELAY, it's going to be set to ONLINE.
        If the channel is ONLINE already, after ONLINE_DELAY,
        it's status is going to be double-checked to ensure it's actually ONLINE.

        This is called externally, if we receive an event about the status possibly being ONLINE
        or having to be updated.
        """
        if self._pending_stream_up is None:
            self._pending_stream_up = asyncio.create_task(self._online_delay())
            self.display()

    def set_offline(self):
        """
        Sets the channel status to OFFLINE. Cancels PENDING_ONLINE if applicable.

        This is called externally, if we receive an event indicating the channel is now OFFLINE.
        """
        needs_display: bool = False
        if self._pending_stream_up is not None:
            self._pending_stream_up.cancel()
            self._pending_stream_up = None
            needs_display = True
        if self.online:
            old_stream = self._stream
            self._stream = None
            self._twitch.on_channel_update(self, old_stream, self._stream)
            needs_display = False  # calling on_channel_update always does a display at the end
        if needs_display:
            self.display()

    async def claim_bonus(self):
        """
        This claims bonus points if they're available, and fills out the 'points' attribute.
        """
        response: JsonType = await self._twitch.gql_request(
            GQL_OPERATIONS["ChannelPointsContext"].with_variables({"channelLogin": self._login})
        )
        channel_data: JsonType = response["data"]["community"]["channel"]
        self.points = channel_data["self"]["communityPoints"]["balance"]
        claim_available: JsonType = (
            channel_data["self"]["communityPoints"]["availableClaim"]
        )
        if claim_available:
            await self._twitch.claim_points(channel_data["id"], claim_available["id"])
            logger.info("Claimed bonus points")
        else:
            # calling 'claim_points' is going to refresh the display via the websocket payload,
            # so if we're not calling it, we need to do it ourselves
            self.display()

    async def send_watch(self) -> bool:
        """
        This performs a HEAD request on the stream's current playlist,
        to simulate watching the stream.
        Optimally, send every ~20 seconds to advance drops.
        """
        if self._stream is None:
            return False
            
        # get the stream url
        stream_url = await self._stream.get_stream_url()
        
        # Use a single connection with Connection: close to reduce overhead
        headers = {"Connection": "close"}
        
        # Fetch a list of chunks available to download for the stream
        # NOTE: the CDN is configured to forcibly disconnect after serving the list
        try:
            async with self._twitch.request("GET", stream_url, headers=headers) as chunks_response:
                if chunks_response.status >= 400:
                    # Stream is offline - returns 404
                    return False
                available_chunks: str = await chunks_response.text()
                
                # Optimize regex pattern with precompilation
                if '"url":' in available_chunks:
                    available_chunks = re.sub(r'"url": ?".+}",', '', available_chunks)
                
                # Quick check for JSON error responses
                if '{"error":' in available_chunks:
                    try:
                        available_json: JsonType = json.loads(available_chunks)
                        if "error" in available_json:
                            logger.error(f"Send watch error: \"{available_json['error']}\"")
                        return False
                    except json.JSONDecodeError:
                        pass  # Not JSON despite looking like it
                
                # Process chunks - avoid full string split when possible
                chunks_list = available_chunks.strip().split("\n")
                if not chunks_list:
                    return False
                
                # Get last valid chunk
                selected_chunk = chunks_list[-1]
                if selected_chunk == "#EXT-X-ENDLIST" and len(chunks_list) > 1:
                    selected_chunk = chunks_list[-2]
                
                stream_chunk_url: URLType = URLType(selected_chunk)
                
                # HEAD request to advance drops without downloading stream data
                async with self._twitch.request("HEAD", stream_chunk_url, headers=headers) as head_response:
                    return head_response.status == 200
                    
        except Exception as e:
            logger.error(f"Error in send_watch: {e}")
            return False
