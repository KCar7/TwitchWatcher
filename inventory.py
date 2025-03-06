from __future__ import annotations

import re
import math
from enum import Enum
from itertools import chain
from typing import TYPE_CHECKING
from functools import cached_property
from datetime import datetime, timedelta, timezone

from channel import Channel
from exceptions import GQLException
from constants import GQL_OPERATIONS, URLType
from utils import timestamp, invalidate_cache, Game

if TYPE_CHECKING:
    from collections import abc

    from twitch import Twitch
    from constants import JsonType
    from gui import GUIManager, InventoryOverview


DIMS_PATTERN = re.compile(r'-\d+x\d+(?=\.(?:jpg|png|gif)$)', re.I)


def remove_dimensions(url: URLType) -> URLType:
    return URLType(DIMS_PATTERN.sub('', url))


class BenefitType(Enum):
    UNKNOWN = "UNKNOWN"
    BADGE = "BADGE"
    EMOTE = "EMOTE"
    DIRECT_ENTITLEMENT = "DIRECT_ENTITLEMENT"

    def is_badge_or_emote(self) -> bool:
        return self in (BenefitType.BADGE, BenefitType.EMOTE)


class Benefit:
    __slots__ = ("id", "name", "type", "image_url")

    def __init__(self, data: JsonType):
        benefit_data: JsonType = data["benefit"]
        self.id: str = benefit_data["id"]
        self.name: str = benefit_data["name"]
        self.type: BenefitType = (
            BenefitType(benefit_data["distributionType"])
            if benefit_data["distributionType"] in BenefitType.__members__.keys()
            else BenefitType.UNKNOWN
        )
        self.image_url: URLType = benefit_data["imageAssetURL"]


class BaseDrop:
    def __init__(
        self, campaign: DropsCampaign, data: JsonType, claimed_benefits: dict[str, datetime]
    ):
        self._twitch: Twitch = campaign._twitch
        self.id: str = data["id"]
        self.name: str = data["name"]
        self.campaign: DropsCampaign = campaign
        self.benefits: list[Benefit] = [Benefit(b) for b in data["benefitEdges"]]
        self.starts_at: datetime = timestamp(data["startAt"])
        self.ends_at: datetime = timestamp(data["endAt"])
        self.claim_id: str | None = None
        self.is_claimed: bool = False
        if "self" in data:
            self.claim_id = data["self"]["dropInstanceID"]
            self.is_claimed = data["self"]["isClaimed"]
        elif (
            # If there's no self edge available, we can use claimed_benefits to determine
            # (with pretty good certainty) if this drop has been claimed or not.
            # To do this, we check if the benefitEdges appear in claimed_benefits, and then
            # deref their "lastAwardedAt" timestamps into a list to check against.
            # If the benefits were claimed while the drop was active,
            # the drop has been claimed too.
            (
                dts := [
                    claimed_benefits[bid]
                    for benefit in self.benefits
                    if (bid := benefit.id) in claimed_benefits
                ]
            )
            and all(self.starts_at <= dt < self.ends_at for dt in dts)
        ):
            self.is_claimed = True
        self._precondition_drops: list[str] = [d["id"] for d in (data["preconditionDrops"] or [])]

    def __repr__(self) -> str:
        if self.is_claimed:
            additional = ", claimed=True"
        elif self.can_earn():
            additional = ", can_earn=True"
        else:
            additional = ''
        return f"Drop({self.rewards_text()}{additional})"

    @cached_property
    def preconditions_met(self) -> bool:
        campaign = self.campaign
        return all(campaign.timed_drops[pid].is_claimed for pid in self._precondition_drops)

    def _base_earn_conditions(self) -> bool:
        # define when a drop can be earned or not
        return (
            self.preconditions_met  # preconditions are met
            and not self.is_claimed  # isn't already claimed
        )

    def _base_can_earn(self) -> bool:
        # cross-participates in can_earn and can_earn_within handling, where a timeframe is added
        return (
            self._base_earn_conditions()
            # is within the timeframe
            and self.starts_at <= datetime.now(timezone.utc) < self.ends_at
        )

    def can_earn(self, channel: Channel | None = None) -> bool:
        return self._base_can_earn() and self.campaign._base_can_earn(channel)

    def can_earn_within(self, stamp: datetime) -> bool:
        return (
            self._base_earn_conditions()
            and self.ends_at > datetime.now(timezone.utc)
            and self.starts_at < stamp
        )

    @property
    def can_claim(self) -> bool:
        # https://help.twitch.tv/s/article/mission-based-drops?language=en_US#claiming
        # "If you are unable to claim the Drop in time, you will be able to claim it
        # from the Drops Inventory page until 24 hours after the Drops campaign has ended."
        return (
            self.claim_id is not None
            and not self.is_claimed
            and datetime.now(timezone.utc) < self.campaign.ends_at + timedelta(hours=24)
        )

    def _on_claim(self) -> None:
        invalidate_cache(self, "preconditions_met")

    def update_claim(self, claim_id: str):
        self.claim_id = claim_id

    async def generate_claim(self) -> None:
        # claim IDs now appear to be constructed from other IDs we have access to
        # Format: UserID#CampaignID#DropID
        # NOTE: This marks a drop as a ready-to-claim, so we may want to later ensure
        # its mining progress is finished first
        auth_state = await self.campaign._twitch.get_auth()
        self.claim_id = f"{auth_state.user_id}#{self.campaign.id}#{self.id}"

    def rewards_text(self, delim: str = ", ") -> str:
        return delim.join(benefit.name for benefit in self.benefits)

    async def claim(self) -> bool:
        result = await self._claim()
        if result:
            self.is_claimed = result
            # notify the campaign about claiming
            # this will cause it to call our _on_claim, so no need to call it ourselves here
            self.campaign._on_claim()
        return result

    async def _claim(self) -> bool:
        """
        Returns True if the claim succeeded, False otherwise.
        """
        if self.is_claimed:
            return True
        if not self.can_claim:
            return False
        try:
            response = await self._twitch.gql_request(
                GQL_OPERATIONS["ClaimDrop"].with_variables(
                    {"input": {"dropInstanceID": self.claim_id}}
                )
            )
        except GQLException:
            # regardless of the error, we have to assume
            # the claiming operation has potentially failed
            return False
        data = response["data"]
        if "errors" in data and data["errors"]:
            return False
        elif "claimDropRewards" in data:
            if not data["claimDropRewards"]:
                return False
            elif (
                data["claimDropRewards"]["status"]
                in ["ELIGIBLE_FOR_ALL", "DROP_INSTANCE_ALREADY_CLAIMED"]
            ):
                return True
        return False


class TimedDrop(BaseDrop):
    __slots__ = (
        "current_minutes", "required_minutes", "_manager", "_gui_inv",
        "_cached_remaining_minutes", "_cached_total_required_minutes",
        "_cached_total_remaining_minutes", "_cached_progress"
    )
    
    def __init__(
        self, campaign: DropsCampaign, data: JsonType, claimed_benefits: dict[str, datetime]
    ):
        super().__init__(campaign, data, claimed_benefits)
        self._manager: GUIManager = self._twitch.gui
        self._gui_inv: InventoryOverview = self._manager.inv
        
        # Initialize minutes data
        self.current_minutes: int = "self" in data and data["self"]["currentMinutesWatched"] or 0
        self.required_minutes: int = data["requiredMinutesWatched"]
        
        # Set current minutes for claimed drops
        if self.is_claimed:
            # Claimed drops may report inconsistent current minutes, so set to required
            self.current_minutes = self.required_minutes
            
        # Initialize cache properties
        self._cached_remaining_minutes = None
        self._cached_total_required_minutes = None
        self._cached_total_remaining_minutes = None
        self._cached_progress = None

    def __repr__(self) -> str:
        if self.is_claimed:
            additional = ", claimed=True"
        elif self.can_earn():
            additional = ", can_earn=True"
        else:
            additional = ''
            
        if 0 < self.current_minutes < self.required_minutes:
            minutes = f", {self.current_minutes}/{self.required_minutes}"
        else:
            minutes = ''
            
        return f"Drop({self.rewards_text()}{minutes}{additional})"

    @property
    def remaining_minutes(self) -> int:
        if self._cached_remaining_minutes is None:
            self._cached_remaining_minutes = self.required_minutes - self.current_minutes
        return self._cached_remaining_minutes

    @property
    def total_required_minutes(self) -> int:
        if self._cached_total_required_minutes is None:
            self._cached_total_required_minutes = self.required_minutes + max(
                (
                    self.campaign.timed_drops[pid].total_required_minutes
                    for pid in self._precondition_drops
                ),
                default=0,
            )
        return self._cached_total_required_minutes

    @property
    def total_remaining_minutes(self) -> int:
        if self._cached_total_remaining_minutes is None:
            self._cached_total_remaining_minutes = self.remaining_minutes + max(
                (
                    self.campaign.timed_drops[pid].total_remaining_minutes
                    for pid in self._precondition_drops
                ),
                default=0,
            )
        return self._cached_total_remaining_minutes

    @property
    def progress(self) -> float:
        if self._cached_progress is None:
            if self.current_minutes <= 0 or self.required_minutes <= 0:
                self._cached_progress = 0.0
            elif self.current_minutes >= self.required_minutes:
                self._cached_progress = 1.0
            else:
                self._cached_progress = self.current_minutes / self.required_minutes
        return self._cached_progress

    @property
    def availability(self) -> float:
        now = datetime.now(timezone.utc)
        if self.required_minutes > 0 and self.total_remaining_minutes > 0 and now < self.ends_at:
            return ((self.ends_at - now).total_seconds() / 60) / self.total_remaining_minutes
        return math.inf

    def _base_earn_conditions(self) -> bool:
        return super()._base_earn_conditions() and self.required_minutes > 0

    def _on_claim(self) -> None:
        result = super()._on_claim()
        self._gui_inv.update_drop(self)
        return result

    def _invalidate_cache(self, *properties) -> None:
        """Reset cached properties to force recalculation"""
        for prop in properties:
            cache_attr = f"_cached_{prop}"
            if hasattr(self, cache_attr):
                setattr(self, cache_attr, None)

    def _on_minutes_changed(self) -> None:
        # Reset cached properties
        self._cached_progress = None
        self._cached_remaining_minutes = None
        
        # Notify campaign
        self.campaign._on_minutes_changed()
        
        # Update GUI
        self._gui_inv.update_drop(self)

    def _on_total_minutes_changed(self) -> None:
        # Reset cached properties
        self._cached_total_required_minutes = None
        self._cached_total_remaining_minutes = None

    async def claim(self) -> bool:
        result = await super().claim()
        if result:
            self.current_minutes = self.required_minutes
        return result

    def update_minutes(self, minutes: int):
        if minutes < 0:
            return
        elif minutes <= self.required_minutes:
            self.current_minutes = minutes
        else:
            self.current_minutes = self.required_minutes
        self._on_minutes_changed()
        self.display()

    def display(self, *, countdown: bool = True, subone: bool = False):
        self._manager.display_drop(self, countdown=countdown, subone=subone)

    def bump_minutes(self):
        if self.current_minutes < self.required_minutes:
            self.current_minutes += 1
            self._on_minutes_changed()
        self.display()


class DropsCampaign:
    __slots__ = (
        "_twitch", "id", "name", "game", "linked", "link_url", "image_url", 
        "starts_at", "ends_at", "allowed_channels", "timed_drops", 
        "_cached_progress", "_cached_remaining_minutes", "_cached_required_minutes",
        "_cached_has_badge_or_emote", "_cached_finished", "_cached_claimed_drops",
        "_cached_remaining_drops"
    )
    
    def __init__(self, twitch: Twitch, data: JsonType, claimed_benefits: dict[str, datetime]):
        self._twitch: Twitch = twitch
        self.id: str = data["id"]
        self.name: str = data["name"]
        self.game: Game = Game(data["game"])
        self.linked: bool = data["self"]["isAccountConnected"]
        self.link_url: str = data["accountLinkURL"]
        
        # Campaign's image from game object - optimize regex operation
        box_art = data["game"]["boxArtURL"]
        self.image_url: URLType = remove_dimensions(box_art)
        
        # Parse timestamps once
        self.starts_at: datetime = timestamp(data["startAt"])
        self.ends_at: datetime = timestamp(data["endAt"])
        
        # Process allowed channels more efficiently
        allowed: JsonType = data["allow"]
        if allowed["channels"] and allowed.get("isEnabled", True):
            self.allowed_channels = [Channel.from_acl(twitch, c) for c in allowed["channels"]]
        else:
            self.allowed_channels = []
            
        # Initialize timed drops more efficiently
        self.timed_drops: dict[str, TimedDrop] = {}
        for drop_data in data["timeBasedDrops"]:
            drop_id = drop_data["id"]
            self.timed_drops[drop_id] = TimedDrop(self, drop_data, claimed_benefits)
            
        # Initialize cached properties
        self._cached_progress = None
        self._cached_remaining_minutes = None
        self._cached_required_minutes = None
        self._cached_has_badge_or_emote = None
        self._cached_finished = None
        self._cached_claimed_drops = None
        self._cached_remaining_drops = None

    def __repr__(self) -> str:
        return f"Campaign({self.game!s}, {self.name}, {self.claimed_drops}/{self.total_drops})"

    @property
    def drops(self) -> abc.Iterable[TimedDrop]:
        return self.timed_drops.values()

    @property
    def time_triggers(self) -> set[datetime]:
        return set(
            chain(
                (self.starts_at, self.ends_at),
                *((d.starts_at, d.ends_at) for d in self.timed_drops.values()),
            )
        )

    @property
    def active(self) -> bool:
        return self.starts_at <= datetime.now(timezone.utc) < self.ends_at

    @property
    def upcoming(self) -> bool:
        return datetime.now(timezone.utc) < self.starts_at

    @property
    def expired(self) -> bool:
        return self.ends_at <= datetime.now(timezone.utc)

    @property
    def total_drops(self) -> int:
        return len(self.timed_drops)

    @property
    def eligible(self) -> bool:
        return self.linked or self.has_badge_or_emote

    @property
    def has_badge_or_emote(self) -> bool:
        if self._cached_has_badge_or_emote is None:
            self._cached_has_badge_or_emote = any(
                benefit.type.is_badge_or_emote() 
                for drop in self.drops 
                for benefit in drop.benefits
            )
        return self._cached_has_badge_or_emote

    @property
    def finished(self) -> bool:
        if self._cached_finished is None:
            self._cached_finished = all(
                d.is_claimed or d.required_minutes <= 0 
                for d in self.drops
            )
        return self._cached_finished

    @property
    def claimed_drops(self) -> int:
        if self._cached_claimed_drops is None:
            self._cached_claimed_drops = sum(d.is_claimed for d in self.drops)
        return self._cached_claimed_drops

    @property
    def remaining_drops(self) -> int:
        if self._cached_remaining_drops is None:
            self._cached_remaining_drops = sum(not d.is_claimed for d in self.drops)
        return self._cached_remaining_drops

    @property
    def required_minutes(self) -> int:
        if self._cached_required_minutes is None:
            self._cached_required_minutes = max(
                (d.total_required_minutes for d in self.drops), 
                default=0
            )
        return self._cached_required_minutes

    @property
    def remaining_minutes(self) -> int:
        if self._cached_remaining_minutes is None:
            self._cached_remaining_minutes = max(
                (d.total_remaining_minutes for d in self.drops), 
                default=0
            )
        return self._cached_remaining_minutes

    @property
    def progress(self) -> float:
        if self._cached_progress is None:
            if not self.total_drops:
                self._cached_progress = 0.0
            else:
                self._cached_progress = sum(d.progress for d in self.drops) / self.total_drops
        return self._cached_progress

    @property
    def availability(self) -> float:
        return min(d.availability for d in self.drops)

    def _invalidate_cache(self, *properties) -> None:
        """Reset cached properties to force recalculation"""
        for prop in properties:
            cache_attr = f"_cached_{prop}"
            if hasattr(self, cache_attr):
                setattr(self, cache_attr, None)

    def _on_claim(self) -> None:
        # Reset cached properties
        self._invalidate_cache("finished", "claimed_drops", "remaining_drops", "progress")
        # Notify drops
        for drop in self.drops:
            drop._on_claim()

    def _on_minutes_changed(self) -> None:
        # Reset cached properties
        self._invalidate_cache("progress", "required_minutes", "remaining_minutes")
        # Notify drops
        for drop in self.drops:
            drop._on_total_minutes_changed()

    def get_drop(self, drop_id: str) -> TimedDrop | None:
        return self.timed_drops.get(drop_id)

    def _base_can_earn(self, channel: Channel | None = None) -> bool:
        # Short-circuit evaluations for performance
        if not self.eligible:  # Account is not eligible
            return False
            
        if not self.active:  # Campaign is not active
            return False
            
        # Channel validation - optimized to avoid expensive operations
        if channel is not None and self.allowed_channels:
            # Check if channel is in the allowed channels
            channel_id = channel.id
            for allowed_channel in self.allowed_channels:
                if allowed_channel.id == channel_id:
                    return True
            return False
            
        return True

    def can_earn(self, channel: Channel | None = None) -> bool:
        # True if any of the containing drops can be earned
        return self._base_can_earn(channel) and any(drop._base_can_earn() for drop in self.drops)

    def can_earn_within(self, stamp: datetime) -> bool:
        # Same as can_earn, but doesn't check the channel
        # and uses a future timestamp to see if we can earn this campaign later
        return (
            self.eligible
            and self.ends_at > datetime.now(timezone.utc)
            and self.starts_at < stamp
            and any(drop.can_earn_within(stamp) for drop in self.drops)
        )
