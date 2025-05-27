# P4 Cleaner

P4 Cleaner is a tool to monitor your files and clean up old files to recover disk space.

## Features

- Scans and cleans Perforce cache folders to free up disk space.
- Bundled resources for configuration or additional data.
- Provides a ready-to-use Windows executable with a custom icon (no Python installation required).
- Automated GitHub Actions workflow to build and attach the Windows `.exe` to releases.

---

## Download

- Download the latest Windows executable [`P4CacheCleaner.exe`](https://github.com/GEngines/perforce_cache_cleaner/releases/latest) from the [Releases](https://github.com/GEngines/perforce_cache_cleaner/releases) page.
- The executable bundles all required resources and uses a custom icon.

---

## Usage

### As a Windows Executable

1. Download `P4CacheCleaner.exe` from the [latest release](https://github.com/GEngines/perforce_cache_cleaner/releases/latest).
2. (Optional) Place the executable in the desired directory.
3. Double-click to run, or launch from the command line.

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

## Command-Line Usage

You can use the EXE (`P4CacheCleaner.exe`) or the advanced Python script (`p4p_cleaner_advanced.py`) from the command line for automation or headless environments.

### Example (EXE or Python script)

```sh
P4CacheCleaner.exe --path "D:\p4proxy\cache" --drive-mode --low 20 --high 30 --dry-run
# or with Python:
python p4p_cleaner_advanced.py --path "D:\p4proxy\cache" --drive-mode --low 20 --high 30 --dry-run
```

### Main CLI Options

- `--path <folder>`: Path to the cache directory to clean (**required for CLI**)
- `--drive-mode`: Clean based on disk free percentage (default)
- `--folder-mode`: Clean to keep N% of cache folder
- `--low <int>`: Minimum disk free percent (drive mode)
- `--high <int>`: Target disk free percent after cleaning (drive mode)
- `--percent <int>`: Percent of cache folder to keep (folder mode)
- `--dry-run`: Simulate cleaning (no files deleted)
- `--show-excluded`: List files/patterns currently excluded
- `--add-excluded <pattern>`: Add a filename/pattern to be excluded
- `--remove-excluded <pattern>`: Remove a filename/pattern from exclusions
- `--edit-excluded`: Interactively edit the excluded files list

### More Examples

- **Dry run, drive mode**
    ```sh
    P4CacheCleaner.exe --path "D:\p4proxy\cache" --drive-mode --low 15 --high 25 --dry-run
    ```
- **Clean keeping only 70% of cache folder (folder mode)**
    ```sh
    P4CacheCleaner.exe --path "D:\p4proxy\cache" --folder-mode --percent 70
    ```
- **Show excluded files**
    ```sh
    P4CacheCleaner.exe --show-excluded
    ```
- **Add a file to exclusions**
    ```sh
    P4CacheCleaner.exe --add-excluded "*.lbr"
    ```

---

## Building the Windows Executable

### Prerequisites

- Python 3.10 or newer
- [PyInstaller](https://pyinstaller.org/en/stable/)
- Your custom icon, e.g., `app_icon.ico`, and a `resources` folder in the project root

### Build Steps (Manual)

```sh
pip install pyinstaller
pyinstaller --onefile -w --icon=app_icon.ico --add-data "resources;resources" --name P4CacheCleaner p4_cleaner_advanced.py
```

- The resulting `P4CacheCleaner.exe` will appear in the `dist/` directory.
- The executable will include all files from the `resources` folder and use the specified icon.

---

## Automated Builds & Releases

This repository uses GitHub Actions to automatically build and attach the Windows executable to each release.

- When you publish a new release on GitHub, the workflow:
    1. Installs dependencies and PyInstaller.
    2. Builds a Windows `.exe` with your icon and resources folder.
    3. Uploads the executable as an asset on the release.

#### Workflow File

The workflow file is located at `.github/workflows/build-windows-exe.yml` and uses the following build command:

```yaml
pyinstaller --onefile -w --icon=app_icon.ico --add-data "resources;resources" --name P4CacheCleaner p4_cleaner_advanced.py
```

---

## Contributing

- Fork the repository and create a feature branch.
- Ensure requirements are updated for any new dependencies.
- Open a pull request with a clear description of your changes.

---

## License

[MIT](LICENSE)

![image](https://github.com/user-attachments/assets/35d80d32-ed1e-4e4f-a00a-32876cd989d4)

