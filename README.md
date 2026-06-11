# P4 Cleaner

P4 Cleaner is a tool to monitor your Perforce proxy cache and automatically remove the oldest unused files to reclaim disk space.

## Features

- Scans and cleans Perforce proxy cache folders to free up disk space.
- Two cleaning modes: **Drive mode** (free up disk % on the whole drive) and **Folder mode** (reduce cache folder to N% of its current size).
- Supports **dry-run** simulation before any files are deleted.
- Configurable file exclusion list (patterns/filenames to never delete).
- GUI with light/dark mode toggle, live progress and log output.
- Full CLI / headless support for automation and scheduled jobs.
- Pre-built binaries for **Windows** and **Linux** — no Python installation required.
- Automated GitHub Actions workflow builds and attaches both binaries to every release.

---

## Download

Download the latest pre-built binary from the [Releases](https://github.com/GEngines/perforce_cache_cleaner/releases/latest) page:

| Platform | File |
|---|---|
| Windows | `P4CacheCleaner-windows.exe` |
| Linux | `P4CacheCleaner-linux.bin` |

Both binaries bundle all required resources — no Python installation needed.

---

## Usage

### GUI (Windows / Linux)

**Windows:**
1. Download `P4CacheCleaner-windows.exe` from the [latest release](https://github.com/GEngines/perforce_cache_cleaner/releases/latest).
2. Double-click to launch the GUI.

**Linux:**
1. Download `P4CacheCleaner-linux.bin` from the [latest release](https://github.com/GEngines/perforce_cache_cleaner/releases/latest).
2. Make it executable and run:
    ```sh
    chmod +x P4CacheCleaner-linux.bin
    ./P4CacheCleaner-linux.bin
    ```

### From Source (Python)

1. **Clone the repository:**
    ```sh
    git clone https://github.com/GEngines/perforce_cache_cleaner.git
    cd perforce_cache_cleaner
    ```

2. **Install dependencies:**
    ```sh
    pip install -r requirements.txt
    ```

3. **Run the script:**
    ```sh
    python p4p_cleaner_advanced.py
    ```

---

## Command-Line Usage

All options work identically on Windows and Linux, using the binary or the Python script directly.

### Examples

```sh
# Windows binary
P4CacheCleaner-windows.exe --path "D:\p4proxy\cache" --drive-mode --low 20 --high 30 --dry-run

# Linux binary
./P4CacheCleaner-linux.bin --path /mnt/p4proxy/cache --drive-mode --low 20 --high 30 --dry-run

# From source (any platform)
python p4p_cleaner_advanced.py --path /mnt/p4proxy/cache --drive-mode --low 20 --high 30 --dry-run
```

### CLI Options

| Option | Description |
|---|---|
| `--path <folder>` | Path to the cache directory (**required for CLI**) |
| `--drive-mode` | Clean based on whole-drive free percentage (default) |
| `--folder-mode` | Clean to keep N% of the cache folder size |
| `--low <int>` | Minimum disk free % before cleaning starts (drive mode) |
| `--high <int>` | Target disk free % after cleaning (drive mode) |
| `--percent <int>` | Percent of cache folder to retain (folder mode) |
| `--dry-run` | Simulate cleaning — no files are deleted |
| `--show-excluded` | List currently excluded files/patterns |
| `--add-excluded <pattern>` | Add a filename or pattern to the exclusion list |
| `--remove-excluded <pattern>` | Remove a filename or pattern from the exclusion list |
| `--edit-excluded` | Interactively edit the exclusion list |

### More Examples

```sh
# Dry run, drive mode
./P4CacheCleaner-linux.bin --path /mnt/p4proxy/cache --drive-mode --low 15 --high 25 --dry-run

# Keep only 70% of cache folder (folder mode)
./P4CacheCleaner-linux.bin --path /mnt/p4proxy/cache --folder-mode --percent 70

# Show excluded files
./P4CacheCleaner-linux.bin --show-excluded

# Add a pattern to exclusions
./P4CacheCleaner-linux.bin --add-excluded "*.lbr"
```

---

## Building Locally

### Prerequisites

- Python 3.10 or newer
- [PyInstaller](https://pyinstaller.org/en/stable/)

```sh
pip install pyinstaller
pip install -r requirements.txt
```

### Windows

```sh
pyinstaller --onefile -w --icon=resources\icons\icon.ico --add-data "resources;resources" --name P4CacheCleaner-windows p4p_cleaner_advanced.py
# Output: dist/P4CacheCleaner-windows.exe
```

### Linux

```sh
pyinstaller --onefile --add-data "resources:resources" --name P4CacheCleaner-linux p4p_cleaner_advanced.py
mv dist/P4CacheCleaner-linux dist/P4CacheCleaner-linux.bin
chmod +x dist/P4CacheCleaner-linux.bin
# Output: dist/P4CacheCleaner-linux.bin
```

---

## Automated Builds & Releases

The GitHub Actions workflow (`.github/workflows/build-binaries.yml`) automatically builds both binaries and attaches them to each published release.

When you publish a new release on GitHub, the workflow:
1. Runs on both `windows-latest` and `ubuntu-latest` runners in parallel.
2. Installs Python 3.10, PyInstaller, and all dependencies.
3. Builds the platform binary with PyInstaller.
4. Uploads both binaries as release assets.

---

## Contributing

- Fork the repository and create a feature branch.
- Ensure `requirements.txt` is updated for any new dependencies.
- Open a pull request with a clear description of your changes.

---

## License

[GPL-3.0](LICENSE)

![image](https://github.com/user-attachments/assets/35d80d32-ed1e-4e4f-a00a-32876cd989d4)
