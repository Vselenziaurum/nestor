# NESTOR: Skif's sixth sense on a local AI

Source code of `nestor.exe`, the launcher and bridge of the NESTOR mod for
S.T.A.L.K.E.R. 2: Heart of Chornobyl. A language model on the player's graphics
card plays Skif's gut feeling: short warnings with a direction, a chat on F4,
memory between sessions.

The game-side part of the mod (a Blueprint world subsystem built with the
official Zone Kit) ships as cooked `.pak/.ucas/.utoc` files together with this
program on Nexus Mods: https://www.nexusmods.com/stalker2heartofchornobyl/mods/2929

Исходный код nestor.exe, лаунчера мода NESTOR для S.T.A.L.K.E.R. 2. Сборка ниже, раздел Build.

## What nestor.exe does

- **Launcher window.** Checks the game folder (Steam libraries), the six mod
  files in `~mods`, the graphics card (`nvidia-smi`, or the Windows registry for
  other cards), the downloaded engine and model. "Test model" starts the model
  once without the game and measures video memory. "Play" starts the game
  through Steam (`steam://rungameid/1643320`), or runs the game command passed
  by the Steam launch options (`"...\nestor.exe" %command%`).
- **Downloads only when the player presses Download**, and only these pinned
  files (URLs, sizes and SHA-256 in `nestor.py`, `FILES`); every file is checked
  against its SHA-256 before use:
  - llama.cpp b11403, official Windows builds from
    https://github.com/ggml-org/llama.cpp/releases/tag/b11403
    (CUDA 12.4 with the cudart package, or Vulkan);
  - Qwen3.5 9B or 4B, Q4_K_M GGUF, from Hugging Face (lmstudio-community).
- **While the game runs** it starts `llama-server` on `127.0.0.1:8081` (local
  only), reads the game state that the mod writes into a save slot
  (`%LOCALAPPDATA%\Stalker2\Saved\SaveGames\NS_Out.sav`) and writes the reply,
  plus allowed console commands, into `NS_In.sav`, which the mod reads and then
  deletes (`bridge.py`). F4 is a global hotkey (`RegisterHotKey`) for a small
  chat window (`askbox.py`).
- **Console commands** go to the game only on the player's direct request in
  the chat, from a fixed list in `actions.py`: give items, ammo or coupons;
  weather and time of day; start or stop an emission; spawn creatures or psy
  phantoms; repair the gun in hand; make people around friendly or hostile;
  sleep; move forward.
- **Everything stays in its own folder:** `engine\`, `models\`, `memory\`
  (plain-text memory of the conversations), `nestor.json` (settings),
  `nestor.log`.
- **No telemetry.** Apart from the two downloads above the program does not go
  online. The donation link in the window opens in the browser only on click.

## Files

| file | what |
|---|---|
| `nestor.py` | launcher window, checks, downloads, game start |
| `bridge.py` | game state, reasons to speak, chat answers, memory, engine fallback |
| `engine.py` | starts and stops `llama-server` |
| `lang.py` | English and Russian texts and prompts |
| `actions.py` | the fixed list of console commands ("the gut's hands") |
| `memory.py` | memory and diary per Steam profile |
| `askbox.py` | the F4 chat window |
| `items_ru.json`, `items_en.json`, `sparemag_weapons.json` | item names and weapon calibers taken from the game data |
| `measure.py` | developer tool for performance measurements, not part of `nestor.exe` |
| `build.ps1`, `requirements-build.txt` | build |

## Build

Windows 10/11 x64, Python 3.13 x64, in this folder:

```
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File build.ps1
```

Result: `dist\NESTOR\nestor.exe` with `dist\NESTOR\_internal\` next to it
(PyInstaller one-folder build, windowed, no console). The release archive on
Nexus contains this folder plus the six mod files and a README.

PyInstaller programs are sometimes flagged by antivirus heuristics; this
repository is the full source the program is built from.

## License

MIT, see `LICENSE`. llama.cpp (MIT) and the Qwen3.5 models (Apache 2.0) are
downloaded at run time and are not part of this repository.
