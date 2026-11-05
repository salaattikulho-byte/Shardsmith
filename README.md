# Shardsmith – reforge your fate

A save editor for **Stoneshard** by LazarusOne.
Fan-made, not affiliated with or endorsed by Ink Stains Games.

**Download:** [Nexus Mods](LINK_TO_YOUR_NEXUS_PAGE). Players should get Shardsmith from Nexus. This repository is the source code, so you can see what the program does and build it yourself.

## What it does

- Edit gold, stats, level, XP, stat and ability points
- Equipment view with item stats and weapon set switching
- Enchant items, using the game's own enchant values
- Spawn, duplicate, repair, identify, uncurse and delete items, and move them between backpack and caravan stash
- Learn, unlearn and unlock skills
- Save Chronicle: gold and level charts and what changed between saves
- Works with both regular and modbranch Stoneshard, and with bigger backpacks from inventory mods

## What it touches

- Reads and writes your Stoneshard saves in `%LOCALAPPDATA%\StoneShard`. It makes a backup (`data.sav.bak_<date>`) every time it saves.
- Reads item pictures and names from your own Stoneshard install. It never changes game files.
- Keeps its settings and cache in `%LOCALAPPDATA%\Shardsmith`.
- Makes no network connections.

No game files are included in this repository.

## Building the exe

1. Install [Python 3.12](https://www.python.org/downloads/) for Windows.
2. Put `shardsmith.py`, `shardsmith.ico` and `build_shardsmith_exe.bat` in one folder.
3. Double-click `build_shardsmith_exe.bat`. It installs PyInstaller and builds the exe into the **Shardsmith share** folder.

Or build it by hand:

```
py -m pip install pyinstaller
py -m PyInstaller --onefile --windowed --name Shardsmith --icon shardsmith.ico shardsmith.py
```

The exe is created in the `dist` folder. To run it without building, use `py shardsmith.py`.

## License

Copyright (c) 2026 LazarusOne. All rights reserved.

The source is public so people can see what Shardsmith does and build it themselves. That doesn't give anyone permission to modify, re-upload or redistribute it, in whole or in part. See [LICENSE](LICENSE). For permission, message LazarusOne on Nexus Mods.

Made with the help of Claude (Anthropic).
