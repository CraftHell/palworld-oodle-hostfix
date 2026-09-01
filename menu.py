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
  3) optioneditor       -- edit a world's settings (WorldOption.sav)

Both hostfix directions and optioneditor can still be run directly
(`python hostfix.py`, `python optioneditor.py`) with their own CLI flags
for power users -- this is just a friendly landing page for everyone
else.
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
    print("      (hostfix unhost -- keeps your level and Pals; see README for limitations)")
    print("  [3] Edit world settings (WorldOption.sav)")
    print("      (optioneditor -- difficulty, rates, PvP, server settings, etc.)")
    print("  [0] Exit")
    print()

    while True:
        try:
            choice = input("Pick an option [0-3]: ").strip()
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
            import optioneditor
            try:
                optioneditor.run_interactive()
            except (KeyboardInterrupt, optioneditor.EditAborted):
                print("\nCancelled.")
            except optioneditor.OptionEditorError as e:
                print(f"\nSomething went wrong:\n  {e}")
            return
        print("  (please enter 0, 1, 2, or 3)")


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
