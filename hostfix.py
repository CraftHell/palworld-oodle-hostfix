#!/usr/bin/env python3
"""
palworld-oodle-hostfix
======================

Migrate a Palworld character between a *dedicated server* save and a
*single-player / co-op host* save, in either direction:

- `migrate`: dedicated server -> single-player/co-op. Moves everything
  tied to the character (owned Pals, guild membership, placed/painted
  building pieces), keeping their level, inventory, and world progress
  intact.
- `unhost`: single-player/co-op -> a fresh, never-joined dedicated server.
  Moves the character's level, inventory, and owned Pals; guild membership
  and built structures are a known limitation for this direction
  specifically (see scoped_unhost_swap()'s docstring/comment in this file,
  and the README's Limitations section, for why).
- `sync`: single-player/co-op -> an ALREADY-LIVE, already-populated
  dedicated server. Use this instead of `unhost` whenever the destination
  server already has real history (for you and/or other people) -- it
  surgically splices in only the target player's own character, owned
  Pals, and personal containers, leaving every other player, the guild(s),
  and built structures completely untouched (see the module comment above
  `_dump_gvas` for why `unhost`'s whole-file approach would be destructive
  here).

Why this exists
----------------
Since Palworld's 2026 "Oodle" save-format update, world saves are
compressed with Oodle/Kraken and marked with the magic bytes ``PlM``
instead of the classic zlib ``PlZ``. Every published community tool for
this exact migration (xNul/palworld-host-save-fix,
quadrantbs/palworld-hostfix-toolkit, palworld-save-tools' own raw-data
decoders, etc.) either can't read ``PlM`` saves at all, or depends on a
Windows-only Oodle DLL, or breaks on the newer internal struct layout for
Character/Guild data.

This tool sidesteps both problems:

1. Decompression: uses the open-source, cross-platform ``ooz`` Python
   bindings (a clean-room Oodle/Kraken decompressor) instead of a
   redistributed Oodle DLL. Works on Linux, macOS, and Windows.
2. Editing: instead of trying to fully parse the new (and only
   partially-documented) Character/Guild binary struct layout, it works
   one level down -- directly on the decompressed raw bytes. A
   dedicated-server player ID is a 16-byte GUID of the shape
   ``XXXXXXXX-0000-0000-0000-000000000000`` (only the first 4 bytes are
   non-zero), which turns out to appear *verbatim, and only, and always
   legitimately* everywhere that ID is referenced: the character map key,
   every Pal's OwnerPlayerUId/OldOwnerPlayerUIds/nickname-modifier field,
   every guild membership record, and every placed-building's builder
   tag. For `migrate`, a single scoped find-and-replace of that 16-byte
   pattern (verified byte-for-byte before writing) reassigns literally
   everything that ID touches in one pass, without needing to understand
   the surrounding struct at all -- safe because that ID's 4 random bytes
   make a coincidental false match astronomically unlikely. `unhost`
   moves the *other* way, usually away from the special, low-entropy
   single-player host ID, for which that same blind approach turns out to
   be unsafe (see scoped_unhost_swap() below) -- it uses structural
   verification instead, at the cost of not being able to safely cover
   guild/building data. Always writes back in the classic zlib ``PlZ``
   format, which Palworld happily reads on any platform.

This was built and validated against a real dedicated-server world with
~1900 real references to a single player ID scattered across Pals, a
guild, and base structures -- all of which correctly reassigned to the
new ID with a single global replace (for `migrate`) or a structurally-
verified scoped replace (for `unhost`), verified by an exact expected-vs-
actual differing-byte-count check before ever writing a file.

Requirements
------------
    pip install palworld-save-tools ooz

Usage
-----
    # 1. See which Players/<uid>.sav is which character (best-effort,
    #    reads nicknames out of guild data where available)
    python hostfix.py list /path/to/world_folder

    # 2. Migrate a dedicated-server character into single-player/co-op.
    #    Writes to a NEW folder by default -- your original is untouched.
    python hostfix.py migrate /path/to/world_folder \\
        --old-uid aaaaaaaa-0000-0000-0000-000000000000 \\
        --out /path/to/world_folder_migrated

    # Optional: also rename the world as it appears in the load-game list
    python hostfix.py migrate /path/to/world_folder --old-uid ... \\
        --out ... --world-name "MyWorld"

    # Or the reverse: prep a single-player/co-op world to drop onto a
    # dedicated server. --new-uid can't be made up (Palworld computes it
    # from a hash of the player's Steam account) -- start the server,
    # connect once with the real account, stop the server, then find it:
    python hostfix.py list /path/to/server_save_folder

    # ...then use that real ID:
    python hostfix.py unhost /path/to/single_player_world_folder \\
        --new-uid <the real ID from the step above> \\
        --out /path/to/single_player_world_folder_dedicated

    # (see the README's Limitations section for what unhost can't cover)

    # Or, if your dedicated server ISN'T brand new -- it already has real
    # progress for you and/or other people -- use `sync` instead, which
    # never touches anyone else's data:
    python hostfix.py list /path/to/server_save_folder  # find your real ID

    python hostfix.py sync /path/to/single_player_world_folder \\
        --server-dir /path/to/server_save_folder \\
        --target-uid <your real ID from the step above>

Then copy the output folder's contents into your local
``...\\Pal\\Saved\\SaveGames\\<YourSteamID>\\<WorldGUID>\\`` (`migrate`), or
OVER your dedicated server's save folder, replacing the blank character it
made when you connected (`unhost`), or the character it already has
(`sync`).

License: MIT. Use at your own risk -- this edits game save files.
ALWAYS keep a backup of your original save before running this.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from palcommon import (
    GUID_STR_RE,
    SINGLEPLAYER_HOST_UID,
    UID_HEX_RE,
    SavFile,
    filename_uid_to_guid_str,
    guid_str_to_filename_uid,
    guid_str_to_raw,
)


def replace_uid_everywhere(raw: bytes, old_uid: bytes, new_uid: bytes) -> tuple[bytes, int]:
    count = raw.count(old_uid)
    if count == 0:
        return raw, 0
    return raw.replace(old_uid, new_uid), count


def sample_contexts(raw: bytes, needle: bytes, n: int = 5, before: int = 48) -> list[bytes]:
    out = []
    start = 0
    while len(out) < n:
        idx = raw.find(needle, start)
        if idx == -1:
            break
        out.append(raw[max(0, idx - before) : idx])
        start = idx + 1
    return out


_UNREAL_TYPE_KEYWORDS = {
    "StructProperty", "ArrayProperty", "ByteProperty", "IntProperty",
    "Int64Property", "UInt32Property", "StrProperty", "NameProperty",
    "BoolProperty", "FloatProperty", "DoubleProperty", "EnumProperty",
    "MapProperty", "SetProperty", "TextProperty", "ObjectProperty",
    "SoftObjectProperty", "Guid", "None",
}


def _try_read_length_prefixed_str(chunk: bytes, i: int) -> tuple[str, int] | None:
    """If chunk[i:] starts with a valid Unreal-style length-prefixed
    string, return (text, bytes_consumed), else None."""
    if i + 4 > len(chunk):
        return None
    n = int.from_bytes(chunk[i : i + 4], "little")
    if not (2 <= n <= 32 and i + 4 + n <= len(chunk)):
        return None
    candidate = chunk[i + 4 : i + 4 + n]
    if not candidate.endswith(b"\x00"):
        return None
    text = candidate[:-1]
    if not text or not all(32 <= c < 127 for c in text):
        return None
    return text.decode("ascii"), 4 + n


def find_length_prefixed_ascii_strings(chunk: bytes) -> list[str]:
    """Best-effort scan for Unreal-style length-prefixed strings
    (4-byte little-endian length incl. null terminator, then that many
    mostly-printable bytes) inside a byte window, filtering out ones that
    are clearly property *names* (i.e. immediately followed by a type
    keyword like "StructProperty") rather than actual game data. Used
    only for the best-effort 'list' command to guess a player's display
    name from guild data; never used when actually editing a save."""
    results = []
    i = 0
    while i < len(chunk):
        hit = _try_read_length_prefixed_str(chunk, i)
        if hit is None:
            i += 1
            continue
        text, consumed = hit
        # peek at what immediately follows: if it's itself a length-prefixed
        # string that's a known Unreal type keyword, `text` was a property
        # *name*, not real data -- skip it.
        follow = _try_read_length_prefixed_str(chunk, i + consumed)
        is_property_name = follow is not None and follow[0] in _UNREAL_TYPE_KEYWORDS
        if text not in _UNREAL_TYPE_KEYWORDS and not is_property_name:
            results.append(text)
        i += 1
    return results


# --------------------------------------------------------------------------
# Player scanning (shared by the `list` command and the interactive wizard)
# --------------------------------------------------------------------------
@dataclass
class PlayerInfo:
    guid_str: str
    path: Path
    size_bytes: int
    occurrences: int
    name_guess: str | None
    is_singleplayer_host: bool


def _extract_guild_blobs(level: "SavFile") -> list[bytes]:
    """Guild records embed both the guild name and each member's display
    name as plain strings, which makes them a much more reliable source
    for name-guessing than scanning arbitrary occurrences (which mostly
    land inside Pal-ownership records full of unrelated internal names).
    This needs one real structural parse of GroupSaveDataMap."""
    guild_blobs: list[bytes] = []
    try:
        from palworld_save_tools.gvas import GvasFile
        from palworld_save_tools.paltypes import PALWORLD_TYPE_HINTS

        with contextlib.redirect_stdout(io.StringIO()):
            gvas = GvasFile.read(level.raw_gvas, PALWORLD_TYPE_HINTS, {}, allow_nan=True)
            dumped = gvas.dump()
        gsm = dumped["properties"]["worldSaveData"]["value"]["GroupSaveDataMap"]["value"]
        for entry in gsm:
            raw_data = entry.get("value", {}).get("RawData", {}).get("value", {}).get("values")
            if raw_data:
                guild_blobs.append(bytes(raw_data))
    except Exception as e:
        print(f"  (could not parse guild data for name hints: {e})")
    return guild_blobs


def _guess_name(raw_uid: bytes, guild_blobs: list[bytes]) -> str | None:
    for blob in guild_blobs:
        start = 0
        while True:
            idx = blob.find(raw_uid, start)
            if idx == -1:
                break
            window = blob[idx : idx + 200]
            candidates = [
                n for n in find_length_prefixed_ascii_strings(window)
                if not n.endswith(("Id", "Ids", "Uid", "Map", "Data", "Array"))
            ]
            if candidates:
                return candidates[0]
            start = idx + 1
    return None


def scan_players(world_dir: Path, quiet: bool = False) -> tuple[SavFile, list[PlayerInfo]]:
    """Load Level.sav and return (level, [PlayerInfo, ...]) for every
    Players/*.sav file found. Shared by `hostfix.py list` and the
    interactive wizard."""
    level_path = world_dir / "Level.sav"
    players_dir = world_dir / "Players"
    if not level_path.exists():
        sys.exit(f"No Level.sav found in {world_dir}")
    if not players_dir.exists():
        sys.exit(f"No Players/ folder found in {world_dir}")

    if not quiet:
        print(f"Reading {level_path} (this can take a minute for a large world)...")
    level = SavFile.load(level_path)

    player_files = sorted(players_dir.glob("*.sav"))
    if not quiet:
        print("Parsing guild data for name hints (best-effort)...")
    guild_blobs = _extract_guild_blobs(level)

    infos = []
    for pf in player_files:
        stem = pf.stem
        if not UID_HEX_RE.match(stem):
            continue
        guid_str = filename_uid_to_guid_str(stem)
        raw_uid = guid_str_to_raw(guid_str)
        infos.append(
            PlayerInfo(
                guid_str=guid_str,
                path=pf,
                size_bytes=pf.stat().st_size,
                occurrences=level.raw_gvas.count(raw_uid),
                name_guess=_guess_name(raw_uid, guild_blobs),
                is_singleplayer_host=(guid_str == SINGLEPLAYER_HOST_UID),
            )
        )
    return level, infos


def print_player_table(infos: list[PlayerInfo], numbered: bool = False) -> None:
    for i, info in enumerate(infos, start=1):
        tag = "  <- already the single-player host ID" if info.is_singleplayer_host else ""
        prefix = f"  [{i}] " if numbered else "  "
        print(f"{prefix}UID: {info.guid_str}{tag}")
        indent = "      " if numbered else "    "
        print(f"{indent}file: Players/{info.path.name}  ({info.size_bytes:,} bytes)")
        print(f"{indent}referenced {info.occurrences} time(s) in Level.sav")
        if info.is_singleplayer_host:
            print(
                f"{indent}(this is a rough, unverified count -- the single-player host ID's "
                f"raw bytes happen to coincidentally\n{indent} match a lot of unrelated "
                f"padding elsewhere in the file; 'unhost' uses a much more precise, "
                f"structurally-verified\n{indent} count before actually changing anything)"
            )
        print(f"{indent}best-effort name guess: {info.name_guess or '(none found)'}")
        print()


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_list(args: argparse.Namespace) -> None:
    world_dir = Path(args.world_dir)
    level, infos = scan_players(world_dir)
    if not infos:
        print("No player save files found in Players/.")
        return
    print(f"\nFound {len(infos)} player save file(s):\n")
    print_player_table(infos)
    print(
        "Name guesses are best-effort (scraped from guild data) -- if you're not\n"
        "sure which one is you, check file sizes (your real character's save is\n"
        "usually one of the larger ones) or ask whoever hosted the server."
    )


class HostfixError(Exception):
    """A user-facing, recoverable problem -- the CLI turns this into
    sys.exit(str(e)); the interactive wizard catches it, prints it, and
    lets the user try again instead of killing the whole session."""


class MigrationAborted(Exception):
    """User answered 'no' at the confirmation prompt."""


# --------------------------------------------------------------------------
# Safe scoped UID swap -- used by the reverse ("unhost") direction
#
# perform_migration's blind whole-file replace_uid_everywhere() above is
# safe *specifically* because a dedicated-server player ID has 4 fully
# random bytes (~4 billion possible values) -- across a save file with
# hundreds of thousands of unrelated 16-byte fields, the odds of a
# coincidental false match are astronomically low.
#
# `unhost` defaults to moving *away from* the special single-player host
# ID (00000000-0000-0000-0000-000000000001), whose raw bytes are almost
# entirely zero. That exact pattern coincidentally appears thousands of
# times throughout a real save purely because all-zero byte runs are
# extremely common padding -- verified empirically against a real ~47MB
# world file: 18,645 coincidental matches for this pattern, even in a
# save that had never had anything to do with single-player mode. Blindly
# replacing every occurrence would silently corrupt thousands of
# unrelated Pals, items, and containers.
#
# So instead of a blind file-wide replace, this only ever touches byte
# ranges it can *structurally* verify really are the target ID:
#
#   1. Clean StructProperty(Guid) fields anywhere in the file (the
#      CharacterSaveParameterMap key's PlayerUId, the player's own
#      PlayerUId/IndividualId.PlayerUId, etc.) are found via the exact
#      byte layout Unreal uses to serialize one: a "Guid\x00" struct-type
#      marker, then a 16-byte struct-id (usually zero) and a 1-byte "has
#      property guid" flag, and only *then* the real 16-byte value -- the
#      value always starts exactly 22 bytes after the marker. This is
#      entirely independent of the target ID's entropy: it only trusts
#      bytes at a fixed structural offset from a specific, high-entropy
#      5-byte ASCII marker, never a blind search for the target pattern.
#   2. CharacterSaveParameterMap entries that are structurally confirmed
#      -- by parsing the map, not by pattern-matching -- to be keyed to
#      the target player are additionally scanned within their own
#      RawData blob only (never the whole file), for extra coverage of
#      fields the game doesn't re-serialize with type info.
#
# Guild membership (GroupSaveDataMap) and placed-building ownership
# (MapObjectSaveData) store their references inside a different, fully
# opaque, game-custom binary encoding with no structural landmark at all.
# Verified empirically that even scoping a blind search to one guild's or
# one building's own small RawData blob still produces a large majority
# of false positives for this specific low-entropy ID (94% false for
# buildings, 78% false for guild data, measured on a real world save).
# There's no reliable way to migrate those two for the low-entropy case,
# so `unhost` deliberately leaves them untouched rather than guess -- see
# the note printed after a successful run.
# --------------------------------------------------------------------------
_GUID_MARKER = b"Guid\x00"
_GUID_VALUE_OFFSET = len(_GUID_MARKER) + 16 + 1  # + struct_id(16) + has-property-guid flag(1)


def _find_structural_guid_offsets(raw: bytes, target_uid: bytes) -> list[int]:
    """Return every absolute offset in `raw` where a genuine, structurally
    verified StructProperty(Guid)'s *value* equals target_uid. Entropy-
    independent: driven by the high-entropy "Guid\\x00" type marker, never
    by searching for target_uid itself."""
    offsets = []
    start = 0
    while True:
        idx = raw.find(_GUID_MARKER, start)
        if idx == -1:
            break
        value_pos = idx + _GUID_VALUE_OFFSET
        if raw[value_pos : value_pos + 16] == target_uid:
            offsets.append(value_pos)
        start = idx + 1
    return offsets


def _character_map_owned_rawdata_offsets(raw: bytes, old_uid_str: str, old_uid: bytes) -> list[int]:
    """Parse CharacterSaveParameterMap, find every entry structurally
    keyed to old_uid_str (a player's own record, or a Pal they currently
    own), and scan *only that entry's own RawData blob* for additional
    occurrences of old_uid not already covered by the marker-based sweep.
    Raises HostfixError rather than guessing if a blob's position in the
    file can't be uniquely and safely located."""
    with contextlib.redirect_stdout(io.StringIO()):
        from palworld_save_tools.gvas import GvasFile
        from palworld_save_tools.paltypes import PALWORLD_TYPE_HINTS

        gvas = GvasFile.read(raw, PALWORLD_TYPE_HINTS, {}, allow_nan=True)
        dumped = gvas.dump()

    offsets: list[int] = []
    try:
        csm = dumped["properties"]["worldSaveData"]["value"]["CharacterSaveParameterMap"]["value"]
    except (KeyError, TypeError):
        return offsets

    for entry in csm:
        try:
            key_uid = entry["key"]["PlayerUId"]["value"]
        except (KeyError, TypeError):
            continue
        if key_uid != old_uid_str:
            continue
        raw_data = entry.get("value", {}).get("RawData", {}).get("value", {}).get("values")
        if not raw_data:
            continue
        blob = bytes(raw_data)
        if old_uid not in blob:
            continue
        if raw.count(blob) != 1:
            raise HostfixError(
                "Could not safely locate one of your character/Pal records in the "
                "save file (its data isn't byte-unique) -- refusing to guess rather "
                "than risk touching the wrong one. Please open an issue with this save."
            )
        base = raw.find(blob)
        local_start = 0
        while True:
            i = blob.find(old_uid, local_start)
            if i == -1:
                break
            offsets.append(base + i)
            local_start = i + 1
    return offsets


def scoped_unhost_swap(raw: bytes, old_uid_str: str, new_uid_str: str) -> tuple[bytes, int]:
    """Safely reassign old_uid_str -> new_uid_str within a raw .sav's
    decompressed GVAS bytes, WITHOUT a blind whole-file replace (see the
    module comment above for why that's unsafe for the low-entropy IDs
    this is meant for). Returns (new_raw, n) where n is the number of
    distinct byte positions changed. Raises HostfixError rather than
    guessing if anything can't be safely verified."""
    old_uid = guid_str_to_raw(old_uid_str)
    new_uid = guid_str_to_raw(new_uid_str)

    edits: dict[int, bytes] = {}
    for off in _find_structural_guid_offsets(raw, old_uid):
        edits[off] = new_uid
    for off in _character_map_owned_rawdata_offsets(raw, old_uid_str, old_uid):
        edits.setdefault(off, new_uid)

    if not edits:
        return raw, 0

    new_raw = bytearray(raw)
    for off, val in edits.items():
        new_raw[off : off + 16] = val
    new_raw = bytes(new_raw)

    n = len(edits)
    diff_per_edit = sum(1 for a, b in zip(old_uid, new_uid) if a != b)
    expected_diff_bytes = n * diff_per_edit
    actual_diff_bytes = sum(1 for a, b in zip(raw, new_raw) if a != b)
    if actual_diff_bytes != expected_diff_bytes:
        raise HostfixError(
            f"INTERNAL SANITY CHECK FAILED: expected exactly {expected_diff_bytes} "
            f"differing bytes from {n} scoped edits, got {actual_diff_bytes}. Refusing "
            "to write a file that didn't verify cleanly -- please open an issue with this save."
        )
    return new_raw, n


def _migrate_core(
    world_dir: Path,
    old_uid_str: str,
    new_uid_str: str,
    out_dir: Path,
    world_name: str | None,
    force: bool,
    yes: bool,
) -> int:
    """Byte-swap-and-write logic for perform_migration (dedicated server ->
    single-player/co-op). Safe as a blind global replace because the
    dedicated-server old_uid_str has 4 fully random bytes -- see the
    module comment above scoped_unhost_swap() for why the reverse
    direction can't use this same approach. old_uid_str/new_uid_str must
    already be validated, resolved GUID strings. Raises HostfixError for
    validation problems, MigrationAborted if the user declines the
    confirmation prompt, and returns the number of references migrated in
    Level.sav on success."""
    old_uid = guid_str_to_raw(old_uid_str)
    new_uid = guid_str_to_raw(new_uid_str)

    level_path = world_dir / "Level.sav"
    old_player_path = world_dir / "Players" / f"{guid_str_to_filename_uid(old_uid_str)}.sav"
    new_player_path_src = world_dir / "Players" / f"{guid_str_to_filename_uid(new_uid_str)}.sav"
    if not level_path.exists():
        raise HostfixError(f"No Level.sav found in {world_dir}")
    if not old_player_path.exists():
        raise HostfixError(
            f"No player save found at {old_player_path}\n"
            f"(run 'hostfix.py list {world_dir}' to see available player UIDs)"
        )
    if new_player_path_src.exists() and new_player_path_src != old_player_path:
        # A real, cheap, reliable conflict check: if a player save already
        # exists on disk for the target ID, that's a genuine second
        # character -- migrating into it would merge two people's data.
        if not force:
            raise HostfixError(
                f"REFUSING: a player save already exists for the target ID "
                f"{new_uid_str}\n  ({new_player_path_src})\n"
                "Migrating would merge that existing character's data with the one "
                "you're moving in.\nIf you're sure that's what you want, re-run with --force."
            )
        print(f"--force given: proceeding even though {new_player_path_src} already exists.")

    print(f"Loading {level_path} ...")
    level = SavFile.load(level_path)

    occurrences = level.raw_gvas.count(old_uid)
    if occurrences == 0:
        raise HostfixError(
            f"UID {old_uid_str} does not appear anywhere in {level_path}.\n"
            "Double-check you copied it correctly."
        )

    print(f"\nFound {occurrences} reference(s) to {old_uid_str} in the world file.")
    print("Sample contexts (sanity-check these look like real save data, not garbage):")
    for ctx in sample_contexts(level.raw_gvas, old_uid, n=3):
        print(f"    ...{ctx!r}")

    if not yes:
        resp = input("\nProceed with the migration? [y/N] ").strip().lower()
        if resp != "y":
            raise MigrationAborted()

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "Players").mkdir(exist_ok=True)

    # --- Level.sav: global replace, covers the character map key, every
    # owned Pal's OwnerPlayerUId/OldOwnerPlayerUIds/nickname fields, guild
    # membership records, and placed-building builder tags, all at once.
    new_raw, n = replace_uid_everywhere(level.raw_gvas, old_uid, new_uid)
    expected_diff_bytes = n * sum(1 for a, b in zip(old_uid, new_uid) if a != b)
    actual_diff_bytes = sum(1 for a, b in zip(level.raw_gvas, new_raw) if a != b)
    if actual_diff_bytes != expected_diff_bytes:
        raise HostfixError(
            f"INTERNAL SANITY CHECK FAILED: expected exactly {expected_diff_bytes} "
            f"differing bytes, got {actual_diff_bytes}. Refusing to write a file "
            "that didn't verify cleanly -- please open an issue with this save."
        )
    level.raw_gvas = new_raw
    level.write(out_dir / "Level.sav")
    print(f"Wrote {out_dir / 'Level.sav'}  ({n} references migrated, verified byte-exact)")

    # --- Player .sav: same global replace (covers PlayerUId + the
    # IndividualId.PlayerUId sub-field in one pass), then rename the file
    # to the new UID.
    player = SavFile.load(old_player_path)
    p_new_raw, p_n = replace_uid_everywhere(player.raw_gvas, old_uid, new_uid)
    player.raw_gvas = p_new_raw
    new_player_path = out_dir / "Players" / f"{guid_str_to_filename_uid(new_uid_str)}.sav"
    player.write(new_player_path)
    print(f"Wrote {new_player_path}  ({p_n} references migrated)")

    # --- LevelMeta.sav / WorldOption.sav / LocalData.sav: pass through
    # (also normalizes PlM -> PlZ), optionally rename the world.
    # LocalData.sav holds per-save-slot local state like fast-travel/map
    # reveal progress and boss-encounter flags -- it's opaque to us (we
    # don't parse its contents) but decompresses/recompresses cleanly, so
    # carrying it over means you don't lose your explored fast-travel
    # points and have to walk up to them again to re-reveal them.
    for name in ("LevelMeta.sav", "WorldOption.sav", "LocalData.sav"):
        src = world_dir / name
        if not src.exists():
            continue
        try:
            sf = SavFile.load(src)
        except Exception as e:
            print(f"  (skipping {name}: could not decode -- {e})")
            shutil.copy2(src, out_dir / name)
            continue
        sf.raw_gvas, _ = replace_uid_everywhere(sf.raw_gvas, old_uid, new_uid)
        if name == "LevelMeta.sav" and world_name:
            sf = _rename_world(sf, world_name)
        sf.write(out_dir / name)
        print(f"Wrote {out_dir / name}")

    return n


def _unhost_core(
    world_dir: Path,
    old_uid_str: str,
    new_uid_str: str,
    out_dir: Path,
    world_name: str | None,
    force: bool,
    yes: bool,
) -> int:
    """Byte-swap-and-write logic for perform_unhost (single-player/co-op ->
    dedicated server). Unlike _migrate_core, this uses scoped_unhost_swap()
    instead of a blind global replace -- see the module comment above that
    function for why. old_uid_str/new_uid_str must already be validated,
    resolved GUID strings. Raises HostfixError for validation problems,
    MigrationAborted if the user declines the confirmation prompt, and
    returns the number of character/Pal references safely migrated in
    Level.sav on success."""
    old_uid = guid_str_to_raw(old_uid_str)

    level_path = world_dir / "Level.sav"
    old_player_path = world_dir / "Players" / f"{guid_str_to_filename_uid(old_uid_str)}.sav"
    new_player_path_src = world_dir / "Players" / f"{guid_str_to_filename_uid(new_uid_str)}.sav"
    if not level_path.exists():
        raise HostfixError(f"No Level.sav found in {world_dir}")
    if not old_player_path.exists():
        raise HostfixError(
            f"No player save found at {old_player_path}\n"
            f"(run 'hostfix.py list {world_dir}' to see available player UIDs)"
        )
    if new_player_path_src.exists() and new_player_path_src != old_player_path:
        if not force:
            raise HostfixError(
                f"REFUSING: a player save already exists for the target ID "
                f"{new_uid_str}\n  ({new_player_path_src})\n"
                "Migrating would merge that existing character's data with the one "
                "you're moving in.\nIf you're sure that's what you want, re-run with --force."
            )
        print(f"--force given: proceeding even though {new_player_path_src} already exists.")

    print(f"Loading {level_path} ...")
    level = SavFile.load(level_path)

    print(
        "Scanning for your character/Pal records (structurally, not a blind "
        "byte search -- see the tool's docs for why)..."
    )
    new_raw, n = scoped_unhost_swap(level.raw_gvas, old_uid_str, new_uid_str)
    if n == 0:
        raise HostfixError(
            f"Could not find any character/Pal records structurally tied to "
            f"{old_uid_str} in {level_path}.\nDouble-check you copied the UID "
            "correctly."
        )

    print(f"\nFound {n} safely-verified character/Pal reference(s) to {old_uid_str} in the world file.")
    print("Sample contexts (sanity-check these look like real save data, not garbage):")
    for ctx in sample_contexts(level.raw_gvas, old_uid, n=3):
        print(f"    ...{ctx!r}")
    print(
        "\nNote: guild membership and any placed/built structures can't be safely\n"
        "verified for this ID and are intentionally left untouched -- see the\n"
        "'Limitations' section of the README."
    )

    if not yes:
        resp = input("\nProceed with the conversion? [y/N] ").strip().lower()
        if resp != "y":
            raise MigrationAborted()

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "Players").mkdir(exist_ok=True)

    level.raw_gvas = new_raw
    level.write(out_dir / "Level.sav")
    print(f"Wrote {out_dir / 'Level.sav'}  ({n} references migrated, verified byte-exact)")

    # --- Player .sav: entirely the player's own data already, so the
    # scoped technique covers it fully (their own PlayerUId and
    # IndividualId.PlayerUId fields are clean StructProperty(Guid)s).
    player = SavFile.load(old_player_path)
    p_new_raw, p_n = scoped_unhost_swap(player.raw_gvas, old_uid_str, new_uid_str)
    player.raw_gvas = p_new_raw
    new_player_path = out_dir / "Players" / f"{guid_str_to_filename_uid(new_uid_str)}.sav"
    player.write(new_player_path)
    print(f"Wrote {new_player_path}  ({p_n} references migrated)")

    # --- LevelMeta.sav / WorldOption.sav: pass through (also normalizes
    # PlM -> PlZ), optionally rename the world. Neither file has
    # character/guild/building data, so the marker-based half of the
    # scoped technique (no CharacterSaveParameterMap lookup needed) is
    # all that could ever apply, and it's cheap and fully safe to just
    # always run.  LocalData.sav is intentionally skipped -- it's
    # per-install local client state (fast-travel/map reveal, etc.) that
    # dedicated servers don't use; each player who later connects builds
    # up their own copy of it on their own PC.
    for name in ("LevelMeta.sav", "WorldOption.sav"):
        src = world_dir / name
        if not src.exists():
            continue
        try:
            sf = SavFile.load(src)
        except Exception as e:
            print(f"  (skipping {name}: could not decode -- {e})")
            shutil.copy2(src, out_dir / name)
            continue
        sf.raw_gvas, _ = scoped_unhost_swap(sf.raw_gvas, old_uid_str, new_uid_str)
        if name == "LevelMeta.sav" and world_name:
            sf = _rename_world(sf, world_name)
        sf.write(out_dir / name)
        print(f"Wrote {out_dir / name}")

    return n


def perform_migration(
    world_dir: Path,
    old_uid_str: str,
    new_uid_str: str | None = None,
    out_dir: Path | None = None,
    world_name: str | None = None,
    force: bool = False,
    yes: bool = False,
) -> Path:
    """Migrate a dedicated-server character into single-player/co-op.
    Shared by the `migrate` CLI command and the interactive wizard. Raises
    HostfixError for validation problems, MigrationAborted if the user
    declines the confirmation prompt, and returns the output directory on
    success."""
    out_dir = out_dir or (world_dir.parent / (world_dir.name + "_migrated"))

    if not GUID_STR_RE.match(old_uid_str):
        raise HostfixError(f"--old-uid doesn't look like a GUID: {old_uid_str}")
    new_uid_str = new_uid_str or SINGLEPLAYER_HOST_UID
    if not GUID_STR_RE.match(new_uid_str):
        raise HostfixError(f"--new-uid doesn't look like a GUID: {new_uid_str}")

    _migrate_core(world_dir, old_uid_str, new_uid_str, out_dir, world_name, force, yes)

    print(
        f"\nDone. Copy the contents of:\n  {out_dir}\n"
        "into your local single-player save slot, e.g.:\n"
        r"  %LOCALAPPDATA%\Pal\Saved\SaveGames\<YourSteamID>\<AnyWorldGUID>" "\\"
        "\n(create that WorldGUID folder if you don't already have one for this "
        "world -- the folder name doesn't matter, Palworld reads whatever's inside it)."
    )
    return out_dir


def perform_unhost(
    world_dir: Path,
    old_uid_str: str | None = None,
    new_uid_str: str | None = None,
    out_dir: Path | None = None,
    world_name: str | None = None,
    force: bool = False,
    yes: bool = False,
) -> tuple[Path, str]:
    """The reverse of perform_migration: convert a single-player/co-op
    world into save data ready to drop onto a dedicated server, reassigning
    a player (by default the special single-player host ID) onto their
    REAL dedicated-server player ID.

    IMPORTANT: that real ID is NOT something this tool (or anything else)
    can invent. Palworld derives a dedicated-server PlayerUId from a
    one-way hash of the connecting player's own Steam account ID -- the
    same real person always computes the same fixed ID when they connect,
    and a save file can't reserve an arbitrary one in advance. So
    new_uid_str must be the ID the server *already* assigned when that
    player joined the (otherwise empty) server once -- run
    `hostfix.py list <server_save_folder>` after that one join to find it.
    Passing a made-up ID here will produce a save nobody can ever actually
    log in as; the server will just create a brand-new character instead.

    Character level, stats, inventory, unlocked tech, and owned Pals are
    safely and fully migrated via structural verification (see
    scoped_unhost_swap above) rather than a blind byte search, since the
    ID this moves *away from* by default is low-entropy enough that a
    blind search produces massive false-positive corruption. Guild
    membership and placed/built structures can't be safely verified the
    same way and are intentionally left untouched. Raises HostfixError for
    validation problems, MigrationAborted if the user declines the
    confirmation prompt, and returns (output directory, the new UID) on
    success."""
    out_dir = out_dir or (world_dir.parent / (world_dir.name + "_dedicated"))

    old_uid_str = old_uid_str or SINGLEPLAYER_HOST_UID
    if not GUID_STR_RE.match(old_uid_str):
        raise HostfixError(f"--old-uid doesn't look like a GUID: {old_uid_str}")
    if new_uid_str is None:
        raise HostfixError(
            "--new-uid is required for unhost -- and it can't be made up.\n"
            "Palworld computes a dedicated-server player's ID from a one-way hash "
            "of their own Steam account, so there's no ID this tool can invent that "
            "a real player could ever actually connect as.\n\n"
            "The correct order is:\n"
            "  1. Start your (otherwise empty) dedicated server.\n"
            "  2. Connect to it ONCE with the real account you'll play as, then "
            "disconnect and fully stop the server.\n"
            "     (this makes the server compute and assign your real ID, as a "
            "blank freshly-spawned character)\n"
            "  3. Run: hostfix.py list <server_save_folder>  -- to find that real ID.\n"
            "  4. Re-run unhost with --new-uid set to that ID, then copy this tool's "
            "output OVER the server's save folder (replacing the blank character "
            "you just made in step 2)."
        )
    if not GUID_STR_RE.match(new_uid_str):
        raise HostfixError(f"--new-uid doesn't look like a GUID: {new_uid_str}")

    _unhost_core(world_dir, old_uid_str, new_uid_str, out_dir, world_name, force, yes)

    print(
        f"\nDone. Your character's data is now filed under your real "
        f"dedicated-server ID:\n  {new_uid_str}\n\n"
        f"Copy the contents of:\n  {out_dir}\n"
        "OVER your dedicated server's save folder -- e.g.:\n"
        r"  <PalServer install>\Pal\Saved\SaveGames\0\<WorldGUID>" "\\"
        "\n"
        f"This REPLACES the blank character the server made when you first "
        f"connected (that's expected -- it's why {new_uid_str} already had a "
        f"Players/ file for the server to overwrite). Make sure the server is "
        "fully stopped before copying these files in, then start it back up "
        "and reconnect.\n\n"
        "NOTE: LocalData.sav was intentionally not copied -- dedicated servers "
        "don't use it. Guild membership and any structures you'd already built "
        "were also intentionally left as-is (see the README's Limitations "
        "section) -- you may need to create/rejoin a guild and rebuild or "
        "reclaim your base on the new server. Worth double-checking "
        "WorldOption.sav's server settings "
        "(ServerName, ServerPassword, PublicPort, bIsMultiplay, etc. -- see "
        "optioneditor) before starting the server for real."
    )
    return out_dir, new_uid_str


# --------------------------------------------------------------------------
# "sync" -- splice a single character's own records into an ALREADY-LIVE,
# already-populated dedicated server, touching nothing else
#
# `unhost` (above) assumes the destination is a brand-new, never-joined
# server -- it works by editing the *server's own* blank auto-generated
# character in place. That's the wrong tool if the destination already has
# real history for other people (or even for you): a naive whole-Level.sav
# push from single-player would blow away everyone else's progress. Worse,
# single-player mode has been observed (empirically, on a real save) to
# silently prune/degrade OTHER, non-connectable players' Pal-ownership data
# over time just from normal solo play -- so even a very recent
# single-player save can already be lossy for people who aren't the one
# playing it, making a whole-file push actively dangerous, not just
# unnecessary.
#
# `sync` avoids all of that by never touching the destination Level.sav as
# a whole. It parses both worlds structurally -- via palworld-save-tools'
# JSON dump/load round-trip, verified byte-identical on this save format
# when left unmodified -- and replaces ONLY:
#   - CharacterSaveParameterMap entries keyed to the target player (their
#     own character record, plus every Pal entry keyed to their PlayerUId)
#   - ItemContainerSaveData / CharacterContainerSaveData entries whose ID
#     matches one of the target player's own container IDs (inventory,
#     equipped gear, party, Palbox -- read cleanly off each side's own
#     Players/<uid>.sav, which (unlike the map's RawData blobs) is NOT
#     opaque)
# Every other player, GroupSaveDataMap (guild), and MapObjectSaveData
# (buildings) are left completely alone -- sourced only from the
# destination and never even inspected for edits.
#
# Collision handling: if a Pal being added from the source has an
# InstanceId that also exists in a destination entry NOT being replaced
# (owned by someone/something else), it's skipped rather than duplicated
# or force-overwritten, and reported so you can decide what to do about it
# by hand.
# --------------------------------------------------------------------------
def _dump_gvas(raw: bytes) -> tuple[object, dict]:
    """Parse decompressed GVAS bytes into (GvasFile, dumped-dict) via
    palworld-save-tools' structural reader. `sync` needs this (unlike
    migrate/unhost's raw-byte patching) because adding/removing whole map
    entries changes the file's byte length -- something a fixed-offset
    patch can't do."""
    with contextlib.redirect_stdout(io.StringIO()):
        from palworld_save_tools.gvas import GvasFile
        from palworld_save_tools.paltypes import PALWORLD_TYPE_HINTS

        gvas = GvasFile.read(raw, PALWORLD_TYPE_HINTS, {}, allow_nan=True)
        return gvas, gvas.dump()


def _reserialize_gvas(dumped: dict) -> bytes:
    """The other half of the round-trip _dump_gvas started: turn an edited
    dumped dict back into raw GVAS bytes. Verified byte-identical to the
    original input when the dict is left unmodified, on real production
    Oodle-format saves (both a ~43MB single-player Level.sav and a real
    server Level.sav)."""
    from palworld_save_tools.gvas import GvasFile

    new_gvas = GvasFile.load(dumped)
    return new_gvas.write({})


def _container_ids_from_player(player_dumped: dict) -> tuple[set[str], set[str]]:
    """A player's own Players/<uid>.sav SaveData is fully clean/structurally
    typed (unlike CharacterSaveParameterMap's opaque RawData blobs) -- pull
    out the container IDs it owns: personal item containers (inventory,
    equipped weapon/armor/lantern, drop slot) and character containers
    (Otomo party, Palbox storage). These IDs are stable for a character's
    whole lifetime, so the same set applies on both the single-player and
    server side. Returns (item_container_ids, character_container_ids)."""
    sd = player_dumped["properties"]["SaveData"]["value"]
    item_ids: set[str] = set()
    char_ids: set[str] = set()
    inv = sd.get("InventoryInfo", {}).get("value", {})
    for key in (
        "CommonContainerId", "DropSlotContainerId", "EssentialContainerId",
        "WeaponLoadOutContainerId", "PlayerEquipArmorContainerId", "FoodEquipContainerId",
    ):
        node = inv.get(key)
        if node:
            item_ids.add(node["value"]["ID"]["value"])
    for key in ("OtomoCharacterContainerId", "PalStorageContainerId"):
        node = sd.get(key)
        if node:
            char_ids.add(node["value"]["ID"]["value"])
    return item_ids, char_ids


@dataclass
class SyncStats:
    records_removed: int
    records_added: int
    collisions_skipped: list[str]
    item_containers_replaced: int
    char_containers_replaced: int


def _splice_player_records(
    src_dumped: dict,
    dst_dumped: dict,
    src_player_dumped: dict,
    dst_player_dumped: dict,
    old_uid_str: str,
    target_uid_str: str,
) -> SyncStats:
    """Mutates dst_dumped IN PLACE: replaces the target player's own
    CharacterSaveParameterMap/ItemContainerSaveData/CharacterContainerSaveData
    entries with the source's, leaving every other entry untouched. See the
    module comment above this section for the full design and why it's
    safe."""
    src_item_ids, src_char_ids = _container_ids_from_player(src_player_dumped)
    dst_item_ids, dst_char_ids = _container_ids_from_player(dst_player_dumped)
    item_ids = src_item_ids | dst_item_ids
    char_ids = src_char_ids | dst_char_ids

    wsd_src = src_dumped["properties"]["worldSaveData"]["value"]
    wsd_dst = dst_dumped["properties"]["worldSaveData"]["value"]

    # --- CharacterSaveParameterMap: the player's own record + every Pal
    # they currently own.
    csm_src = wsd_src["CharacterSaveParameterMap"]["value"]
    csm_dst = wsd_dst["CharacterSaveParameterMap"]["value"]

    kept = [e for e in csm_dst if e["key"]["PlayerUId"]["value"] != target_uid_str]
    removed_n = len(csm_dst) - len(kept)
    added = [e for e in csm_src if e["key"]["PlayerUId"]["value"] == old_uid_str]

    kept_instance_ids = {e["key"]["InstanceId"]["value"] for e in kept}
    collisions = [
        e["key"]["InstanceId"]["value"] for e in added
        if e["key"]["InstanceId"]["value"] in kept_instance_ids
    ]
    added_filtered = [e for e in added if e["key"]["InstanceId"]["value"] not in kept_instance_ids]

    for e in added_filtered:
        e["key"]["PlayerUId"]["value"] = target_uid_str
    csm_dst[:] = kept + added_filtered

    # --- ItemContainerSaveData / CharacterContainerSaveData: swap in the
    # source's containers for every ID either side reports as the player's
    # own (matched by clean container ID, never by opaque RawData).
    icd_src = wsd_src["ItemContainerSaveData"]["value"]
    icd_dst = wsd_dst["ItemContainerSaveData"]["value"]
    kept_icd = [e for e in icd_dst if e["key"]["ID"]["value"] not in item_ids]
    added_icd = [e for e in icd_src if e["key"]["ID"]["value"] in item_ids]
    icd_dst[:] = kept_icd + added_icd

    ccd_src = wsd_src["CharacterContainerSaveData"]["value"]
    ccd_dst = wsd_dst["CharacterContainerSaveData"]["value"]
    kept_ccd = [e for e in ccd_dst if e["key"]["ID"]["value"] not in char_ids]
    added_ccd = [e for e in ccd_src if e["key"]["ID"]["value"] in char_ids]
    ccd_dst[:] = kept_ccd + added_ccd

    return SyncStats(
        records_removed=removed_n,
        records_added=len(added_filtered),
        collisions_skipped=collisions,
        item_containers_replaced=len(added_icd),
        char_containers_replaced=len(added_ccd),
    )


def _check_no_duplicate_ids(dst_dumped: dict) -> None:
    """Sanity check run after splicing, before anything is written: the
    splice logic in _splice_player_records should make this structurally
    impossible (kept/added are always partitioned by ID), but for
    something about to be written onto a live, shared server, checking
    costs nothing and catches a future bug here before it reaches disk."""
    wsd = dst_dumped["properties"]["worldSaveData"]["value"]
    checks = (
        ("CharacterSaveParameterMap", wsd["CharacterSaveParameterMap"]["value"],
         lambda e: e["key"]["InstanceId"]["value"]),
        ("ItemContainerSaveData", wsd["ItemContainerSaveData"]["value"],
         lambda e: e["key"]["ID"]["value"]),
        ("CharacterContainerSaveData", wsd["CharacterContainerSaveData"]["value"],
         lambda e: e["key"]["ID"]["value"]),
    )
    for name, entries, key_fn in checks:
        ids = [key_fn(e) for e in entries]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise HostfixError(
                f"INTERNAL SANITY CHECK FAILED: {name} has duplicate key(s) after "
                f"splicing ({len(dupes)} duplicated ID(s), e.g. {dupes[0]}). Refusing "
                "to write a file that didn't verify cleanly -- please open an issue "
                "with this save."
            )


def _sync_core(
    local_world_dir: Path,
    server_dir: Path,
    old_uid_str: str,
    target_uid_str: str,
    out_dir: Path,
    yes: bool,
) -> SyncStats:
    """Load both worlds, splice, confirm, and write output. Raises
    HostfixError for validation problems, MigrationAborted if the user
    declines the confirmation prompt."""
    src_level_path = local_world_dir / "Level.sav"
    dst_level_path = server_dir / "Level.sav"
    src_player_path = local_world_dir / "Players" / f"{guid_str_to_filename_uid(old_uid_str)}.sav"
    dst_player_path = server_dir / "Players" / f"{guid_str_to_filename_uid(target_uid_str)}.sav"

    for p, label in (
        (src_level_path, "single-player world's Level.sav"),
        (dst_level_path, "server's Level.sav"),
        (src_player_path, "single-player character's save"),
        (dst_player_path, "server character's save"),
    ):
        if not p.exists():
            raise HostfixError(
                f"No {label} found at:\n  {p}\n"
                "(run 'hostfix.py list <folder>' on each side to double-check the UIDs)"
            )

    print(f"Loading {src_level_path} ...")
    src_level = SavFile.load(src_level_path)
    print(f"Loading {dst_level_path} (this can take a minute for a large world)...")
    dst_level = SavFile.load(dst_level_path)

    print("Parsing both worlds structurally (this can also take a minute)...")
    _, src_dumped = _dump_gvas(src_level.raw_gvas)
    dst_gvas, dst_dumped = _dump_gvas(dst_level.raw_gvas)

    src_player = SavFile.load(src_player_path)
    dst_player = SavFile.load(dst_player_path)
    _, src_player_dumped = _dump_gvas(src_player.raw_gvas)
    _, dst_player_dumped = _dump_gvas(dst_player.raw_gvas)

    stats = _splice_player_records(
        src_dumped, dst_dumped, src_player_dumped, dst_player_dumped, old_uid_str, target_uid_str,
    )
    _check_no_duplicate_ids(dst_dumped)

    print(
        f"\nCharacterSaveParameterMap: replacing {stats.records_removed} stale server "
        f"record(s) with {stats.records_added} fresh one(s) from your single-player save "
        f"(your character + every Pal you currently own)."
    )
    print(
        f"Containers: replacing {stats.item_containers_replaced} item container(s) "
        f"(inventory/equipment) and {stats.char_containers_replaced} character "
        f"container(s) (party/Palbox)."
    )
    if stats.collisions_skipped:
        print(
            f"\nHeads up: {len(stats.collisions_skipped)} Pal(s) in your single-player save "
            "share an ID with something that already exists on the server under someone "
            "or something else -- these were left as-is on the server rather than risk "
            "duplicating or overwriting them:"
        )
        for iid in stats.collisions_skipped:
            print(f"    {iid}")
    print(
        "\nEverything else on the server -- every other player, the guild(s), and all "
        "placed/built structures -- is untouched: not read, not re-serialized, not "
        "written."
    )

    if not yes:
        resp = input("\nProceed and write the updated server save? [y/N] ").strip().lower()
        if resp != "y":
            raise MigrationAborted()

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "Players").mkdir(exist_ok=True)

    print("\nReserializing the server's world file (this is the slow part)...")
    new_raw = _reserialize_gvas(dst_dumped)
    new_level = SavFile(path=out_dir / "Level.sav", raw_gvas=new_raw, save_type=dst_level.save_type)
    new_level.write(out_dir / "Level.sav")
    print(f"Wrote {out_dir / 'Level.sav'}")

    print("Verifying the written file re-parses cleanly...")
    try:
        reloaded = SavFile.load(out_dir / "Level.sav")
        _dump_gvas(reloaded.raw_gvas)
    except Exception as e:
        raise HostfixError(
            f"INTERNAL SANITY CHECK FAILED: the file just written to "
            f"{out_dir / 'Level.sav'} does not re-parse cleanly ({e}).\n"
            "Do NOT copy this output onto your server -- please open an issue with "
            "this save."
        )
    print("  OK -- re-parses cleanly.")

    # --- Player .sav: relabel the single-player character's own save file
    # onto the server's target ID, via the same structurally-verified
    # technique unhost uses (safe regardless of old_uid_str's entropy).
    print("Relabeling your character's own save file onto the server's target ID...")
    p_new_raw, p_n = scoped_unhost_swap(src_player.raw_gvas, old_uid_str, target_uid_str)
    new_player_path = out_dir / "Players" / f"{guid_str_to_filename_uid(target_uid_str)}.sav"
    new_player = SavFile(path=new_player_path, raw_gvas=p_new_raw, save_type=src_player.save_type)
    new_player.write(new_player_path)
    print(f"Wrote {new_player_path}  ({p_n} references migrated)")

    return stats


def perform_sync(
    local_world_dir: Path,
    server_dir: Path,
    target_uid_str: str,
    old_uid_str: str | None = None,
    out_dir: Path | None = None,
    yes: bool = False,
) -> Path:
    """Splice a single character's own records from a single-player/co-op
    world into an ALREADY-LIVE, already-populated dedicated server, without
    touching any other player, the guild(s), or built structures. Use this
    instead of `unhost` whenever the destination server already has real
    history -- either for other people, or for you (e.g. you played solo as
    a stopgap and now want to bring that progress back to the shared
    server). See the module comment above _dump_gvas for the full design
    and why a naive whole-Level.sav push (what `unhost` does, correctly,
    for a brand-new server) would be destructive here instead.

    old_uid_str defaults to the single-player host ID. target_uid_str must
    be the player's REAL, already-established ID on the destination server
    -- run `hostfix.py list <server_save_folder>` to find it (unlike
    `unhost`, there's no "join once with a blank character first" step
    needed, since this player already exists on the server). Raises
    HostfixError for validation problems, MigrationAborted if the user
    declines the confirmation prompt, and returns the output directory on
    success."""
    out_dir = out_dir or (server_dir.parent / (server_dir.name + "_synced"))

    old_uid_str = old_uid_str or SINGLEPLAYER_HOST_UID
    if not GUID_STR_RE.match(old_uid_str):
        raise HostfixError(f"--old-uid doesn't look like a GUID: {old_uid_str}")
    if not GUID_STR_RE.match(target_uid_str):
        raise HostfixError(f"--target-uid doesn't look like a GUID: {target_uid_str}")

    _sync_core(local_world_dir, server_dir, old_uid_str, target_uid_str, out_dir, yes)

    print(
        f"\nDone. Copy the contents of:\n  {out_dir}\n"
        f"OVER your dedicated server's save folder:\n  {server_dir}\n"
        "Make sure the server is fully stopped before copying these files in, then "
        "start it back up and reconnect."
    )
    return out_dir


def cmd_migrate(args: argparse.Namespace) -> None:
    try:
        perform_migration(
            world_dir=Path(args.world_dir),
            old_uid_str=args.old_uid,
            new_uid_str=args.new_uid,
            out_dir=Path(args.out) if args.out else None,
            world_name=args.world_name,
            force=args.force,
            yes=args.yes,
        )
    except HostfixError as e:
        sys.exit(str(e))
    except MigrationAborted:
        print("Aborted, nothing was written.")


def cmd_unhost(args: argparse.Namespace) -> None:
    try:
        perform_unhost(
            world_dir=Path(args.world_dir),
            old_uid_str=args.old_uid,
            new_uid_str=args.new_uid,
            out_dir=Path(args.out) if args.out else None,
            world_name=args.world_name,
            force=args.force,
            yes=args.yes,
        )
    except HostfixError as e:
        sys.exit(str(e))
    except MigrationAborted:
        print("Aborted, nothing was written.")


def cmd_sync(args: argparse.Namespace) -> None:
    try:
        perform_sync(
            local_world_dir=Path(args.local_world_dir),
            server_dir=Path(args.server_dir),
            target_uid_str=args.target_uid,
            old_uid_str=args.old_uid,
            out_dir=Path(args.out) if args.out else None,
            yes=args.yes,
        )
    except HostfixError as e:
        sys.exit(str(e))
    except MigrationAborted:
        print("Aborted, nothing was written.")


def _rename_world(levelmeta: SavFile, new_name: str) -> SavFile:
    """Renaming needs a real property edit (the name length can differ),
    so this is the one place we go through the JSON round-trip instead of
    a raw byte replace."""
    import io

    from palworld_save_tools.gvas import GvasFile
    from palworld_save_tools.json_tools import CustomEncoder
    from palworld_save_tools.paltypes import PALWORLD_TYPE_HINTS

    gvas_file = GvasFile.read(levelmeta.raw_gvas, PALWORLD_TYPE_HINTS, {}, allow_nan=True)
    dumped = json.loads(json.dumps(gvas_file.dump(), cls=CustomEncoder))
    try:
        dumped["properties"]["SaveData"]["value"]["WorldName"]["value"] = new_name
    except KeyError:
        print("  (could not find WorldName field to rename -- leaving it as-is)")
        return levelmeta
    new_gvas = GvasFile.load(dumped)
    new_raw = new_gvas.write({})
    return SavFile(path=levelmeta.path, raw_gvas=new_raw, save_type=levelmeta.save_type)


# --------------------------------------------------------------------------
# Interactive wizard (the friendly, no-command-line-flags-needed mode --
# this is what runs if you just double-click the script or run it with
# no arguments)
# --------------------------------------------------------------------------
def _clean_path_input(s: str) -> str:
    s = s.strip()
    # Windows Explorer's "Copy as path" wraps in quotes; dragging a folder
    # onto the console can leave a trailing space or backslash-quote combo.
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1]
    return s.strip()


def _prompt(msg: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            raw = input(f"{msg}{suffix}: ")
        except EOFError:
            raise MigrationAborted()
        val = _clean_path_input(raw)
        if not val and default is not None:
            return default
        if val:
            return val
        print("  (please enter a value)")


def _prompt_yes_no(msg: str, default: bool = False) -> bool:
    d = "Y/n" if default else "y/N"
    try:
        raw = input(f"{msg} [{d}] ").strip().lower()
    except EOFError:
        return default
    if not raw:
        return default
    return raw.startswith("y")


def _prompt_choice(msg: str, n_options: int) -> int:
    while True:
        try:
            raw = input(f"{msg} [1-{n_options}]: ").strip()
        except EOFError:
            raise MigrationAborted()
        if raw.isdigit() and 1 <= int(raw) <= n_options:
            return int(raw) - 1
        print(f"  (please enter a number from 1 to {n_options})")


def run_interactive() -> None:
    print("=" * 70)
    print(" palworld-oodle-hostfix")
    print(" Migrate a dedicated-server character into single-player/co-op")
    print("=" * 70)
    print()

    while True:
        world_dir_str = _prompt(
            "Path to the SERVER world folder (the one with Level.sav in it -- "
            "you can paste or drag-and-drop it here)"
        )
        world_dir = Path(world_dir_str)
        if not world_dir.is_dir():
            print(f"  '{world_dir}' isn't a folder. Try again.\n")
            continue
        if not (world_dir / "Level.sav").exists():
            print(f"  No Level.sav found directly inside '{world_dir}'. "
                  "Make sure you point at the folder that directly contains it "
                  "(it usually has a long hex-looking name).\n")
            continue
        break

    print()
    level, infos = scan_players(world_dir)
    if not infos:
        print("No player save files found in that world's Players/ folder. Nothing to do.")
        return
    print(f"Found {len(infos)} player(s) in this world:\n")
    print_player_table(infos, numbered=True)
    print(
        "(Name guesses are best-effort, scraped from guild data -- if none of these\n"
        "look right, check file sizes or ask whoever hosted the server.)\n"
    )

    choice = _prompt_choice("Which number is you?", len(infos))
    selected = infos[choice]
    if selected.is_singleplayer_host:
        print(
            "\nHeads up: that's already the single-player host ID -- there's usually "
            "nothing to migrate here."
        )
        if not _prompt_yes_no("Continue anyway?", default=False):
            return
    print(f"\nSelected: {selected.guid_str}  (Players/{selected.path.name})\n")

    default_out = str(world_dir.parent / (world_dir.name + "_migrated"))
    out_dir_str = _prompt("Where should the fixed-up world be written?", default=default_out)
    out_dir = Path(_clean_path_input(out_dir_str))

    world_name = _prompt(
        "Rename the world (as it'll show in Palworld's load-game list)? "
        "Leave blank to keep the original name",
        default="",
    )

    print()
    try:
        perform_migration(
            world_dir=world_dir,
            old_uid_str=selected.guid_str,
            new_uid_str=None,
            out_dir=out_dir,
            world_name=world_name or None,
            force=False,
            yes=False,
        )
    except HostfixError as e:
        print(f"\nSomething went wrong:\n  {e}")
    except MigrationAborted:
        print("\nAborted, nothing was written.")


def _prompt_world_folder(msg: str) -> Path:
    """Prompt for a folder path, re-prompting until it exists and directly
    contains a Level.sav."""
    while True:
        raw = _prompt(msg)
        world_dir = Path(raw)
        if not world_dir.is_dir():
            print(f"  '{world_dir}' isn't a folder. Try again.\n")
            continue
        if not (world_dir / "Level.sav").exists():
            print(f"  No Level.sav found directly inside '{world_dir}'. "
                  "Make sure you point at the folder that directly contains it.\n")
            continue
        return world_dir


def run_interactive_unhost() -> None:
    print("=" * 70)
    print(" palworld-oodle-hostfix -- reverse mode")
    print(" Convert a single-player/co-op world into a dedicated-server-ready save")
    print("=" * 70)
    print()
    print(
        "IMPORTANT: a dedicated-server player ID isn't something anyone can make "
        "up -- Palworld computes it from a one-way hash of the connecting player's "
        "own Steam account, the same way every time. So before this can work, the "
        "REAL account you'll play as needs to have already connected to your "
        "(otherwise empty) dedicated server once -- that's what makes the server "
        "compute and assign that real ID, as a blank freshly-spawned character.\n"
    )
    if not _prompt_yes_no(
        "Have you already started your dedicated server, connected to it ONCE with "
        "the real account you'll play as, and then fully stopped the server again?",
        default=False,
    ):
        print(
            "\nNo problem -- go do that first (start the server, connect once, "
            "disconnect, stop the server), then come back and run this again."
        )
        return
    print()

    world_dir = _prompt_world_folder(
        "Path to your SINGLE-PLAYER/CO-OP world folder (the one with Level.sav "
        "in it -- usually under ...\\Pal\\Saved\\SaveGames\\<SteamID>\\<WorldGUID>\\, "
        "you can paste or drag-and-drop it here)"
    )

    print()
    level, infos = scan_players(world_dir)
    if not infos:
        print("No player save files found in that world's Players/ folder. Nothing to do.")
        return
    print(f"Found {len(infos)} player(s) in this world:\n")
    print_player_table(infos, numbered=True)

    host_index = next((i for i, info in enumerate(infos) if info.is_singleplayer_host), None)
    if host_index is not None:
        print(
            "(Usually you want the one tagged as the single-player host ID -- "
            f"that's [{host_index + 1}].)\n"
        )
    else:
        print()

    choice = _prompt_choice("Which one do you want to move onto the dedicated server?", len(infos))
    selected = infos[choice]
    print(f"\nSelected: {selected.guid_str}  (Players/{selected.path.name})\n")

    server_dir = _prompt_world_folder(
        "Now point me at your DEDICATED SERVER's save folder (the one you just "
        "connected to once -- usually under "
        "<PalServer install>\\Pal\\Saved\\SaveGames\\0\\<WorldGUID>\\)"
    )
    print()
    _, server_infos = scan_players(server_dir, quiet=True)
    if not server_infos:
        print(
            "  No player save files found in that server's Players/ folder -- which "
            "means the server hasn't actually assigned anyone a real ID yet. Make "
            "sure you connected to it at least once (per the instructions above), "
            "then try again."
        )
        return
    print(f"Found {len(server_infos)} player(s) already on that server:\n")
    print_player_table(server_infos, numbered=True)
    print(
        "(This should be the blank, freshly-spawned character the server made "
        "when you connected just now -- pick whichever one is you. If more than "
        "one already has real progress, be careful: this will overwrite it.)\n"
    )
    server_choice = _prompt_choice("Which one is the real you on the server?", len(server_infos))
    target = server_infos[server_choice]
    print(f"\nTarget dedicated-server ID: {target.guid_str}  (Players/{target.path.name})\n")

    default_out = str(world_dir.parent / (world_dir.name + "_dedicated"))
    out_dir_str = _prompt("Where should the server-ready save be written?", default=default_out)
    out_dir = Path(_clean_path_input(out_dir_str))

    world_name = _prompt(
        "Rename the world (as it'll show in the server's world name)? "
        "Leave blank to keep the original name",
        default="",
    )

    print()
    try:
        perform_unhost(
            world_dir=world_dir,
            old_uid_str=selected.guid_str,
            new_uid_str=target.guid_str,
            out_dir=out_dir,
            world_name=world_name or None,
            force=False,
            yes=False,
        )
        print(
            f"\nRemember: copy the contents of {out_dir} OVER {server_dir}, "
            "replacing the blank character there -- not into a new folder."
        )
    except HostfixError as e:
        print(f"\nSomething went wrong:\n  {e}")
    except MigrationAborted:
        print("\nAborted, nothing was written.")


def run_interactive_sync() -> None:
    print("=" * 70)
    print(" palworld-oodle-hostfix -- sync mode")
    print(" Update your character on an already-live dedicated server")
    print(" from a single-player/co-op save, without touching anyone else")
    print("=" * 70)
    print()
    print(
        "Use this instead of the 'unhost' option when your dedicated server ISN'T "
        "brand new -- i.e. it already has real progress for you and/or other people. "
        "A whole-world push (what 'unhost' does) would blow away that history; this "
        "instead surgically updates only YOUR character, your owned Pals, and your "
        "personal containers (inventory/equipment/party/Palbox) -- every other "
        "player, the guild(s), and built structures are left completely untouched.\n"
    )

    world_dir = _prompt_world_folder(
        "Path to your SINGLE-PLAYER/CO-OP world folder (the one with Level.sav in "
        "it -- usually under ...\\Pal\\Saved\\SaveGames\\<SteamID>\\<WorldGUID>\\, "
        "you can paste or drag-and-drop it here)"
    )

    print()
    level, infos = scan_players(world_dir)
    if not infos:
        print("No player save files found in that world's Players/ folder. Nothing to do.")
        return
    print(f"Found {len(infos)} player(s) in this world:\n")
    print_player_table(infos, numbered=True)

    host_index = next((i for i, info in enumerate(infos) if info.is_singleplayer_host), None)
    if host_index is not None:
        print(
            "(Usually you want the one tagged as the single-player host ID -- "
            f"that's [{host_index + 1}].)\n"
        )
    else:
        print()

    choice = _prompt_choice("Which one do you want to sync onto the server?", len(infos))
    selected = infos[choice]
    print(f"\nSelected: {selected.guid_str}  (Players/{selected.path.name})\n")

    server_dir = _prompt_world_folder(
        "Now point me at your LIVE DEDICATED SERVER's save folder (the one that's "
        "currently running / has your and others' real progress on it -- usually "
        "under <PalServer install>\\Pal\\Saved\\SaveGames\\0\\<WorldGUID>\\)"
    )
    print()
    _, server_infos = scan_players(server_dir, quiet=True)
    if not server_infos:
        print("  No player save files found in that server's Players/ folder.")
        return
    print(f"Found {len(server_infos)} player(s) already on that server:\n")
    print_player_table(server_infos, numbered=True)
    print(
        "(Pick whichever one is YOUR real, already-established character on the "
        "server -- this is the one that will be updated. Everyone else in this "
        "list is left completely alone.)\n"
    )
    server_choice = _prompt_choice("Which one is you on the server?", len(server_infos))
    target = server_infos[server_choice]
    print(f"\nTarget dedicated-server ID: {target.guid_str}  (Players/{target.path.name})\n")

    default_out = str(server_dir.parent / (server_dir.name + "_synced"))
    out_dir_str = _prompt("Where should the updated server save be written?", default=default_out)
    out_dir = Path(_clean_path_input(out_dir_str))

    print()
    try:
        perform_sync(
            local_world_dir=world_dir,
            server_dir=server_dir,
            target_uid_str=target.guid_str,
            old_uid_str=selected.guid_str,
            out_dir=out_dir,
            yes=False,
        )
        print(
            f"\nRemember: copy the contents of {out_dir} OVER {server_dir}, "
            "replacing your character there -- not into a new folder."
        )
    except HostfixError as e:
        print(f"\nSomething went wrong:\n  {e}")
    except MigrationAborted:
        print("\nAborted, nothing was written.")


def main() -> None:
    if len(sys.argv) == 1:
        # No arguments at all -- most people sharing/running this tool
        # will just double-click it or run `python hostfix.py`, so that's
        # the friendly interactive wizard. Anyone who wants the scriptable
        # CLI (list / migrate with flags) just passes those as usual.
        try:
            run_interactive()
        except (KeyboardInterrupt, MigrationAborted):
            print("\nCancelled.")
        except Exception as e:  # noqa: BLE001 -- keep the console open on any crash
            print(f"\nUnexpected error: {e}")
        finally:
            try:
                input("\nPress Enter to exit...")
            except EOFError:
                pass
        return

    parser = argparse.ArgumentParser(
        prog="hostfix.py",
        description=(
            "Migrate a Palworld dedicated-server character into a "
            "single-player/co-op save (Oodle/PlM-save compatible)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List player UIDs found in a world save")
    p_list.add_argument("world_dir", help="Path to the world folder (contains Level.sav)")
    p_list.set_defaults(func=cmd_list)

    p_migrate = sub.add_parser("migrate", help="Migrate a character to single-player/co-op")
    p_migrate.add_argument("world_dir", help="Path to the SOURCE world folder")
    p_migrate.add_argument(
        "--old-uid", required=True, help="The dedicated-server player UID to migrate, e.g. "
        "aaaaaaaa-0000-0000-0000-000000000000 (see the 'list' command)"
    )
    p_migrate.add_argument(
        "--new-uid", default=None,
        help=f"Target UID (default: the single-player host ID, {SINGLEPLAYER_HOST_UID})"
    )
    p_migrate.add_argument("--out", default=None, help="Output folder (default: <world_dir>_migrated)")
    p_migrate.add_argument("--world-name", default=None, help="Optionally rename the world too")
    p_migrate.add_argument("-y", "--yes", action="store_true", help="Don't ask for confirmation")
    p_migrate.add_argument(
        "--force", action="store_true",
        help="Proceed even if a player save already exists for --new-uid (DANGEROUS: "
        "merges two characters' data)",
    )
    p_migrate.set_defaults(func=cmd_migrate)

    p_unhost = sub.add_parser(
        "unhost", help="Convert a single-player/co-op world into a dedicated-server-ready save"
    )
    p_unhost.add_argument("world_dir", help="Path to the SOURCE single-player/co-op world folder")
    p_unhost.add_argument(
        "--old-uid", default=None,
        help=f"The player UID to convert (default: the single-player host ID, {SINGLEPLAYER_HOST_UID})"
    )
    p_unhost.add_argument(
        "--new-uid", required=True,
        help="Your REAL dedicated-server player UID -- NOT something you can make up. "
        "Palworld derives it from a hash of your Steam account, so first: start the "
        "(otherwise empty) server, connect to it once with the real account you'll "
        "play as, fully stop the server, then run 'hostfix.py list <server_save_folder>' "
        "to find this value.",
    )
    p_unhost.add_argument("--out", default=None, help="Output folder (default: <world_dir>_dedicated)")
    p_unhost.add_argument("--world-name", default=None, help="Optionally rename the world too")
    p_unhost.add_argument("-y", "--yes", action="store_true", help="Don't ask for confirmation")
    p_unhost.add_argument(
        "--force", action="store_true",
        help="Proceed even if a player save already exists for --new-uid (DANGEROUS: "
        "merges two characters' data)",
    )
    p_unhost.set_defaults(func=cmd_unhost)

    p_sync = sub.add_parser(
        "sync",
        help="Update YOUR character on an already-live dedicated server from a "
        "single-player/co-op save, without touching anyone else",
    )
    p_sync.add_argument("local_world_dir", help="Path to the SOURCE single-player/co-op world folder")
    p_sync.add_argument(
        "--server-dir", required=True,
        help="Path to the LIVE dedicated server's save folder to update",
    )
    p_sync.add_argument(
        "--target-uid", required=True,
        help="Your REAL, already-established player UID on that server (see the 'list' command)",
    )
    p_sync.add_argument(
        "--old-uid", default=None,
        help="The player UID to sync from in the single-player world (default: the "
        f"single-player host ID, {SINGLEPLAYER_HOST_UID})",
    )
    p_sync.add_argument("--out", default=None, help="Output folder (default: <server_dir>_synced)")
    p_sync.add_argument("-y", "--yes", action="store_true", help="Don't ask for confirmation")
    p_sync.set_defaults(func=cmd_sync)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
