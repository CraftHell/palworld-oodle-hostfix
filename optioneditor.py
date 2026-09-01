#!/usr/bin/env python3
"""
optioneditor.py
===============
A friendly editor for Palworld's ``WorldOption.sav`` -- the file that
holds a world's settings: difficulty, day/night speed, all the XP/damage/
drop rate sliders, PvP and multiplayer toggles, and (for a dedicated
server) the server name/password/ports/etc.

Unlike Character/Guild data, ``WorldOption.sav`` turned out to be fully
and cleanly parseable with ``palworld-save-tools`` even on saves from the
newer Oodle-compressed game version -- no raw-byte hacking needed here,
just a normal structured read/edit/write. It shares the Oodle (``PlM``)
decompression support from ``palcommon.py`` with ``hostfix.py``, so it
works on both classic (``PlZ``) and dedicated-server (``PlM``) saves.

Requirements
------------
    pip install palworld-save-tools pyooz

Usage
-----
    # Easy mode: interactive, categorized menu
    python optioneditor.py

    # or point it at a file directly
    python optioneditor.py /path/to/WorldOption.sav

    # Power-user mode: scriptable CLI
    python optioneditor.py show /path/to/WorldOption.sav
    python optioneditor.py set /path/to/WorldOption.sav ExpRate 3.0
    python optioneditor.py set /path/to/WorldOption.sav bIsMultiplay true

    # Generate a PalWorldSettings.ini from a WorldOption.sav's current
    # settings -- useful for a dedicated server, since a handful of fields
    # (ServerName, ServerPassword, PublicPort, bIsMultiplay, and similar
    # identity/network settings) are only ever read from the ini, even
    # when a WorldOption.sav also exists and takes priority for everything
    # else. Also available from the interactive menu ([E] on the category
    # screen).
    python optioneditor.py export-ini /path/to/WorldOption.sav

Always makes a ``.bak`` backup of the original file the first time it
writes to a given path in a session, and never touches anything until you
confirm.

License: MIT. Use at your own risk -- this edits game save files.
ALWAYS keep a backup of your original save before running this.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path
from typing import Any

from palcommon import SavFile

SETTINGS_PATH = ("properties", "OptionWorldData", "value", "Settings", "value")

# Known enum choices for the handful of EnumProperty fields, shown as a
# hint only -- you can always type a different value if your game version
# uses something not listed here.
KNOWN_ENUM_CHOICES = {
    "Difficulty": ["None", "Casual", "Normal", "Hard", "Custom"],
    "DeathPenalty": ["None", "Item", "ItemAndEquipment", "All"],
    "LogFormatType": ["Text", "Json"],
    "RandomizerType": ["None", "Region", "All"],
}
# Fallback enum-type prefix for array fields, used only if the array is
# currently empty (so there's nothing to infer the prefix from).
KNOWN_ARRAY_ENUM_PREFIX = {
    "CrossplayPlatforms": "EPalAllowConnectPlatform",
}

CATEGORIES: list[tuple[str, list[str]]] = [
    ("Difficulty & rates", [
        "Difficulty", "RandomizerType", "RandomizerSeed", "bIsRandomizerPalLevelRandom",
        "DayTimeSpeedRate", "NightTimeSpeedRate", "ExpRate", "PalCaptureRate", "PalSpawnNumRate",
        "PalDamageRateAttack", "PalDamageRateDefense", "PlayerDamageRateAttack", "PlayerDamageRateDefense",
        "PlayerStomachDecreaceRate", "PlayerStaminaDecreaceRate", "PlayerAutoHPRegeneRate",
        "PlayerAutoHpRegeneRateInSleep", "PalStomachDecreaceRate", "PalStaminaDecreaceRate",
        "PalAutoHPRegeneRate", "PalAutoHpRegeneRateInSleep", "BuildObjectHpRate",
        "BuildObjectDamageRate", "BuildObjectDeteriorationDamageRate", "CollectionDropRate",
        "CollectionObjectHpRate", "CollectionObjectRespawnSpeedRate", "EnemyDropItemRate",
        "WorkSpeedRate", "ItemWeightRate", "EquipmentDurabilityDamageRate",
        "ItemCorruptionMultiplier", "MonsterFarmActionSpeedRate",
    ]),
    ("PvP, death & hardcore", [
        "DeathPenalty", "bEnablePlayerToPlayerDamage", "bEnableFriendlyFire", "bEnableInvaderEnemy",
        "bIsPvP", "bHardcore", "bPalLost", "bCharacterRecreateInHardcore",
        "bCanPickupOtherGuildDeathPenaltyDrop", "bEnableNonLoginPenalty",
        "bEnableDefenseOtherGuildPlayer", "bInvisibleOtherGuildBaseCampAreaFX", "bBuildAreaLimit",
        "bDisplayPvPItemNumOnWorldMap_BaseCamp", "bDisplayPvPItemNumOnWorldMap_Player",
        "AdditionalDropItemWhenPlayerKillingInPvPMode", "AdditionalDropItemNumWhenPlayerKillingInPvPMode",
        "bAdditionalDropItemWhenPlayerKillingInPvPMode", "BlockRespawnTime",
        "RespawnPenaltyDurationThreshold", "RespawnPenaltyTimeScale",
    ]),
    ("Player", [
        "bEnableAimAssistPad", "bEnableAimAssistKeyboard", "bEnableFastTravel",
        "bEnableFastTravelOnlyBaseCamp", "bIsStartLocationSelectByMap", "bExistPlayerAfterLogout",
        "bAllowEnhanceStat_Health", "bAllowEnhanceStat_Attack", "bAllowEnhanceStat_Stamina",
        "bAllowEnhanceStat_Weight", "bAllowEnhanceStat_WorkSpeed",
    ]),
    ("Base, building & guild", [
        "DropItemMaxNum", "PhysicsActiveDropItemMaxNum", "DropItemMaxNum_UNKO", "bActiveUNKO",
        "BaseCampMaxNum", "BaseCampWorkerMaxNum", "DropItemAliveMaxHours",
        "bAutoResetGuildNoOnlinePlayers", "AutoResetGuildTimeNoOnlinePlayers", "GuildPlayerMaxNum",
        "BaseCampMaxNumInGuild", "PalEggDefaultHatchingTime", "MaxBuildingLimitNum",
        "bAllowGlobalPalboxExport", "bAllowGlobalPalboxImport", "bEnableBuildingPlayerUIdDisplay",
        "BuildingNameDisplayCacheTTLSeconds", "GuildRejoinCooldownMinutes",
        "AutoTransferMasterCheckIntervalSeconds", "AutoTransferMasterThresholdDays", "MaxGuildsPerFrame",
        "EnablePredatorBossPal", "SupplyDropSpan",
    ]),
    ("Multiplayer & server", [
        "bIsMultiplay", "autoSaveSpan", "CoopPlayerMaxNum", "ServerPlayerMaxNum", "ServerName",
        "ServerDescription", "AdminPassword", "ServerPassword", "bAllowClientMod", "PublicPort",
        "PublicIP", "RCONEnabled", "RCONPort", "Region", "bUseAuth", "BanListURL", "RESTAPIEnabled",
        "RESTAPIPort", "bShowPlayerList", "ChatPostLimitPerMinute", "CrossplayPlatforms",
        "bIsUseBackupSaveData", "LogFormatType", "bIsShowJoinLeftMessage",
        "ServerReplicatePawnCullDistance", "DenyTechnologyList", "bEnableVoiceChat",
        "VoiceChatMaxVolumeDistance", "VoiceChatZeroVolumeDistance",
        "ItemContainerForceMarkDirtyInterval", "PlayerDataPalStorageUpdateCheckTickInterval",
    ]),
]


class OptionEditorError(Exception):
    """User-facing, recoverable problem."""


class EditAborted(Exception):
    """User backed out without saving."""


# --------------------------------------------------------------------------
# Load / save
# --------------------------------------------------------------------------
def _dig(d: dict, path: tuple[str, ...]) -> Any:
    cur = d
    for key in path:
        cur = cur[key]
    return cur


def load_world_option(path: Path) -> tuple[SavFile, dict, dict]:
    """Returns (sav, full_dumped_json, settings_dict). `settings_dict` is a
    live reference into `full_dumped_json` -- mutate it in place, then pass
    `full_dumped_json` to `save_world_option`."""
    from palworld_save_tools.gvas import GvasFile
    from palworld_save_tools.json_tools import CustomEncoder
    from palworld_save_tools.paltypes import PALWORLD_TYPE_HINTS

    sav = SavFile.load(path)
    with contextlib.redirect_stdout(io.StringIO()):
        gvas_file = GvasFile.read(sav.raw_gvas, PALWORLD_TYPE_HINTS, {}, allow_nan=True)
    dumped = json.loads(json.dumps(gvas_file.dump(), cls=CustomEncoder))
    try:
        settings = _dig(dumped, SETTINGS_PATH)
    except (KeyError, TypeError) as e:
        raise OptionEditorError(
            f"Couldn't find the settings block in {path} (unexpected file shape: {e}). "
            "Is this really a WorldOption.sav?"
        )
    return sav, dumped, settings


def save_world_option(sav: SavFile, dumped: dict, out_path: Path, backup: bool = True) -> None:
    from palworld_save_tools.gvas import GvasFile

    if backup and out_path.exists():
        bak = out_path.with_suffix(out_path.suffix + ".bak")
        if not bak.exists():
            bak.write_bytes(out_path.read_bytes())
    with contextlib.redirect_stdout(io.StringIO()):
        new_gvas = GvasFile.load(dumped)
        new_raw = new_gvas.write({})
    new_sav = SavFile(path=out_path, raw_gvas=new_raw, save_type=sav.save_type)
    new_sav.write(out_path)


# --------------------------------------------------------------------------
# Field get/set (type-aware)
# --------------------------------------------------------------------------
def format_value(name: str, prop: dict) -> str:
    t = prop.get("type")
    if t == "EnumProperty":
        return str(prop["value"]["value"]).split("::")[-1]
    if t == "ArrayProperty":
        values = prop["value"]["values"]
        shown = [str(v).split("::")[-1] for v in values]
        return "[" + ", ".join(shown) + "]" if shown else "[] (empty)"
    if t == "BoolProperty":
        return "true" if prop["value"] else "false"
    if t == "StrProperty" and prop["value"] == "":
        return "(empty)"
    return str(prop["value"])


def set_value(name: str, prop: dict, raw_input_str: str) -> None:
    """Mutates `prop` in place based on its Unreal property type. Raises
    ValueError with a human-readable message on bad input."""
    t = prop.get("type")
    s = raw_input_str.strip()

    if t == "BoolProperty":
        low = s.lower()
        if low in ("true", "t", "yes", "y", "1"):
            prop["value"] = True
        elif low in ("false", "f", "no", "n", "0"):
            prop["value"] = False
        else:
            raise ValueError(f"expected true/false, got {raw_input_str!r}")

    elif t == "IntProperty":
        try:
            prop["value"] = int(s)
        except ValueError:
            raise ValueError(f"expected a whole number, got {raw_input_str!r}")

    elif t == "FloatProperty":
        try:
            prop["value"] = float(s)
        except ValueError:
            raise ValueError(f"expected a number, got {raw_input_str!r}")

    elif t in ("StrProperty", "NameProperty"):
        prop["value"] = raw_input_str  # not stripped -- allow intentional spaces

    elif t == "EnumProperty":
        current = prop["value"]["value"]
        prefix = current.split("::")[0] if "::" in current else current
        choice = s.split("::")[-1] if "::" in s else s
        known = KNOWN_ENUM_CHOICES.get(name)
        if known is not None:
            match = next((k for k in known if k.lower() == choice.lower()), None)
            if match is None:
                raise ValueError(
                    f"{raw_input_str!r} isn't one of the known values for {name}: "
                    f"{', '.join(known)}"
                )
            choice = match
        prop["value"]["value"] = f"{prefix}::{choice}"

    elif t == "ArrayProperty":
        items = [x.strip() for x in s.split(",") if x.strip()] if s else []
        if prop.get("array_type") == "EnumProperty":
            existing = prop["value"]["values"]
            prefix = (
                existing[0].split("::")[0]
                if existing and "::" in existing[0]
                else KNOWN_ARRAY_ENUM_PREFIX.get(name, "")
            )
            new_items = []
            for it in items:
                it = it.split("::")[-1] if "::" in it else it
                new_items.append(f"{prefix}::{it}" if prefix else it)
            prop["value"]["values"] = new_items
        else:
            prop["value"]["values"] = items

    else:
        raise ValueError(f"don't know how to edit property type {t!r} yet")


# --------------------------------------------------------------------------
# Export to PalWorldSettings.ini
#
# WorldOption.sav and PalWorldSettings.ini store the exact same underlying
# settings struct -- one as a binary GVAS property map, the other as a
# single comma-separated OptionSettings=(...) line of plain text. This
# reformats the values already parsed out of a .sav into that ini syntax,
# rather than re-deriving them some other way.
#
# On a dedicated server, WorldOption.sav (if present) takes priority over
# the ini for most gameplay settings, but a handful of identity/network
# fields -- ServerName, ServerPassword, AdminPassword, PublicPort,
# PublicIP, RCON*, bIsMultiplay, and similar -- are always read from the
# ini instead, regardless of what's in the .sav. That's the scenario this
# export is for: getting those fields (and everything else, for
# completeness) into a real ini the server will actually honor.
#
# A field whose value can't be safely written as one ini token (an
# unrecognized property type, or a string containing a literal double
# quote, which would break the surrounding quoting) is skipped rather than
# guessed at -- one malformed token would make the game ignore the whole
# OptionSettings line, and PalWorldSettings.ini tolerates a partial list
# (anything omitted just falls back to the game's default), so skipping a
# handful of edge-case fields is far safer than writing something that
# might not parse.
# --------------------------------------------------------------------------
def format_ini_value(name: str, prop: dict) -> str:
    """Render one setting's value the way PalWorldSettings.ini's
    OptionSettings=(...) line expects it. NOT the same formatting as
    format_value() above (which is for the interactive display) -- e.g.
    booleans are 'True'/'False' here, not 'true'/'false', and floats are
    always shown with 6 decimal places to match the game's own generated
    inis. Raises ValueError if this value can't be safely represented."""
    t = prop.get("type")
    if t == "BoolProperty":
        return "True" if prop["value"] else "False"
    if t == "IntProperty":
        return str(prop["value"])
    if t == "FloatProperty":
        return f"{float(prop['value']):.6f}"
    if t in ("StrProperty", "NameProperty"):
        val = str(prop["value"])
        if '"' in val:
            raise ValueError("contains a double-quote character, can't be safely quoted")
        return f'"{val}"'
    if t == "EnumProperty":
        return str(prop["value"]["value"]).split("::")[-1]
    if t == "ArrayProperty":
        values = prop["value"]["values"]
        if prop.get("array_type") == "EnumProperty":
            items = [str(v).split("::")[-1] for v in values]
        else:
            items = []
            for v in values:
                v = str(v)
                if '"' in v:
                    raise ValueError("contains an item with a double-quote character")
                items.append(f'"{v}"')
        return "(" + ",".join(items) + ")"
    raise ValueError(f"unrecognized property type {t!r}")


def build_ini_text(settings: dict) -> tuple[str, list[tuple[str, str]]]:
    """Returns (ini_text, skipped) where skipped is [(field_name, reason), ...]
    for anything left out of the output."""
    parts = []
    skipped: list[tuple[str, str]] = []
    for name, prop in settings.items():
        try:
            parts.append(f"{name}={format_ini_value(name, prop)}")
        except ValueError as e:
            skipped.append((name, str(e)))
    ini_text = "[/Script/Pal.PalGameWorldSettings]\nOptionSettings=(" + ",".join(parts) + ")\n"
    return ini_text, skipped


def export_ini(world_option_path: Path, out_path: Path, force: bool) -> list[tuple[str, str]]:
    """Load a WorldOption.sav and write out_path as a PalWorldSettings.ini
    built from its current settings. Returns the list of (field, reason)
    skipped. Raises OptionEditorError for validation problems."""
    _, _, settings = load_world_option(world_option_path)
    ini_text, skipped = build_ini_text(settings)
    if out_path.exists() and not force:
        raise OptionEditorError(
            f"{out_path} already exists -- refusing to overwrite it without confirmation.\n"
            "(Back up or rename your existing PalWorldSettings.ini first if you want to keep it.)"
        )
    out_path.write_text(ini_text, encoding="utf-8")
    return skipped


# --------------------------------------------------------------------------
# Interactive wizard
# --------------------------------------------------------------------------
def _prompt(msg: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    try:
        raw = input(f"{msg}{suffix}: ")
    except EOFError:
        raise EditAborted()
    val = raw.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        val = val[1:-1].strip()
    if not val and default is not None:
        return default
    return val


def _prompt_yes_no(msg: str, default: bool = False) -> bool:
    d = "Y/n" if default else "y/N"
    try:
        raw = input(f"{msg} [{d}] ").strip().lower()
    except EOFError:
        return default
    if not raw:
        return default
    return raw.startswith("y")


def _prompt_choice(msg: str, n_options: int, allow_zero_back: bool = True) -> int:
    lo = 0 if allow_zero_back else 1
    while True:
        try:
            raw = input(f"{msg}: ").strip()
        except EOFError:
            raise EditAborted()
        if raw.isdigit() and lo <= int(raw) <= n_options:
            return int(raw)
        print(f"  (please enter a number from {lo} to {n_options})")


def _categorize(settings: dict) -> list[tuple[str, list[str]]]:
    """Returns the category list, but only with fields that actually exist
    in this file, plus an 'Other' bucket for anything not in CATEGORIES
    (keeps this tool from silently hiding fields on a save from a newer/
    older game version than this script was written against)."""
    known = set()
    for _, fields in CATEGORIES:
        known.update(fields)
    result = []
    for cat_name, fields in CATEGORIES:
        present = [f for f in fields if f in settings]
        if present:
            result.append((cat_name, present))
    leftover = [f for f in settings if f not in known]
    if leftover:
        result.append(("Other", leftover))
    return result


def _edit_one_field(settings: dict, name: str) -> None:
    prop = settings[name]
    print(f"\n{name}  (current: {format_value(name, prop)})")
    if name in KNOWN_ENUM_CHOICES:
        print(f"  common choices: {', '.join(KNOWN_ENUM_CHOICES[name])}")
    if prop.get("type") == "ArrayProperty":
        print("  enter a comma-separated list (blank line = empty list)")
    new_val = _prompt("  new value (blank to cancel)", default="")
    if new_val == "":
        print("  (unchanged)")
        return
    try:
        set_value(name, prop, new_val)
    except ValueError as e:
        print(f"  Couldn't set that: {e}")
        return
    print(f"  -> {name} is now {format_value(name, prop)}  (not saved yet)")


def _run_export_ini_prompt(world_option_path: Path) -> None:
    default_out = str(world_option_path.parent / "PalWorldSettings.ini")
    out_str = _prompt(
        "Write the ini to (usually <PalServer install>\\Pal\\Saved\\Config\\WindowsServer\\"
        "PalWorldSettings.ini on the SERVER, not this world folder -- you'll need to move it "
        "there yourself)",
        default=default_out,
    )
    out_path = Path(out_str)
    force = False
    if out_path.exists():
        if not _prompt_yes_no(f"{out_path} already exists -- overwrite it?", default=False):
            print("Cancelled, nothing was written.")
            return
        force = True
    try:
        skipped = export_ini(world_option_path, out_path, force=force)
    except OptionEditorError as e:
        print(f"\nSomething went wrong:\n  {e}")
        return
    print(f"\nWrote {out_path}")
    if skipped:
        print(
            f"\n{len(skipped)} field(s) couldn't be safely written and were left out -- set "
            "these by hand in the ini if you need them:"
        )
        for name, reason in skipped:
            print(f"    {name}  ({reason})")
    print(
        "\nStop your server, replace its PalWorldSettings.ini with this file, then start it "
        "back up (settings are only read at boot).\n"
        "Note: as long as a WorldOption.sav also exists in the server's save folder, it takes "
        "priority over this ini for most gameplay settings -- but server "
        "identity/network fields (name, password, port, the multiplayer toggle, and similar) "
        "are always read from the ini regardless, which is usually why you'd want this export "
        "in the first place.\n"
        "If anything in the generated file looks off, the safest fallback is copying your "
        "server's own DefaultPalWorldSettings.ini and editing just the fields you care about "
        "by hand instead."
    )


def run_interactive(initial_path: str | None = None) -> None:
    print("=" * 70)
    print(" optioneditor -- edit a Palworld WorldOption.sav")
    print("=" * 70)
    print()

    if initial_path:
        path_str = initial_path
    else:
        path_str = _prompt(
            "Path to WorldOption.sav (you can paste or drag-and-drop it here)"
        )
    path = Path(path_str)
    if not path.is_file():
        raise OptionEditorError(f"'{path}' isn't a file.")

    print(f"\nReading {path} ...")
    sav, dumped, settings = load_world_option(path)
    print(f"Loaded {len(settings)} settings.\n")

    dirty = False
    categories = _categorize(settings)

    while True:
        print("Categories:")
        for i, (cat_name, fields) in enumerate(categories, start=1):
            print(f"  [{i}] {cat_name}  ({len(fields)} settings)")
        print(f"  [0] {'Save and exit' if dirty else 'Exit'}" + (" (unsaved changes!)" if dirty else ""))
        print("  [E] Export these settings as a PalWorldSettings.ini (for a dedicated server)")
        try:
            raw = input(f"Pick a category [0-{len(categories)}, or E]: ").strip()
        except EOFError:
            raise EditAborted()
        if raw.lower() == "e":
            _run_export_ini_prompt(path)
            continue
        if not (raw.isdigit() and 0 <= int(raw) <= len(categories)):
            print(f"  (please enter a number from 0 to {len(categories)}, or E)")
            continue
        cat_choice = int(raw)
        if cat_choice == 0:
            break

        cat_name, fields = categories[cat_choice - 1]
        while True:
            print(f"\n-- {cat_name} --")
            for i, f in enumerate(fields, start=1):
                print(f"  [{i}] {f} = {format_value(f, settings[f])}")
            print("  [0] Back to categories")
            field_choice = _prompt_choice("Pick a setting to edit", len(fields))
            if field_choice == 0:
                break
            field_name = fields[field_choice - 1]
            before = json.dumps(settings[field_name])
            _edit_one_field(settings, field_name)
            if json.dumps(settings[field_name]) != before:
                dirty = True

    if not dirty:
        print("\nNo changes made.")
        return

    print()
    out_str = _prompt("Save changes to", default=str(path))
    out_path = Path(out_str)
    if out_path == path:
        print(f"A backup of the original will be saved as {path}.bak")
    if not _prompt_yes_no("Proceed?", default=False):
        print("Discarded, nothing was written.")
        return
    save_world_option(sav, dumped, out_path)
    print(f"Wrote {out_path}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def cmd_show(args: argparse.Namespace) -> None:
    path = Path(args.path)
    _, _, settings = load_world_option(path)
    categories = _categorize(settings)
    for cat_name, fields in categories:
        print(f"\n-- {cat_name} --")
        for f in fields:
            print(f"  {f} = {format_value(f, settings[f])}")


def cmd_set(args: argparse.Namespace) -> None:
    path = Path(args.path)
    sav, dumped, settings = load_world_option(path)
    if args.field not in settings:
        sys.exit(f"No such setting: {args.field}  (run 'optioneditor.py show {path}' to list them)")
    before = format_value(args.field, settings[args.field])
    try:
        set_value(args.field, settings[args.field], args.value)
    except ValueError as e:
        sys.exit(f"Couldn't set {args.field}: {e}")
    after = format_value(args.field, settings[args.field])
    out_path = Path(args.out) if args.out else path
    save_world_option(sav, dumped, out_path, backup=not args.no_backup)
    print(f"{args.field}: {before} -> {after}")
    print(f"Wrote {out_path}" + ("" if args.no_backup else f"  (backup at {out_path}.bak if it existed)"))


def cmd_export_ini(args: argparse.Namespace) -> None:
    path = Path(args.path)
    out_path = Path(args.out) if args.out else path.with_name("PalWorldSettings.ini")
    try:
        skipped = export_ini(path, out_path, force=args.force)
    except OptionEditorError as e:
        sys.exit(str(e))
    print(f"Wrote {out_path}")
    if skipped:
        print(f"{len(skipped)} field(s) skipped (couldn't be safely written as ini values):")
        for name, reason in skipped:
            print(f"    {name}  ({reason})")


def main() -> None:
    if len(sys.argv) == 1 or (len(sys.argv) == 2 and Path(sys.argv[1]).suffix.lower() == ".sav"):
        # No arguments, or just a dropped-in file path -- run the friendly
        # interactive wizard.
        initial = sys.argv[1] if len(sys.argv) == 2 else None
        try:
            run_interactive(initial)
        except (KeyboardInterrupt, EditAborted):
            print("\nCancelled.")
        except OptionEditorError as e:
            print(f"\nSomething went wrong:\n  {e}")
        except Exception as e:  # noqa: BLE001 -- keep the console open on any crash
            print(f"\nUnexpected error: {e}")
        finally:
            try:
                input("\nPress Enter to exit...")
            except EOFError:
                pass
        return

    parser = argparse.ArgumentParser(
        prog="optioneditor.py",
        description="Edit a Palworld WorldOption.sav's settings.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_show = sub.add_parser("show", help="Print all current settings")
    p_show.add_argument("path", help="Path to WorldOption.sav")
    p_show.set_defaults(func=cmd_show)

    p_set = sub.add_parser("set", help="Change one setting")
    p_set.add_argument("path", help="Path to WorldOption.sav")
    p_set.add_argument("field", help="Setting name, e.g. ExpRate (see 'show' for the full list)")
    p_set.add_argument("value", help="New value, e.g. 3.0 / true / Normal")
    p_set.add_argument("--out", default=None, help="Write to a different file instead of in-place")
    p_set.add_argument("--no-backup", action="store_true", help="Don't write a .bak backup")
    p_set.set_defaults(func=cmd_set)

    p_export = sub.add_parser(
        "export-ini",
        help="Generate a PalWorldSettings.ini from a WorldOption.sav's current settings",
    )
    p_export.add_argument("path", help="Path to WorldOption.sav")
    p_export.add_argument(
        "--out", default=None,
        help="Output path (default: PalWorldSettings.ini next to the input file)",
    )
    p_export.add_argument("--force", action="store_true", help="Overwrite --out if it already exists")
    p_export.set_defaults(func=cmd_export_ini)

    args = parser.parse_args()
    try:
        args.func(args)
    except OptionEditorError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
