# P4 Cleaner

P4 Cleaner is a tool to monitor your files and clean up old files to recover disk space.

## Features

- Scans and cleans Perforce cache folders to free up disk space.
- Bundled resources for configuration or additional data.
- Provides a ready-to-use Windows executable with a custom icon (no Python installation required).
- Automated GitHub Actions workflow to build and attach the Windows `.exe` to releases.

---

## Download

- Download the latest Windows executable [`P4Cleaner.exe`](https://github.com/GEngines/perforce_cache_cleaner/releases/latest) from the [Releases](https://github.com/GEngines/perforce_cache_cleaner/releases) page.
- The executable bundles all required resources and uses a custom icon.

---

## Usage

### As a Windows Executable

1. Download `P4Cleaner.exe` from the [latest release](https://github.com/GEngines/perforce_cache_cleaner/releases/latest).
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
    python perforce_cache_cleaner.py
    ```

#### Accessing Resources in Source and Executable

This project uses a utility function to access resources in a way that works both for local development and when packaged as an executable with PyInstaller:

```python
from resource_helper import resource_path

with open(resource_path("resources/myfile.txt")) as f:
    data = f.read()
```

---

## Building the Windows Executable

### Prerequisites

- Python 3.11 or newer
- [PyInstaller](https://pyinstaller.org/en/stable/)
- Your custom icon, e.g., `app_icon.ico`, and a `resources` folder in the project root

### Build Steps (Manual)

```sh
pip install pyinstaller
pyinstaller --onefile --icon=app_icon.ico --add-data "resources;resources" --name P4Cleaner perforce_cache_cleaner.py
```

- The resulting `P4Cleaner.exe` will appear in the `dist/` directory.
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
pyinstaller --onefile --icon=app_icon.ico --add-data "resources;resources" --name P4Cleaner perforce_cache_cleaner.py
```

---

## Contributing

- Fork the repository and create a feature branch.
- Ensure requirements are updated for any new dependencies.
- Use the `resource_helper.py` utility for loading bundled resources.
- Open a pull request with a clear description of your changes.

---

## License

[MIT](LICENSE)

![image](https://github.com/user-attachments/assets/35d80d32-ed1e-4e4f-a00a-32876cd989d4)

