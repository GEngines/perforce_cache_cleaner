# perforce_cache_cleaner

A tool to analyze files in a given path and automatically delete old files based on last access date, helping you recover disk space until a required free percentage is met.

## Features

- GUI for easy interaction
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

1. **Start the application**
   ```bash
   python p4p_cleaner_advanced.py
   ```
   - The GUI will launch.

2. **Configure in the GUI**
   - Select the cache path.
   - Set the low and high disk space thresholds (%).
   - Check "Dry Run" if you want a simulation (no files will be deleted).
   - Click "Start Cleaning" to begin. Progress and logs will be displayed in the GUI.

3. **Review Logs**
   - Logs are saved to disk for your reference and auditing.

> **Note:** There are no command-line arguments for this tool; all configuration is performed through the GUI.

## Support
For questions or issues, please open an issue in this repository.
   

![image](https://github.com/user-attachments/assets/dffa66ba-0f21-4dd3-8130-a1338c8c5dfe)
