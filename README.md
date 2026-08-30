# palworld-oodle-hostfix

Two small tools for Palworld saves using the newer **Oodle-compressed
(`PlM`)** format (dedicated servers, since the 2026 save-format update):

- **hostfix** — migrate a character from a **dedicated server** save into
  a **single-player / co-op host** save, keeping their level, stats,
  inventory, unlocked tech, owned Pals, guild membership, built
  structures, and fast-travel/map reveal progress intact.
- **optioneditor** — edit a world's settings (`WorldOption.sav`):
  difficulty, day/night speed, every XP/damage/drop-rate slider, PvP and
  multiplayer toggles, and (for a dedicated server) the server
  name/password/ports/etc. Handy for tuning a world after migrating it
  with hostfix, or any time.

## The problem this solves

If you've tried to do this since Palworld's 2026 save-format update, you've
probably found that every existing tool for it — `palworld-save-tools`,
`xNul/palworld-host-save-fix`, GUI converters, etc. — either:

- can't even open the save (`Exception: not a compressed Palworld save,
  found b'PlM' instead of b'PlZ'`), because dedicated-server saves are now
  compressed with Oodle/Kraken instead of zlib, or
- requires a Windows-only Oodle DLL, or
- can decompress it but then fails to parse the Character/Guild data
  (`Exception: Warning: EOF not reached` / `could not read 16 bytes for
  uuid`), because the internal binary layout of those structures changed
  too, and the public tools' decoders haven't caught up.

**This tool works around both problems at once**, and was built and
verified against a real ~1900-day dedicated-server world:

1. **Decompression** uses the [`ooz`](https://pypi.org/project/pyooz/)
   Python bindings — an open-source, cross-platform Oodle/Kraken
   decompressor — instead of a redistributed Windows DLL. `pip install`
   and go, on Linux, macOS, or Windows.
2. **Editing** skips trying to fully understand the new (and only
   partially reverse-engineered) Character/Guild struct layout entirely.
   Instead it works one level down, directly on the decompressed raw
   bytes: a dedicated-server player ID is a 16-byte GUID shaped like
   `XXXXXXXX-0000-0000-0000-000000000000` (only the first 4 bytes vary).
   That exact 16-byte pattern turns out to appear **only** in places that
   legitimately reference that player — the character map key, every
   owned Pal's `OwnerPlayerUId` / `OldOwnerPlayerUIds` / nickname-modifier
   fields, guild membership records, and placed/painted building builder
   tags — regardless of which sub-fields the format change touched. A
   single global find-and-replace of that pattern reassigns literally
   everything in one pass, and the tool verifies the number of changed
   bytes exactly matches the expected count *before* writing anything.

Recompression always writes back plain zlib (`PlZ`), which Palworld reads
fine on any platform — there's no need to write Oodle back out.

## Install

Needs Python 3.9+ ([python.org/downloads](https://www.python.org/downloads/) —
check "Add Python to PATH" during setup on Windows).

- **Windows:** double-click `setup.bat` once. That's it.
- **Everyone else / manual:**
  ```
  pip install -r requirements.txt
  ```
  (That's `palworld-save-tools` and `pyooz`.)

## Usage

### Easy mode: the interactive menu

- **Windows:** double-click `run.bat`.
- **Anywhere:** run
  ```
  python menu.py
  ```

That gives you a choice of the two tools:

```
  [1] Migrate a dedicated-server character into single-player/co-op
      (hostfix -- keeps your level, Pals, guild, and base)
  [2] Edit world settings (WorldOption.sav)
      (optioneditor -- difficulty, rates, PvP, server settings, etc.)
  [0] Exit
```

Each one can also be run directly the same way (`python hostfix.py` /
`python optioneditor.py`), or on Windows by double-clicking `editoptions.bat`
to jump straight to the settings editor, if you only ever want one of them.

#### 1) hostfix — migrate a character

It walks you through everything with a numbered menu — no command-line
flags to remember:

```
Path to the SERVER world folder (the one with Level.sav in it -- you can
paste or drag-and-drop it here): C:\Users\you\Downloads\some_world_folder

Found 5 player(s) in this world:

  [1] UID: bbbbbbbb-0000-0000-0000-000000000000
      file: Players/BBBBBBBB000000000000000000000000.sav  (17,200 bytes)
      referenced 1755 time(s) in Level.sav
      best-effort name guess: SomePlayer

  [2] UID: aaaaaaaa-0000-0000-0000-000000000000
      file: Players/AAAAAAAA000000000000000000000000.sav  (14,958 bytes)
      referenced 1905 time(s) in Level.sav
      best-effort name guess: AnotherPlayer

  ...

Which number is you? [1-5]: 2

Where should the fixed-up world be written? [.../some_world_folder_migrated]:
Rename the world (as it'll show in Palworld's load-game list)? Leave blank to keep the original name:

Found 1905 reference(s) to aaaaaaaa-0000-0000-0000-000000000000 in the world file.
...
Proceed with the migration? [y/N] y
```

If the name guess for your character comes back empty (not everyone is in
a guild), match yourself up by file size, or ask whoever hosted the
server. The window stays open with a summary and "Press Enter to exit"
when it's done (or if something goes wrong), so nothing flashes shut on
you.

#### 2) optioneditor — edit world settings

Point it at a `WorldOption.sav` (the migrated one from step 1, or any
world's) and pick a category, then a setting, then type a new value:

```
Path to WorldOption.sav (you can paste or drag-and-drop it here): C:\...\WorldOption.sav

Reading ... 
Loaded 119 settings.

Categories:
  [1] Difficulty & rates  (33 settings)
  [2] PvP, death & hardcore  (21 settings)
  [3] Player  (11 settings)
  [4] Base, building & guild  (23 settings)
  [5] Multiplayer & server  (31 settings)
  [0] Exit
Pick a category: 1

-- Difficulty & rates --
  [1] Difficulty = Custom
  [2] RandomizerType = None
  ...
  [7] ExpRate = 1.0
  ...
Pick a setting to edit: 7

ExpRate  (current: 1.0)
  new value (blank to cancel): 3.0
  -> ExpRate is now 3.0  (not saved yet)
```

Keep editing as many settings across as many categories as you like, then
choose `[0] Save and exit`. It'll ask where to save (defaults to
overwriting the same file) and always makes a `.bak` backup of the
original the first time it writes to a given path. Nothing is written
until you confirm.

Bools take `true`/`false` (or `y`/`n`), numbers take plain numbers, and
the handful of dropdown-style settings (`Difficulty`, `DeathPenalty`,
`LogFormatType`, `RandomizerType`) show you the valid choices and reject
anything else so you can't accidentally save a value the game won't
understand.

### Power-user mode: the command line

**hostfix** — same two steps, scriptable:

```
python hostfix.py list /path/to/world_folder

python hostfix.py migrate /path/to/world_folder \
    --old-uid aaaaaaaa-0000-0000-0000-000000000000 \
    --out /path/to/world_folder_migrated \
    --world-name "My World" \
    -y
```

`--world-name` is optional, `-y`/`--yes` skips the confirmation prompt.
Run `python hostfix.py migrate --help` for the full flag list.

**optioneditor** — inspect or change one setting at a time:

```
python optioneditor.py show /path/to/WorldOption.sav

python optioneditor.py set /path/to/WorldOption.sav ExpRate 3.0
python optioneditor.py set /path/to/WorldOption.sav bIsMultiplay true
python optioneditor.py set /path/to/WorldOption.sav Difficulty Hard
python optioneditor.py set /path/to/WorldOption.sav ServerName "My Server" --out /path/to/new_WorldOption.sav
```

`show` prints every setting (organized the same way as the wizard) so you
can find the exact field name to pass to `set`. By default `set` edits
the file in place and leaves a `.bak` backup next to it; pass `--out` to
write elsewhere instead, or `--no-backup` to skip the backup.

### Last step after migrating: copy it into your game

(Only applies to hostfix's output — optioneditor edits `WorldOption.sav`
in place, so there's nothing extra to copy for that one.)

Copy the *contents* of the output folder into a world slot under your
local save directory:

```
%LOCALAPPDATA%\Pal\Saved\SaveGames\<YourSteamID>\<AnyWorldGUID>\
```

The `WorldGUID` folder name doesn't matter — create a new one, or reuse an
existing empty-ish one if Palworld already made a placeholder for this
world (it does this if you previously joined the same server as a
client). Launch Palworld and the world should appear in your load list
with your character intact.

**Note:** `LocalData.sav` (fast-travel/map reveal progress, boss-encounter
flags, and similar per-save-slot local state) is carried over too if the
source world folder has one, so you shouldn't have to re-discover
fast-travel points you'd already found. It's copied through as-is (we
don't parse or edit its contents, just normalize the compression) rather
than being tied to your character specifically, so treat it as
best-effort. If you already have a `LocalData.sav` in your target save
slot that you'd rather keep (e.g. from previously joining the same server
as a client from this PC), back it up before copying the migrated files
over it.

## Safety

**hostfix:**
- Never writes to your source folder — always a new output folder.
- Refuses to run if the target ID already has an existing player save on
  disk (that would silently merge two different characters' data)
  unless you pass `--force`.
- Before writing, verifies the number of changed bytes exactly matches
  `(occurrences found) × (bytes that differ between the old and new
  GUID)` — if that check doesn't line up exactly, it aborts without
  writing anything.

**optioneditor:**
- Only ever touches the one file you point it at, and only the specific
  settings you change — everything else round-trips byte-for-byte.
- Always makes a `.bak` backup of the original the first time it writes
  to a given path in a session.
- Validates values before writing (numbers must parse, the handful of
  dropdown-style settings must be one of the known choices) and won't
  save anything until you explicitly confirm.

**Both:** back up your world folder before running either tool. They
edit binary save file internals; that's inherently not risk-free.

## Limitations

- hostfix is for the specific "recover my character after a dedicated
  server shuts down (or migrate co-op ↔ dedicated)" use case — it is not
  a general-purpose save editor. optioneditor only covers `WorldOption.sav`
  (world/server settings) — it doesn't touch characters, Pals, or items.
- Guild ownership can still be a little quirky after a hostfix migration
  in some cases (this is a known rough edge in every tool that does this
  kind of migration, not specific to this one) — if your base/guild
  doesn't look right after loading, that's the most likely place to
  check.
- `LocalData.sav` is copied through opaquely (see above) — if your game
  version's internal layout for it ever changes in a way that breaks this,
  hostfix will print a warning and fall back to a raw file copy rather
  than fail the whole migration.
- optioneditor's dropdown-style settings (`Difficulty`, `DeathPenalty`,
  `LogFormatType`, `RandomizerType`) are validated against the choices
  known at the time this was written — if a future game update adds new
  choices for one of these and it's rejected, please open an issue.
- Tested against real-world saves from a private dedicated server; if you
  hit a save shape either tool doesn't handle, please open an issue with
  (a redacted version of) the error.

## Credits / prior art

This builds on the excellent
[`palworld-save-tools`](https://github.com/cheahjs/palworld-save-tools)
for the base `.sav` ⇄ GVAS decompression/parsing, and takes inspiration
from [`xNul/palworld-host-save-fix`](https://github.com/xNul/palworld-host-save-fix)
and [`quadrantbs/palworld-hostfix-toolkit`](https://github.com/quadrantbs/palworld-hostfix-toolkit),
which solve the same problem for the older zlib save format. The Oodle
decompression here is possible thanks to the
[`ooz`](https://pypi.org/project/pyooz/) project's clean-room
Oodle/Kraken implementation.

## License

MIT — see `LICENSE`.
