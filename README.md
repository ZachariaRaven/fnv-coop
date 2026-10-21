# Fallout: New Vegas co-op: story sync (developer preview)

[![Discord](https://img.shields.io/badge/Discord-chat%20and%20help-5865F2?logo=discord&logoColor=white)](https://discord.gg/EXGB49R82t)

> **Status (October 2026): developer preview, about 40% of the way to full co-op. See the 10-step roadmap below.**
>
> | Area | Where it stands |
> |---|---|
> | Sync core (protocol, merge rules, rejoin, rules engine) | built, covered by game-free tests and a 4-player soak |
> | Co-op rules for every story quest | written for 481 quests |
> | Engine hooks that record world changes | cover about two thirds of the script commands the game uses |
> | Two game copies on one PC for automated tests | working |
> | Quest stage sync between two real game copies | working: stages arrive exactly once, with no scripts re-run on the client |
> | Goodsprings stage sweep | 65 of 104 stages pass |
> | NV:MP alongside the plugin | both clients connect and see each other |
>
> There is no player release yet. Progress updates land here.

Play the whole **Fallout: New Vegas** story together: 2 to 4 players, one shared world, one shared quest
state. This repo is the **story layer**: an xNVSE plugin plus a game-free sync core that keeps quests,
objectives, dialogue results, reputation and karma identical on every machine.

[![Support me on Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/cranebeaker)

Avatars, enemies and combat get their own sync layer (steps 1 to 4 of the roadmap). Until that ships, **NV:MP**,
the existing New Vegas multiplayer mod by the NV:MP team, can run alongside. We do not modify or redistribute it.

## Roadmap: 10 steps to full co-op

After these ten steps the whole game plays in co-op: story, enemies, combat, loot and the world, for 2 to 4 players.
A step marked **Claimed** is being worked on right now. Want to take one? Open an issue and it gets claimed for you.
Who: Track A is the sync core (the maintainer). Track B is in-game testing, split-screen and real-time VATS (a contributor,
see [docs/CONTRIBUTING-HANDOFF.md](docs/CONTRIBUTING-HANDOFF.md)). What Track B works on now and next is in the [Track B status issue](../../issues/17).

| # | Step | What it means when you play | Who | Status |
|---|---|---|---|---|
| 1 | Enemy and NPC sync | One PC runs each enemy's AI and everyone sees a smooth copy. Hits count once, deaths happen once, encounters spawn once, and enemy attacks, flinches and death poses look right on every screen. | A | **Claimed**: code written, finishing |
| 2 | Enemy sync in the real game | The two-copy fight, ownership handoff, spawn and corpse-loot tests pass on a real PC, and the feature is switched on by default. | B, then A fixes | Next |
| 3 | Player avatars | You see your friends as characters that walk, run, aim and animate, without needing NV:MP. | A, B tests | **Claimed**: finishing the code |
| 4 | Full combat | Real-time VATS for every player, projectiles and explosions, companions in fights, crime and faction hostility. | A and B | **Claimed**: combat and real-time VATS being written |
| 5 | Loot, items and containers | Shared containers and corpses, item drops and pickups, and no duplicated items. | A | **Claimed**: being written |
| 6 | World, time and travel | Doors, terminals, switches and traps; time and weather; fast travel, waiting and sleeping by party vote; saving and rejoining. | A | **Claimed**: finishing the code |
| 7 | Desync check and repair | Every area checks itself every few seconds and fixes any difference it finds. | A | **Claimed**: extending to enemies, loot and the world |
| 8 | The whole main story in game | Every main questline swept stage by stage with two game copies, from Goodsprings to the endings. | A and B | **Claimed**: closing script gaps and writing sweeps for every questline (Goodsprings: 65 of 104 stages pass) |
| 9 | Every side quest and DLC | All side quests, companions and the four DLCs, played through by two-copy test bots. | A and B | **Claimed**: sweeps and test bots being written |
| 10 | Player release 1.0 | Installer, one-click host and join, split-screen on one PC, player guide. | A and B | **Claimed**: installer, launcher, split-screen and player guide being written |

## Play in three steps

**There is no player release yet.** This is a developer preview without an installer. When there is one, it
will be:

1. Download the zip from [Releases](../../releases) and unzip it.
2. Run the installer. It finds your New Vegas install and adds the plugin next to xNVSE.
3. Host starts a session, friends join.

Until then, to try it you need to build it yourself (see "Building and tests").

## Status

Everything listed as proven is checked by automated tests; nobody drives the game by hand. See
[docs/STATUS.md](docs/STATUS.md) for the details.

Proven by automated tests (game-free suites plus in-game runs with a remote-control test API):

- The sync protocol and merge rules (stage = max, flags = OR, kills = union), with a simulator of 2 to 4 clients
  on a lossy, reordering link, seeded chaos fuzzing and a 1-hour 4-player soak that converged.
- Capture and replay of a quest-script batch in the real game: the host runs a stage script, our hooks record
  every world change it causes as one transaction, a guest applies it **without re-running the script**, and
  the snapshots match.
- Multiple game instances on one PC (the single-instance lock is worked around inside our own process), so
  tests can run a real host and a real client.
- Host stage change to client over our own ENet channel, baseline join, save/load rebase, barrier release and
  the version handshake, in the real game.

## Known issues

- Developer preview only: no installer, no player documentation.
- Many suites need your own game install or data generated from it. Without it they skip with a message.
- Windows and the Steam or GOG release of New Vegas 1.4.0.525 only.

## How it works

- **Effect capture, not script replay.** Only the host runs quest and dialogue result scripts. While one runs,
  hooks record every primitive world change it causes (stage, objective, global, ref enable/disable, item,
  reputation, karma, quest start/stop, move). That list ships as one ordered transaction, and clients apply it
  directly. No duplicate rewards, no missing side effects, and every transaction doubles as a test fixture.
- **Sync core** (`synccore/`): engine-agnostic C++ with the wire format, merge rules, echo guard, rejoin and
  late-join replay, desync digests and a rules engine for per-quest policies. Design in
  [docs/protocol.md](docs/protocol.md) and [docs/MASTER-PLAN.md](docs/MASTER-PLAN.md).
- **Plugin** (`plugin/`): the xNVSE plugin `nvcoop.dll`, with a remote-control bridge for tests, snapshots,
  engine hooks, dialogue state machines and a co-op runtime.
- **Quest data** (`tools/questdata`): a parser that reads your own game's `.esm` files and works out, for every
  quest stage, what a result script does and how risky it is for co-op.
- **Policies** (`docs/quest-policies/rules.json`): our own per-quest co-op rules (tether the party, share karma,
  rewards to everyone, initiator picks the dialogue).

Building and running the tests: see below and [docs/03-testing.md](docs/03-testing.md).

## Building and tests

You need Windows, Python 3.11+, and for the C++ parts CMake and the MSVC x86 toolchain. xNVSE sources go in
`third_party/NVSE` (see [third_party/README.md](third_party/README.md)).

```
python -I tools/test_all.py --quick     # game-free suites that need no compiler
python -I tools/test_all.py             # everything, including the C++ suites
```

What cannot run without your own game or mods (these suites print `SKIP <name>: <reason>`):

- Quest data tests: run against your install if it is found at the default Steam path, otherwise they skip.
  Generate the data other tools use with `python -I tools/questdata/run_all.py`.
- Coverage and snapshot tests: the parts that need generated game data skip; the rest run.
- NV:MP signature and patcher tests: need your own NV:MP 9.4 client files under `third_party/nvmp/`.
- Dialogue and questline reports: regenerated from your game data; the generated files are not in this repo.
- In-game tests (`tools/ingame_gate.py`, `plugin/coop/tools/`): need a licensed install and `tools/runner`.

This repo ships no game data. Dialogue text, quest dumps and script listings are left out on purpose; run the
tools in `tools/questdata` against your own install to regenerate them.

## Part of coop-anything

This is one entry in [coop-anything](https://github.com/cranebeaker/coop-anything), a community playbook and
directory for adding co-op to single-player classics. If your AI agent helped you fix something here, the
playbook explains how to send it back.

## Credits

- **xNVSE** (the New Vegas Script Extender team): https://github.com/xNVSE/NVSE
- **NV:MP** by the NV:MP team (nv-mp.com). Not included; we patch it only at runtime.
- **ENet** by Lee Salzman: https://github.com/lsalzman/enet (bundled in `third_party/enet`, MIT licensed).
- **Nucleus Co-op** (SplitScreen.Me): the techniques for running several copies of a single-instance game on one
  PC: https://github.com/SplitScreen-Me/splitscreenme-nucleus
- Fallout: New Vegas by Obsidian Entertainment and Bethesda Softworks. You need your own copy.

## License

The code and docs in this repository are MIT licensed, see [LICENSE](LICENSE). ENet keeps its own license.
Fallout: New Vegas, xNVSE and NV:MP are the property of their owners and are not included.
