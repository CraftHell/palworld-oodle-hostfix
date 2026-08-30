#!/usr/bin/env python3
"""
palworld-oodle-hostfix
======================

Migrate a Palworld *dedicated server* character (and everything tied to
them: owned Pals, guild membership, placed/painted building pieces) into
a *single-player / co-op host* save, keeping their level, inventory, and
world progress intact.

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
   tag. A single scoped find-and-replace of that 16-byte pattern (verified
   byte-for-byte before writing) reassigns literally everything that ID
   touches in one pass, without needing to understand the surrounding
   struct at all. Always writes back in the classic zlib ``PlZ`` format,
   which Palworld happily reads on any platform.

This was built and validated against a real dedicated-server world with
~1900 real references to a single player ID scattered across Pals, a
guild, and base structures -- all of which correctly reassigned to the
new ID with a single global replace, verified by an exact expected-vs-
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

Then copy the output folder's contents into your local
``...\\Pal\\Saved\\SaveGames\\<YourSteamID>\\<WorldGUID>\\`` (create the
WorldGUID folder if it doesn't already exist -- the name doesn't matter,
Palworld reads whatever's in there).

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


def perform_migration(
    world_dir: Path,
    old_uid_str: str,
    new_uid_str: str | None = None,
    out_dir: Path | None = None,
    world_name: str | None = None,
    force: bool = False,
    yes: bool = False,
) -> Path:
    """Core migration logic, shared by the `migrate` CLI command and the
    interactive wizard. Raises HostfixError for validation problems,
    MigrationAborted if the user declines the confirmation prompt, and
    returns the output directory on success."""
    out_dir = out_dir or (world_dir.parent / (world_dir.name + "_migrated"))

    if not GUID_STR_RE.match(old_uid_str):
        raise HostfixError(f"--old-uid doesn't look like a GUID: {old_uid_str}")
    new_uid_str = new_uid_str or SINGLEPLAYER_HOST_UID
    if not GUID_STR_RE.match(new_uid_str):
        raise HostfixError(f"--new-uid doesn't look like a GUID: {new_uid_str}")

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

    # --- LevelMeta.sav / WorldOption.sav: pass through (also normalizes
    # PlM -> PlZ), optionally rename the world.
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
        sf.raw_gvas, _ = replace_uid_everywhere(sf.raw_gvas, old_uid, new_uid)
        if name == "LevelMeta.sav" and world_name:
            sf = _rename_world(sf, world_name)
        sf.write(out_dir / name)
        print(f"Wrote {out_dir / name}")

    print(
        f"\nDone. Copy the contents of:\n  {out_dir}\n"
        "into your local single-player save slot, e.g.:\n"
        r"  %LOCALAPPDATA%\Pal\Saved\SaveGames\<YourSteamID>\<AnyWorldGUID>" "\\"
        "\n(create that WorldGUID folder if you don't already have one for this "
        "world -- the folder name doesn't matter, Palworld reads whatever's inside it).\n"
        "NOTE: this does not touch LocalData.sav. If you have an existing LocalData.sav "
        "for this world slot, leave it in place; otherwise Palworld will generate a fresh one."
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

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
