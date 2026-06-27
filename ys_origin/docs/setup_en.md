# Ys Origin Setup Guide

## Requirements

- Windows 10/11
- Ys Origin (Steam), `yso_win.exe`, version **1.1.1.0**
- Python 3.11+ (only needed to run the client from source)
- An Archipelago installation (for generating and hosting), or access to a host

## Installing the apworld

1. Zip the `ys_origin/` directory into `ys_origin.apworld`
   (the archive's top-level folder must be `ys_origin/`). From the repo root:

   ```powershell
   Compress-Archive -Path ys_origin -DestinationPath ys_origin.zip
   Rename-Item ys_origin.zip ys_origin.apworld
   ```

2. Copy `ys_origin.apworld` into your Archipelago `custom_worlds/` folder
   (older installs: `lib/worlds/`).

3. Confirm it loads: the Archipelago Launcher → "Generate" should now list
   **Ys Origin** as an available game.

## Generating a seed

1. Create a YAML for Ys Origin (the Launcher's "Generate Template Options"
   produces one once the apworld is installed). Options:
   - `character`: `yunica` / `hugo` / `toal`
   - `start_with_double_jump`: `true` / `false`
   - `include_equipment`: `true` / `false`
   - `goal`: `defeat_darm` / `defeat_all_bosses`
2. Generate and host as usual.

## Running the client

1. Launch Ys Origin and load your save.
2. From the repo root, run the client (it needs the Archipelago source on the
   path — set `AP_ROOT` to your Archipelago checkout):

   ```powershell
   $env:AP_ROOT = "C:\path\to\Archipelago"
   python -m client.ap_client <host:port> [password]
   ```

   Run as Administrator if attaching to the game fails — reading another
   process's memory requires it on some systems.

3. The client attaches to `yso_win.exe`, connects to the AP server, and begins
   polling. Received items are written into the game; checks are sent as you
   find them.

## Discovering offsets (developers)

Many memory offsets are still being reverse-engineered. The scanner helps map
them against a live game:

```powershell
python -m tools.scan
```

See the repo README for the scanner workflow.

## Troubleshooting

- **"Could not import Archipelago's CommonClient"** — set `AP_ROOT` to your
  Archipelago checkout, or run the client from inside that tree.
- **"failed to attach"** — the game isn't running, or you need Administrator.
- **Items not applying / "Offset ... is not mapped yet"** — that item's memory
  offset hasn't been discovered. Map it with `tools/scan.py` and add it to
  `client/offsets.py`.
