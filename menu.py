#!/usr/bin/env python3
"""
menu.py
=======
Single entry point for the palworld-oodle-hostfix toolkit. Double-click
this (or run `python menu.py`) to pick between the bundled tools:

  1) hostfix (migrate)  -- migrate a dedicated-server character into
                            single-player/co-op
  2) hostfix (unhost)   -- convert a single-player/co-op world into a
                            dedicated-server-ready save
  3) hostfix (sync)     -- update YOUR character (and guild membership) on
                            an already-live dedicated server from a
                            single-player/co-op save, without touching
                            anyone else
  4) optioneditor       -- edit a world's settings (WorldOption.sav)
  5) hostfix (backup)   -- zip up a world folder's save files
  6) hostfix (doctor)   -- scan a world folder for known problems/gotchas

Both hostfix directions, sync, optioneditor, backup, and doctor can still
be run directly (`python hostfix.py`, `python optioneditor.py`) with
their own CLI flags for power users -- this is just a friendly landing
page for everyone else.
"""
from __future__ import annotations

import sys


def main() -> None:
    print("=" * 70)
    print(" palworld-oodle-hostfix toolkit")
    print("=" * 70)
    print()
    print("  [1] Migrate a dedicated-server character into single-player/co-op")
    print("      (hostfix -- keeps your level, Pals, guild, and base)")
    print("  [2] Convert a single-player/co-op world into a dedicated-server-ready save")
    print("      (hostfix unhost -- for a BRAND NEW server; see README for limitations)")
    print("  [3] Update your character on an ALREADY-LIVE dedicated server")
    print("      (hostfix sync -- safe when the server already has real progress for")
    print("       you and/or others; updates your own guild membership too, but never")
    print("       touches other players, their guild data, or built structures)")
    print("  [4] Edit world settings (WorldOption.sav)")
    print("      (optioneditor -- difficulty, rates, PvP, server settings, etc.)")
    print("  [5] Back up a world folder's save files")
    print("      (hostfix backup -- a quick timestamped zip before anything risky)")
    print("  [6] Check a world folder for known problems")
    print("      (hostfix doctor -- corrupt files, duplicate IDs, missing/degraded")
    print("       player data compared to a backup, WorldOption.sav/ini gotchas)")
    print("  [0] Exit")
    print()

    while True:
        try:
            choice = input("Pick an option [0-6]: ").strip()
        except EOFError:
            return
        if choice == "0":
            return
        if choice == "1":
            import hostfix
            hostfix.run_interactive()
            return
        if choice == "2":
            import hostfix
            hostfix.run_interactive_unhost()
            return
        if choice == "3":
            import hostfix
            hostfix.run_interactive_sync()
            return
        if choice == "4":
            import optioneditor
            try:
                optioneditor.run_interactive()
            except (KeyboardInterrupt, optioneditor.EditAborted):
                print("\nCancelled.")
            except optioneditor.OptionEditorError as e:
                print(f"\nSomething went wrong:\n  {e}")
            return
        if choice == "5":
            import hostfix
            hostfix.run_interactive_backup()
            return
        if choice == "6":
            import hostfix
            hostfix.run_interactive_doctor()
            return
        print("  (please enter a number from 0 to 6)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
    finally:
        try:
            input("\nPress Enter to exit...")
        except EOFError:
            pass
        sys.exit(0)
