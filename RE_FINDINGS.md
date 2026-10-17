# Reverse-engineering notes

The detailed research notes behind this project (the memory map, the item and flag arrays, how each check
is detected) are kept with the rest of the Ys Origin research, which is not published.

For working on the code here you do not need them:

- **Game addresses and structures** the mod uses are named and commented where they are used, mostly in
  [`mod/src/hook_ap.cpp`](mod/src/hook_ap.cpp) (items, flags, the player's stats) and
  [`mod/src/hook_vm.cpp`](mod/src/hook_vm.cpp) (the event-script hooks).
- **Items, locations and the logic** are data in [`ys_origin/data/`](ys_origin/data/), with notes on the room
  graph in [`ys_origin/data/room_graph_notes.md`](ys_origin/data/room_graph_notes.md).
- **The tools** in [`tools/`](tools/) each say in their header what they read from the game and why.

Questions are welcome in the issues.
