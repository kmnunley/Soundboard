> [!IMPORTANT]
> This repository is entirely AI-developed and is being used as a testbed to evaluate the coding capabilities of various LLMs. PRs from the general public are disabled.

# Soundboard

Desktop soundboard app built with Python, PyQt6, and pygame.

## Features

- Circular sound buttons with grouped folders
- Optional compressor controls for playback shaping
- Optional Windows smart unmute/remute support via `pycaw`

## Requirements

- Python 3.10+ (3.12 recommended)
- Windows (primary target)

## Setup

1. Create a virtual environment:

```powershell
python -m venv .venv
```

2. Activate it:

```powershell
.\.venv\Scripts\Activate.ps1
```

3. Install dependencies:

```powershell
pip install -r requirements.txt
```

## Run

```powershell
python soundboard.py
```

## Sound Files

- Put audio files under `sounds/`
- Button labels are derived from file names
- Use subfolders in `sounds/` to organize groups

`sounds/readme.txt` includes the same folder-specific notes.

## Git Notes

This repo ignores:

- local virtual environments (`.venv/`, `venv/`)
- local runtime/config files (`settings.json`, `.processed_cache/`)
- local audio content under `sounds/`

## LLM Usage Log

| LLM | Version |
| --- | --- |
| ChatGPT Codex | GPT-5.3-Codex |
