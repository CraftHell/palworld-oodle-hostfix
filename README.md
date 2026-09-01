# palworld-oodle-hostfix

Two small tools for Palworld saves using the newer **Oodle-compressed
(`PlM`)** format (dedicated servers, since the 2026 save-format update):

- **hostfix** — migrate a character between a **dedicated server** save
  and a **single-player / co-op host** save, in either direction:
  - **migrate**: dedicated server -> single-player/co-op, keeping their
    level, stats, inventory, unlocked tech, owned Pals, guild membership,
    built structures, and fast-travel/map reveal progress intact.
  - **unhost**: single-player/co-op -> a fresh, never-joined dedicated
    server, keeping their level, stats, inventory, unlocked tech, owned
    Pals, and guild membership intact (built structures are a known
    limitation for this direction specifically -- see below).
  - **sync**: single-player/co-op -> an **already-live, already-populated**
    dedicated server. Use this instead of `unhost` whenever the server
    already has real progress for you and/or other people -- it updates
    only your own character, owned Pals, personal inventory/equipment, and
    your own guild membership, and never touches anyone else, anyone
    else's guild data, or built structures (see below for why this needs a
    different technique than `unhost`).
  - **backup**: zip up a world folder's save files (Level.sav,
    LevelMeta.sav, WorldOption.sav, LocalData.sav, every Players/*.sav)
    into one timestamped archive. Also offered automatically by the
    `unhost`/`sync` wizards right before they'd touch anything.
  - **doctor**: scan a world folder for problems this project has
    actually run into -- files that don't decompress/parse cleanly,
    duplicate IDs, a `WorldOption.sav` silently overriding settings you
    just changed in the ini, and (with `--compare`) player data that's
    missing or dropped sharply compared to another folder (an older
    backup, or the live server).
- **optioneditor** — edit a world's settings (`WorldOption.sav`):
  difficulty, day/night speed, every XP/damage/drop-rate slider, PvP and
  multiplayer toggles, and (for a dedicated server) the server
  name/password/ports/etc. Handy for tuning a world after migrating it
  with hostfix, or any time. Includes small bundled presets
  (casual/normal/hard/pvp/pve) to change a handful of related settings in
  one step, and can export a `WorldOption.sav`'s settings into a real
  `PalWorldSettings.ini` for the fields a dedicated server only ever reads
  from there.

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

That gives you a choice of the tools:

```
  [1] Migrate a dedicated-server character into single-player/co-op
      (hostfix -- keeps your level, Pals, guild, and base)
  [2] Convert a single-player/co-op world into a dedicated-server-ready save
      (hostfix unhost -- for a BRAND NEW server; see Limitations)
  [3] Update your character on an ALREADY-LIVE dedicated server
      (hostfix sync -- safe when the server already has real progress for
       you and/or others; updates your own guild membership too, but never
       touches other players, their guild data, or built structures)
  [4] Edit world settings (WorldOption.sav)
      (optioneditor -- difficulty, rates, PvP, server settings, etc.)
  [5] Back up a world folder's save files
  [6] Check a world folder for known problems
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

#### 2) hostfix unhost — prep a single-player/co-op world for a dedicated server

The reverse direction: turns your single-player or co-op world into a
save ready to drop onto a dedicated server.

**This needs one thing to happen first, and it can't be skipped:** a
dedicated-server player ID isn't something anyone can invent -- Palworld
computes it from a one-way hash of the connecting player's own Steam
account, the same value every time. There's no way to reserve an
arbitrary ID in a save file in advance and have a real player show up as
it. So before running `unhost`, you need to:

1. Start your dedicated server (it can already have other stuff on it, or
   be completely fresh -- either way, at this point it doesn't have you).
2. Connect to it **once** with the real account you're going to play as,
   then disconnect and fully stop the server. This makes the server
   compute and assign your real ID, saved as a blank, freshly-spawned
   character.
3. Now run `unhost` -- pick option `[2]` from the menu. It'll ask you to
   confirm you've done steps 1-2, then walk you through picking your
   single-player character *and* pointing it at the server's save folder
   so it can find the real ID the server just assigned you:

```
Have you already started your dedicated server, connected to it ONCE with
the real account you'll play as, and then fully stopped the server again? [y/N] y

Path to your SINGLE-PLAYER/CO-OP world folder (...): C:\...\SaveGames\76561...\WorldGUID
Found 1 player(s) in this world:

  [1] UID: 00000000-0000-0000-0000-000000000001  <- already the single-player host ID
      ...
Which one do you want to move onto the dedicated server? [1-1]: 1

Now point me at your DEDICATED SERVER's save folder (...): C:\...\PalServer\...\SaveGames\0\WorldGUID
Found 1 player(s) already on that server:

  [1] UID: 7c3a9f10-0000-0000-0000-000000000000
      file: Players/7C3A9F10000000000000000000000000.sav  (6,111 bytes)
      ...
(This should be the blank, freshly-spawned character the server made
when you connected just now -- pick whichever one is you.)
Which one is the real you on the server? [1-1]: 1

Where should the server-ready save be written? [.../MyWorld_dedicated]:
Rename the world (as it'll show in the server's world name)? Leave blank to keep the original name:

Scanning for your character/Pal records (structurally, not a blind byte search -- see the tool's docs for why)...

Found 1253 safely-verified character/Pal reference(s) to 00000000-0000-0000-0000-000000000001 in the world file.
...
Proceed with the conversion? [y/N] y
```

(In the single-player world's player list, ignore the "referenced N
time(s)" line for the single-player host ID -- it's a rough, unverified
scan, and for that specific ID it always looks much bigger than the real
number of things that end up migrated. The "safely-verified" count
printed right before the confirmation prompt is the real one; see
[Safety](#safety) below for why the two differ.)

Once it's done, copy the output folder's contents **over** your dedicated
server's save folder -- the same one you pointed at above -- replacing
the blank character the server made in step 2. Don't put it in a new
folder; the whole point is that it needs to land under the exact ID the
server is expecting.

**Important:** `unhost` assumes the destination server doesn't have
anything of yours on it yet. If your server already has real progress --
for you or for anyone else -- **use `sync` instead** (option `[3]`, next
section). A whole-world push onto a server that already has other real
players on it will destroy their data; see
[why single-player mode is a bad long-term substitute](#why-single-player-mode-is-a-bad-long-term-substitute-for-a-shut-down-server)
below for a real example of just how much damage that can do, even
without `unhost` being involved at all.

#### 3) hostfix sync — update your character on an already-live server

Say your dedicated server went down, you kept playing solo as a
stopgap using `migrate`, and now you want to bring that progress back to
the shared server (which still has your and everyone else's real data on
it). `unhost` is the wrong tool here: it's built for a brand-new server
that doesn't know you yet, and pushing your whole single-player
`Level.sav` over an already-populated one would overwrite every other
player's data with your solo save's (possibly stale, or even
degraded -- see the note linked above) copy of them.

`sync` instead surgically updates **only your own stuff**: your character
record, every Pal you currently own, your personal containers (inventory,
equipped gear, party, Palbox), and your own guild membership. It parses
both worlds properly rather than doing a raw byte patch, so it can add and
remove entries cleanly -- and it never even opens anyone else's guild
data, so every other player's guild is guaranteed byte-for-byte untouched
no matter what. Placed/built structures aren't migrated either way (see
the Limitations section).

From the menu, pick option `[3]`:

```
Path to your SINGLE-PLAYER/CO-OP world folder (...): C:\...\SaveGames\76561...\WorldGUID
Found 1 player(s) in this world:

  [1] UID: 00000000-0000-0000-0000-000000000001  <- already the single-player host ID
      ...
Which one do you want to sync onto the server? [1-1]: 1

Now point me at your LIVE DEDICATED SERVER's save folder (...): C:\...\PalServer\...\SaveGames\0\WorldGUID
Found 5 player(s) already on that server:

  [1] UID: 1e129d9a-0000-0000-0000-000000000000
      file: Players/1E129D9A000000000000000000000000.sav  (14,958 bytes)
      ...
  [2] UID: bbbbbbbb-0000-0000-0000-000000000000
      ...
Which one is you on the server? [1-5]: 1

Where should the updated server save be written? [.../WorldGUID_synced]:

Checking guild membership...
  You're already a guild member on the server -- guild membership left as-is.

CharacterSaveParameterMap: replacing 1 stale server record(s) with 68 fresh one(s) from
your single-player save (your character + every Pal you currently own).
Containers: replacing 6 item container(s) (inventory/equipment) and 2 character
container(s) (party/Palbox).

Everything else on the server -- every other player, every other player's guild
membership, and all placed/built structures -- is untouched.

Proceed and write the updated server save? [y/N] y
```

Unlike `unhost`, there's no "join once with a blank character first" step
-- you already exist on the server, so `sync` just needs to know which
existing server player is you.

If a Pal in your single-player save happens to share an ID with something
that already exists on the server under someone (or something) else, it's
reported and left alone rather than risking a duplicate or an overwrite:

```
Heads up: 4 Pal(s) in your single-player save share an ID with something that
already exists on the server under someone or something else -- these were
left as-is on the server rather than risk duplicating or overwriting them:
    d2a40080-4852-84c3-0a7c-95876462933c
    ...
```

Once it's done, copy the output folder's contents **over** your dedicated
server's save folder, same as `unhost` -- make sure the server is fully
stopped first.

#### Guild membership in `sync`

Three outcomes, depending on what's already there:

- You're already a guild member on the server (the common case) -- your
  stored display name in that one row is refreshed if it's changed; the
  guild's name, base camp, roles, and every other member are left exactly
  as they were.
- You're not a guild member on the server yet, and your single-player
  guild is just you (a solo guild) -- a new guild is created on the server
  for you under a fresh ID, keeping your guild's name but with base camp
  references cleared (buildings aren't migrated, so those IDs wouldn't
  point to anything real on the server).
- You're not a guild member on the server yet, and your single-player
  guild has other real people in it -- skipped, with a message telling you
  so. Creating that guild on the server would mean inventing state for
  players `sync` has no business touching; join or create a guild normally
  after connecting instead.

#### 4) hostfix backup / doctor — safety net and health check

Two small, read-mostly helpers that don't fit either migration direction:

**backup** (option `[5]`) zips up a world folder's save files -- `Level.sav`,
`LevelMeta.sav`, `WorldOption.sav`, `LocalData.sav`, and every
`Players/*.sav` -- into one timestamped archive next to the folder, without
touching the original. The `unhost` and `sync` wizards also offer this
automatically right before they'd write anything to your source or server
folder, so you don't have to remember to run it separately in the normal
flow -- this option is for backing something up on its own (e.g. before
using a different tool entirely, or just as a habit).

**doctor** (option `[6]`) scans a world folder for problems this project
has actually run into: files that don't decompress or structurally parse
cleanly, duplicate `InstanceId`/container IDs, and a `WorldOption.sav`
that's present (a reminder about the ini-precedence gotcha above). Point
it at a second folder -- an older backup, or the live server -- and it'll
also flag any player whose data is missing or has dropped sharply between
the two:

```
Path to the world folder to check (...): C:\...\PalServer\...\SaveGames\0\WorldGUID
Also compare against another world folder (...)? Leave blank to skip: C:\...\backup\world\2026.08.29-01.28.59

[WARN]  Player bbbbbbbb-0000-0000-0000-000000000000: had 1755 reference(s) in
.../backup/world/2026.08.29-01.28.59 but none at all in .../WorldGUID. If this
is your OWN real server ID and you're currently playing single-player under a
different ID (e.g. the single-player host ID), that's expected, not a problem
-- otherwise, this player's data doesn't appear to be present in this save at
all.
```

This doesn't diagnose *why* -- a player's data can go missing from
intentional cleanup with a different tool, an accidental copy of the wrong
folder, or the single-player degradation pattern this check was originally
built to catch (see [Limitations](#why-single-player-mode-is-a-bad-long-term-substitute-for-a-shut-down-server))
-- it just tells you something changed so you can decide whether that's
expected before pushing this data anywhere further.

#### 5) optioneditor — edit world settings

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

#### Presets

Pick **`[P]`** from the category screen to apply a small bundle of
related settings in one step instead of changing them individually:

```
  [P] Apply a bundled preset (casual/normal/hard/pvp/pve)

Available presets:
  [1] casual  -- Easier survival -- faster leveling, generous captures, no permanent death penalty.
  [2] normal  -- The game's own default difficulty-related values.
  [3] hard  -- Tougher survival -- slower leveling, stingier captures, lose equipment on death.
  [4] pvp  -- Turns on player-vs-player combat and related toggles. Doesn't change PvE rates.
  [5] pve  -- Turns player-vs-player combat off (the usual choice for a co-op-focused server).

Applied 'casual' (4 field(s) changed, not saved yet):
  Difficulty: Custom -> Casual
  ExpRate: 5.0 -> 2.0
  DeathPenalty: Item -> None
  bEnableInvaderEnemy: true -> false
```

These are deliberately small, targeted bundles -- a handful of fields
each, not a full rewrite of every setting -- so the diff is easy to
review, and nothing's saved until you go through the normal save prompt
afterward. Applying one is a starting point, not a final answer: check
the categories afterward and adjust anything you'd rather set
differently before saving. From the CLI:

```
python optioneditor.py apply-preset --list
python optioneditor.py apply-preset /path/to/WorldOption.sav casual
```

#### Turning WorldOption.sav into a PalWorldSettings.ini

Here's a Palworld dedicated-server gotcha that trips people up: **a
handful of settings are only ever read from `PalWorldSettings.ini`, never
from `WorldOption.sav`** — specifically server identity/network fields
like `ServerName`, `ServerPassword`, `AdminPassword`, `PublicPort`,
`PublicIP`, the RCON/REST API settings, and `bIsMultiplay`. Everything
else works the other way around: if `WorldOption.sav` exists in the
world's save folder, the server uses *that* instead of the ini for
gameplay settings (rates, difficulty, PvP, etc.). So if you used
optioneditor to change, say, `ServerName` and it didn't take effect on
your dedicated server even though it worked fine in single-player, this
is why — that field simply isn't read from the `.sav` on a server.

From the category screen in optioneditor's interactive mode, pick
**`[E]`** to export every setting currently in the `WorldOption.sav`
you're editing into a real `PalWorldSettings.ini`:

```
  [E] Export these settings as a PalWorldSettings.ini (for a dedicated server)

Write the ini to (...): C:\...\PalWorldSettings.ini
Wrote C:\...\PalWorldSettings.ini
```

Or from the command line:

```
python optioneditor.py export-ini /path/to/WorldOption.sav
```

Then stop your server, replace its
`<PalServer install>\Pal\Saved\Config\WindowsServer\PalWorldSettings.ini`
(or `.../LinuxServer/...` on Linux) with the generated file, and start it
back up — settings are only read at boot. A field whose value can't be
safely written into the ini's single-line format (an unrecognized
property type, or a string containing a literal double-quote) is skipped
rather than guessed at, and listed for you to set by hand; `.ini`s
tolerate missing fields fine (anything left out just falls back to the
game's default), so a couple of skipped edge cases is far safer than
risking one malformed value breaking the whole line.

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

The reverse direction -- **`--new-uid` is required and can't be made up**
(see the walkthrough above for why): start your dedicated server, connect
to it once with the real account you'll play as, fully stop the server,
then find that real ID with `list` before running `unhost`:

```
python hostfix.py list /path/to/server_save_folder
# -> note the UID it reports for the blank character you just made

python hostfix.py unhost /path/to/single_player_world_folder \
    --new-uid <the real ID from the step above> \
    --out /path/to/single_player_world_folder_dedicated \
    --world-name "My World" \
    -y
```

`--old-uid` defaults to the single-player host ID
(`00000000-0000-0000-0000-000000000001`). Run `python hostfix.py unhost
--help` for the full flag list.

If your dedicated server **already has real progress** (for you and/or
other people), use `sync` instead of `unhost` -- same idea, but it only
touches your own data:

```
python hostfix.py list /path/to/server_save_folder
# -> note your existing, already-established UID on that server

python hostfix.py sync /path/to/single_player_world_folder \
    --server-dir /path/to/server_save_folder \
    --target-uid <your real ID from the step above> \
    --out /path/to/server_save_folder_synced \
    -y
```

`--old-uid` defaults to the single-player host ID, same as `unhost`. Run
`python hostfix.py sync --help` for the full flag list.

**backup** / **doctor**:

```
python hostfix.py backup /path/to/world_folder
# -> writes world_folder_backup_<timestamp>.zip next to it

python hostfix.py doctor /path/to/world_folder
python hostfix.py doctor /path/to/world_folder --compare /path/to/an/older/backup_or_the_live_server
```

**optioneditor** — inspect or change one setting at a time:

```
python optioneditor.py show /path/to/WorldOption.sav

python optioneditor.py set /path/to/WorldOption.sav ExpRate 3.0
python optioneditor.py set /path/to/WorldOption.sav bIsMultiplay true
python optioneditor.py set /path/to/WorldOption.sav Difficulty Hard
python optioneditor.py set /path/to/WorldOption.sav ServerName "My Server" --out /path/to/new_WorldOption.sav

python optioneditor.py export-ini /path/to/WorldOption.sav
# -> writes PalWorldSettings.ini next to it; see "Turning WorldOption.sav
#    into a PalWorldSettings.ini" above for why you'd want this
```

`show` prints every setting (organized the same way as the wizard) so you
can find the exact field name to pass to `set`. By default `set` edits
the file in place and leaves a `.bak` backup next to it; pass `--out` to
write elsewhere instead, or `--no-backup` to skip the backup.

### Last step after migrating: copy it into your game

(Only applies to hostfix's output — optioneditor edits `WorldOption.sav`
in place, so there's nothing extra to copy for that one.)

**For `migrate` (-> single-player/co-op):** copy the *contents* of the
output folder into a world slot under your local save directory:

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

**For `unhost` (-> a dedicated server):** copy the *contents* of the
output folder **over** your dedicated server's save folder, e.g.:

```
<PalServer install>\Pal\Saved\SaveGames\0\<WorldGUID>\
```

This is the same folder you already connected to once and pointed the
tool at (see the walkthrough above) -- copying in replaces the blank
character the server made for you, under the real ID it's expecting.
Make sure the server is fully stopped first.
`LocalData.sav` is intentionally not copied for this direction —
dedicated servers don't use it; each player who joins later builds up
their own copy on their own PC.

**For `sync` (-> an already-live dedicated server):** same as `unhost` --
copy the *contents* of the output folder **over** the server's save
folder, with the server fully stopped first. The output only ever
contains `Level.sav` and your own `Players/<uid>.sav` — no other player's
file, `LevelMeta.sav`, or `WorldOption.sav` are touched or written, since
`sync` never has a reason to change them.

## Safety

**hostfix migrate (dedicated server -> single-player/co-op):**
- Never writes to your source folder — always a new output folder.
- Refuses to run if the target ID already has an existing player save on
  disk (that would silently merge two different characters' data)
  unless you pass `--force`.
- Uses a single global find-and-replace of the old player ID's raw 16
  bytes across the world file. This is safe here specifically because a
  dedicated-server player ID has 4 fully random bytes (~4 billion
  possible values) — across a save file with hundreds of thousands of
  unrelated 16-byte fields, the odds of a coincidental false match are
  astronomically low.
- Before writing, verifies the number of changed bytes exactly matches
  `(occurrences found) × (bytes that differ between the old and new
  GUID)` — if that check doesn't line up exactly, it aborts without
  writing anything.

**hostfix unhost (single-player/co-op -> dedicated server):**
- Requires a real `--new-uid` and refuses to guess one. A dedicated-server
  player ID is a one-way hash of the connecting player's own Steam
  account, computed by the game itself -- not a value a save file can
  reserve or a tool can invent. (An earlier version of this tool *did*
  generate a random one, which produced saves nobody could actually log
  in as — the game just created a new character instead. Fixed by
  requiring the real ID, found via `list` after joining the server once.)
- Never writes to your source folder — always a new output folder.
- Refuses to run if the target ID already has an existing player save on
  disk, same as `migrate`, unless you pass `--force`.
- Does **not** use a blind global find-and-replace. The ID this direction
  moves *away from* by default (the special single-player host ID,
  `00000000-0000-0000-0000-000000000001`) is almost all zero bytes, and
  that exact pattern turns out to coincidentally match thousands of
  unrelated all-zero-padded fields elsewhere in a real save (measured on
  a real ~47MB world: 18,645 coincidental matches, versus 0 for a proper
  random ID) — a blind replace using this ID would silently corrupt
  unrelated Pals, items, and containers. Instead, `unhost` only ever
  touches byte ranges it can *structurally* verify — by their exact
  Unreal serialization layout, or by parsing the character/Pal ownership
  map itself — really are your character's own data. See the
  `scoped_unhost_swap` function in `hostfix.py` for the full technique if
  you're curious.
- Because of that, it only migrates what it can safely verify: your
  character's own record, every Pal you currently own (level, stats,
  inventory, unlocked tech, and Pals are all covered), and your guild
  membership (see `palgroup.py` — a real, decodable structure, just not
  the one `palworld-save-tools` ships a working decoder for anymore).
  Structures you'd built are **not** migrated by this direction — see
  Limitations below.
- Same before-writing byte-count verification as `migrate`.

**hostfix sync (single-player/co-op -> an already-live dedicated server):**
- Never writes to either your single-player world or your server's save
  folder — always a new output folder.
- Never does a whole-file replace or touches the destination `Level.sav`
  as an opaque blob. It parses both worlds structurally (via
  `palworld-save-tools`' JSON dump/load round-trip, verified byte-for-byte
  identical on real production saves when left unmodified) and only
  replaces the entries structurally keyed to the target player: their own
  `CharacterSaveParameterMap` record, every Pal entry keyed to their
  `PlayerUId`, and their personal `ItemContainerSaveData`/
  `CharacterContainerSaveData` entries (matched by container ID read
  cleanly off each side's own `Players/<uid>.sav`, which is not opaque the
  way the map's `RawData` blobs are).
- Every other player and `MapObjectSaveData` (buildings) in the
  destination are never even inspected for changes — verified on real
  production data to come out byte-for-byte / structurally identical
  before and after a sync. `GroupSaveDataMap` (guild) gets one narrow,
  separate pass that only ever touches the target player's own row, or
  adds one brand-new entry for them — see "Guild membership in `sync`"
  above.
- If a Pal being added from your single-player save shares an `InstanceId`
  with something already present in the destination under someone (or
  something) else, it's skipped and reported rather than duplicated or
  overwritten.
- Re-parses its own output with `palworld-save-tools` before considering
  the run successful, and checks for duplicate `InstanceId`/container `ID`
  values in the result.

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
- Guild ownership can still be a little quirky after a `migrate` in some
  cases (this is a known rough edge in every tool that does this kind of
  migration, not specific to this one) — if your base/guild doesn't look
  right after loading, that's the most likely place to check.
- **Neither `unhost` nor `sync` migrates placed/built structures
  (`MapObjectSaveData`).** This one's a harder wall than the old blanket
  "guild + buildings" limitation used to suggest: guild membership turned
  out to have a real, decodable structure (see `palgroup.py`, and "Guild
  membership in `sync`" above) once we stopped relying on
  `palworld-save-tools`' own bundled decoder for it (which is stale
  against the current save format and throws on every real guild record
  tested). Buildings don't have the same escape hatch — parsing
  `MapObjectSaveData` against real save data fails outright ("Warning: EOF
  not reached"), and even the actively-maintained reference
  implementation this project's guild decoder is ported from treats that
  field as routinely liable to fail to parse and falls back to raw bytes
  per object rather than promising a clean decode. There's no reliable way
  to migrate structure ownership right now. Your character, their
  level/stats/inventory/tech, every Pal they own, and (as of this version)
  guild membership all transfer over fine; you'll likely need to rebuild
  or reclaim (via server admin commands, if your host supports them) any
  base you'd already built, on the new server.
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

### Why single-player mode is a bad long-term substitute for a shut-down server

If your dedicated server is down and you keep playing solo with `migrate`
as a stopgap, don't treat that single-player save as a safe, lossless
stand-in for the shared world for very long. In testing, a real
single-player save that had only been played solo for about two days
showed the *other*, non-connectable players' owned-Pal data measurably
degraded compared to the same players on the last real server save --
raw reference counts for four different real players dropped
(for example, one went from 345 references down to 5; another from 5,000
down to about 2,943) even though nothing about those players' data should
have changed at all during solo play. The active player's own data stayed
intact throughout.

This isn't a bug in this tool -- it's Palworld's single-player save
pruning data for accounts it doesn't consider "present," and it's why
`unhost` explicitly assumes a brand-new server: pushing a solo save's
whole `Level.sav` back over a server that already has other real people's
progress on it would carry this degradation into their data too, on top
of overwriting it outright. If your server already has anyone else's real
progress (or even just older progress of your own you want to keep), use
`sync` instead, which never reads or writes anything belonging to another
player in the first place -- so this degradation, wherever it happens to
live in your solo save, can never reach the server.

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
