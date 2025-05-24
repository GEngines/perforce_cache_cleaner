# perforce_cache_cleaner

A tool to analyze files in a given path and automatically delete old files based on last access date, helping you recover disk space until a required free percentage is met.

## Features

- GUI for easy interaction
- Command-line interface (CLI) support for automation
- Logging to disk for auditing
- Progress indication while analyzing files
- Dry Run mode (provides estimates without deleting data, helpful for previewing actions)

## Upcoming Features

- Dashboard
- Scheduler support
- Central dashboard to monitor multiple instances

## Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/GEngines/perforce_cache_cleaner.git
   cd perforce_cache_cleaner
   ```

2. **Set up a Python virtual environment (recommended)**
   ```bash
   python -m venv venv
   source venv/bin/activate           # On Linux/macOS
   .\venv\Scripts\activate            # On Windows
   ```

3. **Install required dependencies**
   ```bash
   pip install -r requirements.txt
   ```
   > If `requirements.txt` does not exist, install required packages manually as specified in the documentation or code comments.

## Usage

### Launching the GUI

To use the graphical interface:
```bash
python p4p_cleaner_advanced.py
```
- The GUI will launch.  
- Select the cache path.
- Set the low and high disk space thresholds (%).
- Check "Dry Run" if you want a simulation (no files will be deleted).
- Click "Start Cleaning" to begin. Progress and logs will be displayed in the GUI.

### Command-Line Usage

You can also run the cleaner in headless (CLI) mode for scripting, automation, or remote/server use:

```bash
python p4p_cleaner_advanced.py --path /your/cache/path --low 20 --high 30 --dry-run
```

**Arguments:**
- `--path` (required for CLI): Path to the cache directory to analyze and clean.
- `--low`: Minimum required free disk space percentage before cleaning (default: 20).
- `--high`: Target free disk space percentage after cleaning (default: 30).
- `--dry-run`: Perform a dry run (no files will be deleted, just a report).

**Example:**

Dry run (nothing is deleted):
```bash
python p4p_cleaner_advanced.py --path /p4cache --low 20 --high 30 --dry-run
```

Actual cleaning (files will be deleted as needed):
```bash
python p4p_cleaner_advanced.py --path /p4cache --low 20 --high 30
```

## Logging

- All actions and progress are logged to disk at:
  ```
  %APPDATA%/P4PCleaner/cleaner.log   # On Windows
  ./P4PCleaner/cleaner.log           # On Linux/macOS
  ```

## Support

For questions or issues, please open an issue in this repository.

![image](https://github.com/user-attachments/assets/35d80d32-ed1e-4e4f-a00a-32876cd989d4)

