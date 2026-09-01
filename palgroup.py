#!/usr/bin/env python3
"""
palgroup.py
===========
A structural decoder/encoder for the ``GroupSaveDataMap.*.RawData`` blob
inside ``Level.sav`` -- i.e. guild membership data (who's in what guild,
the guild's name, base camp level, map markers, etc).

Why this file exists instead of using ``palworld-save-tools`` directly:
that library ships a decoder for this exact field
(``palworld_save_tools.rawdata.group``), but it's stale -- verified against
real, current save data (2026), it throws on *every single* real
``GroupSaveDataMap`` entry, both ``Guild`` and ``Organization`` types
("could not read 16 bytes for uuid" / "Warning: EOF not reached"). The
on-disk format has drifted since that decoder was last updated and the
maintained-fork's own game version compatibility notes confirm this is a
known, unresolved format change, not a bug in how we're calling it.

This module is a Python port of the binary layout used by
oMaN-Rod/uesave-rs (branch ``pluggable-game-support``,
``uesave/src/games/palworld/groups.rs`` + ``types.rs``), a Rust
implementation that IS current, cross-checked commit-for-commit against
that repo at the time this was written. Verified against a real save with
13 real ``GroupSaveDataMap`` entries (7 ``Organization`` + 6 ``Guild``,
including guilds with a custom name, base camps, and 21KB+ of guild-owned
Pal/character handle data): every entry decodes and re-encodes
byte-for-byte identical to the original.

The one group type NOT verified against real data is
``EPalGroupType::IndependentGuild`` -- it never appeared in any real save
inspected while building this. The decoder for it is included (ported the
same way, from the same source), but every caller in this project treats a
round-trip failure on ANY entry -- known type or not -- as "the format
looks different than expected" and safely skips guild-data editing
entirely rather than guessing. See ``verify_group_map_roundtrip()`` below,
which every guild-editing code path in hostfix.py calls first.

All integers are little-endian, matching Unreal's native serialization.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

# ``FGuid`` here is represented as a bare (a, b, c, d) tuple of 4 raw
# little-endian uint32s -- exactly what you get from
# ``struct.unpack("<IIII", guid_str_to_raw(some_dash_string))`` (see
# ``uid_str_to_tuple``/``tuple_to_uid_str`` below, which piggyback on
# palcommon's already-verified guid_str_to_raw/raw_to_guid_str so this
# module never has to re-derive Unreal's GUID display-string byte order
# from scratch).


class GroupCodecError(Exception):
    """Raised on any malformed/unexpected byte layout while decoding a
    GroupSaveDataMap RawData blob. Callers should treat this as "the save
    format doesn't match what this module expects" and skip guild editing
    entirely rather than guess -- never caught-and-ignored deeper inside
    this module itself."""


# --------------------------------------------------------------------------
# UID conversion helpers (bridge to palcommon's dash-string convention)
# --------------------------------------------------------------------------
def uid_str_to_tuple(guid_str: str) -> tuple[int, int, int, int]:
    from palcommon import guid_str_to_raw

    return struct.unpack("<IIII", guid_str_to_raw(guid_str))


def tuple_to_uid_str(t: tuple[int, int, int, int]) -> str:
    from palcommon import raw_to_guid_str

    return raw_to_guid_str(struct.pack("<IIII", *t))


# --------------------------------------------------------------------------
# Low-level byte reader/writer
# --------------------------------------------------------------------------
class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise GroupCodecError(
                f"unexpected EOF: wanted {n} byte(s) at offset {self.pos}, "
                f"only {len(self.data) - self.pos} remain"
            )
        b = self.data[self.pos : self.pos + n]
        self.pos += n
        return b

    def u8(self) -> int:
        return self.take(1)[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def i64(self) -> int:
        return struct.unpack("<q", self.take(8))[0]

    def f64(self) -> float:
        return struct.unpack("<d", self.take(8))[0]

    def guid(self) -> tuple[int, int, int, int]:
        return struct.unpack("<IIII", self.take(16))

    def string(self) -> str:
        """Unreal FString: i32 length. Positive = ASCII (length includes
        the trailing NUL). Negative = UTF-16LE (abs(length) chars,
        includes the trailing NUL char). Zero = empty, no data follows."""
        n = self.i32()
        if n == 0:
            return ""
        if n > 0:
            raw = self.take(n)
            return raw[:-1].decode("ascii", errors="replace")
        raw = self.take(-n * 2)
        return raw[:-2].decode("utf-16-le", errors="replace")


class _Writer:
    def __init__(self):
        self.buf = bytearray()

    def bytes(self) -> bytes:
        return bytes(self.buf)

    def raw(self, b: bytes) -> None:
        self.buf += b

    def u8(self, v: int) -> None:
        self.buf += struct.pack("<B", v)

    def u32(self, v: int) -> None:
        self.buf += struct.pack("<I", v)

    def i32(self, v: int) -> None:
        self.buf += struct.pack("<i", v)

    def i64(self, v: int) -> None:
        self.buf += struct.pack("<q", v)

    def f64(self, v: float) -> None:
        self.buf += struct.pack("<d", v)

    def guid(self, g: tuple[int, int, int, int]) -> None:
        self.buf += struct.pack("<IIII", *g)

    def string(self, s: str) -> None:
        if s == "":
            self.i32(0)
            return
        try:
            raw = s.encode("ascii") + b"\x00"
            self.i32(len(raw))
            self.raw(raw)
        except UnicodeEncodeError:
            raw = s.encode("utf-16-le") + b"\x00\x00"
            self.i32(-(len(raw) // 2))
            self.raw(raw)


# --------------------------------------------------------------------------
# Shared sub-structures
# --------------------------------------------------------------------------
@dataclass
class PlayerInfoDetails:
    last_online_real_time: int
    player_name: str

    @staticmethod
    def read(r: _Reader) -> "PlayerInfoDetails":
        return PlayerInfoDetails(r.i64(), r.string())

    def write(self, w: _Writer) -> None:
        w.i64(self.last_online_real_time)
        w.string(self.player_name)


@dataclass
class PlayerInfo:
    player_uid: tuple
    info: PlayerInfoDetails

    @staticmethod
    def read(r: _Reader) -> "PlayerInfo":
        uid = r.guid()
        return PlayerInfo(uid, PlayerInfoDetails.read(r))

    def write(self, w: _Writer) -> None:
        w.guid(self.player_uid)
        self.info.write(w)


@dataclass
class GuildPlayerWithRole:
    player_uid: tuple
    info: PlayerInfoDetails
    role: int

    @staticmethod
    def read(r: _Reader) -> "GuildPlayerWithRole":
        p = PlayerInfo.read(r)
        role = r.u8()
        return GuildPlayerWithRole(p.player_uid, p.info, role)

    def write(self, w: _Writer) -> None:
        w.guid(self.player_uid)
        self.info.write(w)
        w.u8(self.role)


@dataclass
class InstanceId:
    guid: tuple
    instance_id: tuple

    @staticmethod
    def read(r: _Reader) -> "InstanceId":
        return InstanceId(r.guid(), r.guid())

    def write(self, w: _Writer) -> None:
        w.guid(self.guid)
        w.guid(self.instance_id)


@dataclass
class GuildMarker:
    marker_id: tuple
    icon_location: tuple  # (x, y, z) float64
    icon_type: int
    owner_player_uid: tuple

    @staticmethod
    def read(r: _Reader) -> "GuildMarker":
        marker_id = r.guid()
        loc = (r.f64(), r.f64(), r.f64())
        icon_type = r.i32()
        owner = r.guid()
        return GuildMarker(marker_id, loc, icon_type, owner)

    def write(self, w: _Writer) -> None:
        w.guid(self.marker_id)
        for c in self.icon_location:
            w.f64(c)
        w.i32(self.icon_type)
        w.guid(self.owner_player_uid)


@dataclass
class GuildRolePermission:
    role: int
    permissions: list

    @staticmethod
    def read(r: _Reader) -> "GuildRolePermission":
        role = r.u8()
        n = r.u32()
        return GuildRolePermission(role, [r.u8() for _ in range(n)])

    def write(self, w: _Writer) -> None:
        w.u8(self.role)
        w.u32(len(self.permissions))
        for p in self.permissions:
            w.u8(p)


# --------------------------------------------------------------------------
# Guild tail -- a real save can carry either shape; PalGuildTail::read in
# the reference implementation figures out which by speculatively parsing
# as PostUpdate first and checking whether that exactly consumes the rest
# of the buffer.
# --------------------------------------------------------------------------
@dataclass
class GuildTailPreUpdate:
    admin_player_uid: tuple
    players: list  # [PlayerInfo]
    trailing_bytes: bytes  # 4 bytes, unknown meaning -- preserved verbatim

    @staticmethod
    def read(r: _Reader) -> "GuildTailPreUpdate":
        admin = r.guid()
        n = r.u32()
        players = [PlayerInfo.read(r) for _ in range(n)]
        trailing = r.take(4)
        return GuildTailPreUpdate(admin, players, trailing)

    def write(self, w: _Writer) -> None:
        w.guid(self.admin_player_uid)
        w.u32(len(self.players))
        for p in self.players:
            p.write(w)
        w.raw(self.trailing_bytes)


@dataclass
class GuildTailPostUpdate:
    guild_chest_allowed_roles: list
    unknown_i32: int
    admin_player_uid: tuple
    players: list  # [GuildPlayerWithRole]
    role_permissions: list  # [GuildRolePermission]
    trailing_bytes: bytes  # 4 bytes, unknown meaning -- preserved verbatim

    @staticmethod
    def read(r: _Reader) -> "GuildTailPostUpdate":
        chest_n = r.u32()
        chest_roles = [r.u8() for _ in range(chest_n)]
        unknown_i32 = r.i32()
        admin = r.guid()
        player_n = r.u32()
        players = [GuildPlayerWithRole.read(r) for _ in range(player_n)]
        perm_n = r.u32()
        perms = [GuildRolePermission.read(r) for _ in range(perm_n)]
        trailing = r.take(4)
        return GuildTailPostUpdate(
            chest_roles, unknown_i32, admin, players, perms, trailing
        )

    def write(self, w: _Writer) -> None:
        w.u32(len(self.guild_chest_allowed_roles))
        for b in self.guild_chest_allowed_roles:
            w.u8(b)
        w.i32(self.unknown_i32)
        w.guid(self.admin_player_uid)
        w.u32(len(self.players))
        for p in self.players:
            p.write(w)
        w.u32(len(self.role_permissions))
        for perm in self.role_permissions:
            perm.write(w)
        w.raw(self.trailing_bytes)


def _read_guild_tail(r: _Reader):
    start = r.pos
    try:
        tail = GuildTailPostUpdate.read(r)
        if r.remaining() == 0:
            return tail
    except GroupCodecError:
        pass
    r.pos = start
    return GuildTailPreUpdate.read(r)


# --------------------------------------------------------------------------
# Group variants
# --------------------------------------------------------------------------
@dataclass
class GuildGroup:
    org_type: int
    leading_bytes: bytes  # 4 bytes, unknown meaning -- preserved verbatim
    base_ids: list
    unknown_1: int
    base_camp_level: int
    map_object_instance_ids_base_camp_points: list
    guild_name: str
    last_guild_name_modifier_player_uid: tuple
    guild_markers: list
    tail: object  # GuildTailPreUpdate | GuildTailPostUpdate

    @staticmethod
    def read(r: _Reader) -> "GuildGroup":
        org_type = r.u8()
        leading = r.take(4)
        base_id_n = r.u32()
        base_ids = [r.guid() for _ in range(base_id_n)]
        unknown_1 = r.i32()
        base_camp_level = r.i32()
        camp_pt_n = r.u32()
        camp_pts = [r.guid() for _ in range(camp_pt_n)]
        guild_name = r.string()
        last_mod = r.guid()
        marker_n = r.u32()
        markers = [GuildMarker.read(r) for _ in range(marker_n)]
        tail = _read_guild_tail(r)
        return GuildGroup(
            org_type, leading, base_ids, unknown_1, base_camp_level,
            camp_pts, guild_name, last_mod, markers, tail,
        )

    def write(self, w: _Writer) -> None:
        w.u8(self.org_type)
        w.raw(self.leading_bytes)
        w.u32(len(self.base_ids))
        for g in self.base_ids:
            w.guid(g)
        w.i32(self.unknown_1)
        w.i32(self.base_camp_level)
        w.u32(len(self.map_object_instance_ids_base_camp_points))
        for g in self.map_object_instance_ids_base_camp_points:
            w.guid(g)
        w.string(self.guild_name)
        w.guid(self.last_guild_name_modifier_player_uid)
        w.u32(len(self.guild_markers))
        for m in self.guild_markers:
            m.write(w)
        self.tail.write(w)


@dataclass
class IndependentGuildGroup:
    """Never observed in real save data while building this -- ported from
    the same reference source as everything else here, but treat any use
    of this path with extra suspicion (it's covered by the same
    round-trip self-check as everything else, so a mismatch here still
    fails safely)."""

    org_type: int
    base_camp_level: int
    map_object_instance_ids_base_camp_points: list
    guild_name: str
    player_uid: tuple
    guild_name_2: str
    last_online_real_time: int
    player_name: str

    @staticmethod
    def read(r: _Reader) -> "IndependentGuildGroup":
        org_type = r.u8()
        base_camp_level = r.i32()
        n = r.u32()
        pts = [r.guid() for _ in range(n)]
        guild_name = r.string()
        player_uid = r.guid()
        guild_name_2 = r.string()
        last_online = r.i64()
        player_name = r.string()
        return IndependentGuildGroup(
            org_type, base_camp_level, pts, guild_name, player_uid,
            guild_name_2, last_online, player_name,
        )

    def write(self, w: _Writer) -> None:
        w.u8(self.org_type)
        w.i32(self.base_camp_level)
        w.u32(len(self.map_object_instance_ids_base_camp_points))
        for g in self.map_object_instance_ids_base_camp_points:
            w.guid(g)
        w.string(self.guild_name)
        w.guid(self.player_uid)
        w.string(self.guild_name_2)
        w.i64(self.last_online_real_time)
        w.string(self.player_name)


@dataclass
class OrganizationGroup:
    """No player-identifying field survives decoding for this type (just
    a type byte and 12 bytes of unexplained data) -- callers never attempt
    to edit these."""

    org_type: int
    trailing_bytes: bytes  # 12 bytes

    @staticmethod
    def read(r: _Reader) -> "OrganizationGroup":
        org_type = r.u8()
        trailing = r.take(12)
        return OrganizationGroup(org_type, trailing)

    def write(self, w: _Writer) -> None:
        w.u8(self.org_type)
        w.raw(self.trailing_bytes)


GUILD_TYPES = ("EPalGroupType::Guild", "EPalGroupType::IndependentGuild")


@dataclass
class GroupData:
    group_id: tuple
    group_name: str
    individual_character_handle_ids: list  # [InstanceId]
    group_type: str
    variant: object  # GuildGroup | IndependentGuildGroup | OrganizationGroup

    @staticmethod
    def decode(raw: bytes, group_type: str) -> "GroupData":
        r = _Reader(raw)
        group_id = r.guid()
        group_name = r.string()
        handle_n = r.u32()
        handles = [InstanceId.read(r) for _ in range(handle_n)]
        if group_type == "EPalGroupType::Guild":
            variant = GuildGroup.read(r)
        elif group_type == "EPalGroupType::IndependentGuild":
            variant = IndependentGuildGroup.read(r)
        elif group_type == "EPalGroupType::Organization":
            variant = OrganizationGroup.read(r)
        else:
            raise GroupCodecError(f"unknown group_type {group_type!r}")
        if r.remaining() != 0:
            raise GroupCodecError(
                f"{r.remaining()} unconsumed byte(s) left after decoding "
                f"a {group_type} group"
            )
        return GroupData(group_id, group_name, handles, group_type, variant)

    def encode(self) -> bytes:
        w = _Writer()
        w.guid(self.group_id)
        w.string(self.group_name)
        w.u32(len(self.individual_character_handle_ids))
        for h in self.individual_character_handle_ids:
            h.write(w)
        self.variant.write(w)
        return w.bytes()

    def player_uids(self) -> list[tuple]:
        """Every player UID this entry references (admin + members for a
        guild; the single member for an IndependentGuild; empty for
        Organization, which has none we can see)."""
        if isinstance(self.variant, GuildGroup):
            tail = self.variant.tail
            return [p.player_uid for p in tail.players]
        if isinstance(self.variant, IndependentGuildGroup):
            return [self.variant.player_uid]
        return []


# --------------------------------------------------------------------------
# Whole-map helpers -- used by hostfix.py
# --------------------------------------------------------------------------
def iter_group_entries(wsd: dict):
    """Yield (entry_dict, group_type) for every GroupSaveDataMap entry in a
    dumped worldSaveData dict. No-op (yields nothing) if the map is
    missing entirely."""
    gsm = wsd.get("GroupSaveDataMap", {}).get("value")
    if not gsm:
        return
    for entry in gsm:
        try:
            gt = entry["value"]["GroupType"]["value"]["value"]
        except (KeyError, TypeError):
            continue
        yield entry, gt


def get_raw_data(entry: dict) -> bytes:
    return bytes(entry["value"]["RawData"]["value"]["values"])


def set_raw_data(entry: dict, raw: bytes) -> None:
    entry["value"]["RawData"]["value"] = {"values": list(raw)}


def verify_group_map_roundtrip(wsd: dict) -> tuple[bool, str]:
    """Decode-then-re-encode EVERY GroupSaveDataMap entry and confirm it's
    byte-for-byte identical to the original, for every entry in the map --
    not just the one(s) about to be edited. This is the safety gate every
    guild-editing code path in hostfix.py calls first: if this file's
    guild-data format has drifted from what this module expects (a future
    game update, same way it already drifted once from
    palworld-save-tools' bundled decoder), this fails immediately and
    guild editing is skipped entirely rather than risking a bad write to
    data this module doesn't actually understand.

    Returns (True, "") if every entry round-trips clean, or
    (False, reason) on the first mismatch/decode failure."""
    for entry, gt in iter_group_entries(wsd):
        raw = get_raw_data(entry)
        try:
            decoded = GroupData.decode(raw, gt)
        except GroupCodecError as e:
            return False, f"a {gt} entry didn't decode as expected: {e}"
        reencoded = decoded.encode()
        if reencoded != raw:
            return False, (
                f"a {gt} entry decoded but didn't re-encode identically "
                "(the format understood here doesn't fully match this file)"
            )
    return True, ""
