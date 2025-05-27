import sys
import os
import shutil
import logging
import argparse
import threading
import time
import sqlite3

from PySide2.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QPushButton,
    QFileDialog, QTextEdit, QProgressBar, QHBoxLayout, QSpinBox, QLineEdit, QCheckBox, QGroupBox
)
from PySide2.QtGui import QIcon, QFont, QTextCursor
from PySide2.QtCore import Qt, Signal, QThread

# Setup AppData log file
APPDATA_DIR = os.path.join(os.environ.get("APPDATA", "."), "P4PCleaner")
os.makedirs(APPDATA_DIR, exist_ok=True)
LOG_FILE = os.path.join(APPDATA_DIR, f"cleaner_{time.strftime('%Y%m%d-%H%M%S')}.log")

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)

EXCLUDE_FILES = {"p4p", "p4p.exe", "pdb.lbr", "p4p.conf", "p4ps.exe", "svcinst.exe"}

def resource_path(relative_path):
    """
    Get absolute path to resource, works for dev and for PyInstaller.

    Args:
        relative_path (str): Relative path to resource.

    Returns:
        str: Absolute path to resource.
    """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(base_path, relative_path)

def load_stylesheet(path):
    """
    Load a Qt stylesheet from file.

    Args:
        path (str): Path to stylesheet file.

    Returns:
        str: Stylesheet contents.
    """
    with open(resource_path(path), "r", encoding="utf-8") as f:
        return f.read()

# --- Base Cleaning Logic ---

class BaseCacheCleaner:
    """
    Base class for cache cleaning logic, shared by both threading and QThread workers.
    """
    def __init__(self, path, low_thresh, high_thresh, dry_run=False):
        """
        Initialize the cleaner.

        Args:
            path (str): The directory to clean.
            low_thresh (int): Minimum required disk free percentage.
            high_thresh (int): Target disk free percentage after cleaning.
            dry_run (bool): If True, simulate cleaning without deleting files.
        """
        self.path = path
        self.low_thresh = low_thresh
        self.high_thresh = high_thresh
        self.dry_run = dry_run

    def get_disk_info(self, path):
        """
        Get disk usage statistics for the given path.

        Args:
            path (str): Path to check.

        Returns:
            tuple: (total_bytes, free_bytes, percent_free)
        """
        usage = shutil.disk_usage(path)
        return usage.total, usage.free, (usage.free / usage.total) * 100

    def get_mb(self, size):
        """
        Convert bytes to megabytes as a formatted string.

        Args:
            size (int): Size in bytes.

        Returns:
            str: Size in MB.
        """
        return f"{size / (1024 * 1024):.2f} MB"

    def count_files(self, path):
        """
        Count the total number of files in a directory, excluding certain files.

        Args:
            path (str): Directory to scan.

        Returns:
            int: Number of files found.
        """
        count = 0
        for root, dirs, files in os.walk(path):
            for f in files:
                if f.lower() not in EXCLUDE_FILES:
                    count += 1
        return count

    def scan_dir(self, path, total_files, on_progress=None, on_log=None):
        """
        Scan directory and yield file access info for each file.

        Args:
            path (str): Directory to scan.
            total_files (int): Total number of files for progress calculation.
            on_progress (callable): Optional callback for progress updates.
            on_log (callable): Optional callback for log messages.

        Yields:
            tuple: (last_access_time, file_size, file_path)
        """
        scanned = 0
        for root, dirs, files in os.walk(path):
            for f in files:
                if f.lower() in EXCLUDE_FILES:
                    continue
                full_path = os.path.join(root, f)
                try:
                    stat = os.stat(full_path)
                    scanned += 1
                    if total_files:
                        percent = int((scanned / total_files) * 100)
                        if on_progress: on_progress(percent)
                    yield (stat.st_atime, stat.st_size, full_path)
                except Exception as e:
                    if on_log: on_log(f"Error accessing {full_path}: {e}")

    def clean(self, on_log, on_progress):
        """
        Perform the cache cleaning operation.

        Args:
            on_log (callable): Function to call with log messages.
            on_progress (callable): Function to call with progress updates.
        """

        try:
            mode = "DRY RUN" if self.dry_run else "ACTUAL DELETION"
            on_log(f"Starting cache clean operation ({mode})...")

            disk_total, disk_free, disk_free_percent = self.get_disk_info(self.path)
            on_log(f"Total disk: {self.get_mb(disk_total)} | Free: {self.get_mb(disk_free)} | Free %: {disk_free_percent:.2f}%")

            if disk_free_percent >= self.low_thresh:
                on_log("Disk space above threshold, no action taken.")
                on_progress(100)
                return

            on_progress(-1)
            on_log("Setting up SQLite database for file metadata...")

            db_path = os.path.join(APPDATA_DIR, 'p4cleaner.db')
            # clean up any existing db.
            if os.path.exists(db_path):
                os.remove(db_path)

            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("CREATE TABLE files (atime REAL, size INTEGER, path TEXT)")
            conn.commit()

            # Scan and insert metadata
            count = 0
            batch = []
            for atime, size, path in self.scan_dir(self.path, None, None, on_log):
                batch.append((atime, size, path))
                count += 1
                if len(batch) >= 1000:
                    c.executemany("INSERT INTO files VALUES (?, ?, ?)", batch)
                    conn.commit()
                    batch.clear()
            if batch:  # Insert any remaining rows
                c.executemany("INSERT INTO files VALUES (?, ?, ?)", batch)
                conn.commit()
            on_log(f"Indexed {count} files in database.")

            disk_free_target = self.high_thresh * disk_total / 100
            size_target = disk_free_target - disk_free
            removed_size = 0
            deleted = 0

            # Query & delete the oldest files until space target is met
            while removed_size < size_target:
                c.execute("SELECT atime, size, path FROM files ORDER BY atime ASC LIMIT 100")
                batch = c.fetchall()
                if not batch:
                    break
                for atime, size, path in batch:
                    if removed_size >= size_target:
                        break
                    try:
                        if self.dry_run:
                            on_log(f"Would delete: {path}")
                        else:
                            os.remove(path)
                            on_log(f"Deleted: {path}")
                        removed_size += size
                        deleted += 1
                        percent = round(100 * removed_size / size_target, 1) if size_target > 0 else 100.0
                        on_progress(percent)
                        # Remove from DB
                        c.execute("DELETE FROM files WHERE path = ?", (path,))
                    except Exception as e:
                        on_log(f"Failed to delete: {path} - {e}")
                conn.commit()

            action = "would be removed" if self.dry_run else "removed"
            on_log(f"Total of {self.get_mb(removed_size)} {action} to meet target. Deleted {deleted} files.")
            on_progress(100)
            c.close()
            conn.close()
            os.remove(db_path)
        except Exception as e:
            on_log(f"Exception occurred: {e}")


# --- CLI Threaded Worker ---

class ThreadedCacheCleaner(threading.Thread, BaseCacheCleaner):
    """
    Threaded cache cleaner for CLI/headless mode using Python threading.
    """
    def __init__(self, path, low_thresh, high_thresh, dry_run=False):
        """
        Initialize the threaded cleaner.

        Args:
            path (str): The directory to clean.
            low_thresh (int): Minimum required disk free percentage.
            high_thresh (int): Target disk free percentage after cleaning.
            dry_run (bool): If True, simulate cleaning without deleting files.
        """
        threading.Thread.__init__(self)
        BaseCacheCleaner.__init__(self, path, low_thresh, high_thresh, dry_run)

    def run(self):
        """
        Run the cleaning logic with print/log output.
        """
        def print_and_log(msg):
            print(msg)
            logging.info(msg)
        self.clean(print_and_log, lambda p: None)

# --- Qt QThread Worker ---

class QtCacheCleanerWorker(QThread, BaseCacheCleaner):
    """
    QThread-based cache cleaner for GUI mode, emitting Qt signals for progress and logs.
    """
    progress_signal = Signal(float)
    log_signal = Signal(str)
    done_signal = Signal()

    def __init__(self, path, low_thresh, high_thresh, dry_run=False):
        """
        Initialize the QThread worker.

        Args:
            path (str): The directory to clean.
            low_thresh (int): Minimum required disk free percentage.
            high_thresh (int): Target disk free percentage after cleaning.
            dry_run (bool): If True, simulate cleaning without deleting files.
        """
        QThread.__init__(self)
        BaseCacheCleaner.__init__(self, path, low_thresh, high_thresh, dry_run)

    def run(self):
        """
        Run the cleaning logic, emitting Qt signals for UI updates.
        """
        self.clean(self.log_signal.emit, self.progress_signal.emit)
        self.done_signal.emit()

# --- GUI ---

class P4PCleanUI(QWidget):
    """
    Qt GUI for the Perforce Proxy Cache Cleaner with modernized design and dark mode toggle.
    """
    def __init__(self):
        """
        Initialize the main GUI window and widgets.
        """
        super().__init__()
        self.setWindowTitle("Perforce Proxy Cache Cleaner")
        self.resize(650, 500)
        self.dark_mode = False

        # Main layout
        main_layout = QVBoxLayout()
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(30, 30, 30, 30)

        # Theme toggle button
        self.theme_button = QPushButton("🌙 Dark Mode")
        self.theme_button.setCheckable(True)
        self.theme_button.setMaximumWidth(140)
        self.theme_button.clicked.connect(self.toggle_theme)
        main_layout.addWidget(self.theme_button, alignment=Qt.AlignRight)

        # Header
        header = QLabel("Perforce Proxy Cache Cleaner")
        header.setFont(QFont("Segoe UI", 20, QFont.Bold))
        header.setAlignment(Qt.AlignCenter)
        subheader = QLabel("Free your disk by automatically removing old cache files.")
        subheader.setAlignment(Qt.AlignCenter)
        subheader.setStyleSheet("color: #666; margin-bottom: 10px;")
        main_layout.addWidget(header)
        main_layout.addWidget(subheader)

        # Group: Cache Path
        path_group = QGroupBox("Cache Location")
        path_layout = QHBoxLayout()
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Select cache directory...")
        self.browse_button = QPushButton("Browse")
        self.browse_button.setIcon(QIcon.fromTheme("folder-open"))
        self.browse_button.clicked.connect(self.browse_path)
        path_layout.addWidget(self.path_input)
        path_layout.addWidget(self.browse_button)
        path_group.setLayout(path_layout)

        # Group: Cleaning Options
        options_group = QGroupBox("Cleaning Options")
        options_layout = QHBoxLayout()
        self.low_thresh_input = QSpinBox()
        self.low_thresh_input.setRange(1, 100)
        self.low_thresh_input.setValue(20)
        self.low_thresh_input.setSuffix("% (min free)")
        self.high_thresh_input = QSpinBox()
        self.high_thresh_input.setRange(1, 100)
        self.high_thresh_input.setValue(30)
        self.high_thresh_input.setSuffix("% (target free)")
        self.dry_run_checkbox = QCheckBox("Dry Run (simulate only)")
        options_layout.addWidget(self.low_thresh_input)
        options_layout.addWidget(self.high_thresh_input)
        options_layout.addWidget(self.dry_run_checkbox)
        options_group.setLayout(options_layout)

        # Start Button
        self.start_button = QPushButton("Start Cleaning")
        self.start_button.setStyleSheet(
            "QPushButton {background-color: #2d89ef; color: white; border-radius: 6px; padding: 8px 20px; font-size: 16px;} QPushButton:disabled {background-color: #999;}"
        )
        self.start_button.setFixedHeight(40)
        self.start_button.clicked.connect(self.start_cleaning)

        # Progress
        progress_group = QGroupBox("Progress")
        progress_layout = QVBoxLayout()
        self.progress_label = QLabel("Waiting to start...")
        self.progress_label.setStyleSheet("color: #888;")
        self.progress = QProgressBar()
        self.progress.setAlignment(Qt.AlignCenter)
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress)
        progress_group.setLayout(progress_layout)

        # Logs
        logs_group = QGroupBox("Logs")
        logs_layout = QVBoxLayout()
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFont(QFont("Consolas", 10))
        logs_layout.addWidget(self.log_output)
        logs_group.setLayout(logs_layout)

        # Add to main layout
        main_layout.addWidget(path_group)
        main_layout.addWidget(options_group)
        main_layout.addWidget(self.start_button)
        main_layout.addWidget(progress_group)
        main_layout.addWidget(logs_group)
        self.setLayout(main_layout)

        # Theme
        self.light_stylesheet = load_stylesheet(resource_path("resources/css/light_mode.css"))
        self.dark_stylesheet = load_stylesheet(resource_path("resources/css/dark_mode.css"))
        self.setStyleSheet(self.light_stylesheet)
        self._gui_log_buffer = []  # Add this for storing last 100 lines
        self.append_log(f"Logs are also saved to: {LOG_FILE}")

    def closeEvent(self, event):
        # If a cleaning thread is running, request it to stop
        cleaner = getattr(self, "cleaner", None)
        if cleaner and cleaner.isRunning():
            cleaner.terminate()  # Forcefully stop the QThread
            cleaner.wait()
        event.accept()

    def toggle_theme(self):
        """
        Switch between light and dark mode.
        """
        self.dark_mode = not self.dark_mode
        if self.dark_mode:
            self.setStyleSheet(self.dark_stylesheet)
            self.theme_button.setText("☀️ Light Mode")
        else:
            self.setStyleSheet(self.light_stylesheet)
            self.theme_button.setText("🌙 Dark Mode")

    def on_progress_update(self, value):
        """
        Update the progress bar and label.

        Args:
            value (int): The progress percentage, or -1 for indeterminate.
        """
        if value == -1:
            self.progress.setRange(0, 0)
            self.progress_label.setText("Analyzing files...")
        else:
            if self.progress.maximum() != 1000:
                self.progress.setRange(0, 1000)
            progress_value = int(value * 10)
            self.progress.setValue(progress_value)
            self.progress_label.setText(f"Progress: {value:.1f}%")

    def browse_path(self):
        """
        Show a dialog to select a directory for cleaning.
        """
        dir_path = QFileDialog.getExistingDirectory(self, "Select Cache Directory")
        if dir_path:
            self.path_input.setText(dir_path)

    def append_log(self, message):
        """
        Append a log message to the log output widget and the log file.
        Only the most recent 100 lines are shown in the GUI.
        Args:
            message (str): The message to log.
        """
        self._gui_log_buffer.append(message)
        # Keep only the last 100 messages
        if len(self._gui_log_buffer) > 100:
            self._gui_log_buffer = self._gui_log_buffer[-100:]
        # Update the GUI log display
        self.log_output.setPlainText("\n".join(self._gui_log_buffer))
        self.log_output.moveCursor(QTextCursor.End)
        logging.info(message)

    def start_cleaning(self):
        """
        Start the cleaning operation, using QThread worker.
        """
        path = self.path_input.text().strip()
        if not os.path.isdir(path):
            self.append_log("<span style='color:red;font-weight:bold'>Invalid path.</span>")
            return

        self.log_output.clear()
        self.progress.setValue(0)
        self.progress_label.setText("Starting...")
        self.start_button.setEnabled(False)
        dry_run = self.dry_run_checkbox.isChecked()

        self.cleaner = QtCacheCleanerWorker(
            path,
            self.low_thresh_input.value(),
            self.high_thresh_input.value(),
            dry_run
        )
        self.cleaner.progress_signal.connect(self.on_progress_update)
        self.cleaner.log_signal.connect(self.append_log)
        self.cleaner.done_signal.connect(self.cleaning_done)
        self.cleaner.start()

    def cleaning_done(self):
        """
        Called when cleaning is finished. Updates the UI.
        """
        self.append_log("<b>Cleaning operation finished.</b>")
        self.progress_label.setText("Done.")
        self.start_button.setEnabled(True)

# --- CLI entry point ---

def run_headless(path, low_thresh, high_thresh, dry_run):
    """
    Run the cache cleaner in CLI mode without GUI.

    Args:
        path (str): Directory to clean.
        low_thresh (int): Minimum required disk free percent.
        high_thresh (int): Target disk free percent.
        dry_run (bool): If True, simulate cleaning.
    """
    if not os.path.isdir(path):
        print("Invalid path.")
        sys.exit(1)
    worker = ThreadedCacheCleaner(
        path,
        low_thresh,
        high_thresh,
        dry_run
    )
    worker.start()
    worker.join()

def main():
    """
    Main entry point for the application. Parses arguments and launches GUI or CLI.
    """
    parser = argparse.ArgumentParser(description="Perforce Proxy Cache Cleaner")
    parser.add_argument('--path', type=str, help='Cache directory to analyze and clean')
    parser.add_argument('--low', type=int, default=20, help='Low disk free threshold percent (default: 20)')
    parser.add_argument('--high', type=int, default=30, help='High disk free threshold percent (default: 30)')
    parser.add_argument('--dry-run', action='store_true', help='Perform a dry run (no files will be deleted)')
    args = parser.parse_args()

    if args.path:
        # Headless (CLI) mode
        run_headless(args.path, args.low, args.high, args.dry_run)
    else:
        # GUI mode
        app = QApplication(sys.argv)
        app.setWindowIcon(QIcon(resource_path("resources/icons/icon.png")))
        window = P4PCleanUI()
        window.resize(650, 900)
        window.show()
        sys.exit(app.exec_())

if __name__ == "__main__":
    main()