import sys
import os
import threading
import shutil
import logging
import argparse

from PySide2.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QPushButton,
    QFileDialog, QTextEdit, QProgressBar, QHBoxLayout, QSpinBox, QLineEdit, QCheckBox, QGroupBox
)
from PySide2.QtGui import QIcon, QFont
from PySide2.QtCore import Qt, Signal, QObject

# Setup AppData log file
APPDATA_DIR = os.path.join(os.environ.get("APPDATA", "."), "P4PCleaner")
os.makedirs(APPDATA_DIR, exist_ok=True)
LOG_FILE = os.path.join(APPDATA_DIR, "cleaner.log")

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)

EXCLUDE_FILES = {"p4p", "p4p.exe", "pdb.lbr", "p4p.conf", "p4ps.exe", "svcinst.exe"}

def load_stylesheet(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


class Logger(QObject):
    """Qt signal-based logger for progress and log messages."""
    log_signal = Signal(str)
    progress_signal = Signal(int)
    done_signal = Signal()  # Signal to indicate cleaning is done

logger = Logger()

class CacheCleaner(threading.Thread):
    """Background thread for cleaning cache files based on disk usage.

    Args:
        path (str): The directory to clean.
        low_thresh (int): Minimum required disk free percentage.
        high_thresh (int): Target disk free percentage after cleaning.
        dry_run (bool): If True, simulate cleaning without deleting files.
        headless (bool): If True, run in CLI mode with console output.
    """
    def __init__(self, path, low_thresh, high_thresh, dry_run=False, headless=False):
        super().__init__()
        self.path = path
        self.low_thresh = low_thresh
        self.high_thresh = high_thresh
        self.dry_run = dry_run
        self.headless = headless

    def run(self):
        """Run the cleaning process, deleting files as needed or simulating if dry run."""
        try:
            mode = "DRY RUN" if self.dry_run else "ACTUAL DELETION"
            self.log(f"Starting cache clean operation ({mode})...")

            disk_total, disk_free, disk_free_percent = self.get_disk_info(self.path)
            self.log(
                f"Total disk: {self.get_mb(disk_total)} | Free: {self.get_mb(disk_free)} | Free %: {disk_free_percent:.2f}%")

            if disk_free_percent >= self.low_thresh:
                self.log("Disk space above threshold, no action taken.")
                self.progress(100)
                logger.done_signal.emit()
                return

            self.progress(-1)  # adding infinite progress bar
            self.log("Counting files to scan...")
            total_files = self.count_files(self.path)
            self.log(f"Total files to scan: {total_files}")

            # Reset progress bar to determinate mode
            self.progress(0)

            self.log("Scanning files...")
            files = list(self.scan_dir(self.path, total_files))
            self.log(f"Found {len(files)} files to consider.")
            files.sort(key=lambda x: x[0])  # Sort by last access time

            disk_free_target = self.high_thresh * disk_total / 100
            size_target = disk_free_target - disk_free
            removed_size = 0

            for i, (_, size, path) in enumerate(files):
                if removed_size >= size_target:
                    break
                try:
                    if self.dry_run:
                        self.log(f"Would delete: {path}")
                    else:
                        os.remove(path)
                        self.log(f"Deleted: {path}")
                    removed_size += size
                    self.update_progress(removed_size, size_target)
                except Exception as e:
                    self.log(f"Failed to delete: {path} - {e}")

            action = "would be removed" if self.dry_run else "removed"
            self.log(f"Total of {self.get_mb(removed_size)} {action} to meet target.")
            self.progress(100)

        except Exception as e:
            self.log(f"Exception occurred: {e}")

        logger.done_signal.emit()

    def count_files(self, path):
        """Count the total number of files in a directory, excluding certain files.

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

    def scan_dir(self, path, total_files):
        """Scan directory and yield file access info for each file.

        Args:
            path (str): Directory to scan.
            total_files (int): Total number of files for progress calculation.

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
                    percent = int((scanned / total_files) * 100)
                    self.progress(percent)
                    yield (stat.st_atime, stat.st_size, full_path)
                except Exception as e:
                    self.log(f"Error accessing {full_path}: {e}")

    def get_disk_info(self, path):
        """Get disk usage statistics for the given path.

        Args:
            path (str): Path to check.

        Returns:
            tuple: (total_bytes, free_bytes, percent_free)
        """
        usage = shutil.disk_usage(path)
        return usage.total, usage.free, (usage.free / usage.total) * 100

    def get_mb(self, size):
        """Convert bytes to megabytes as a formatted string.

        Args:
            size (int): Size in bytes.

        Returns:
            str: Size in MB.
        """
        return f"{size / (1024 * 1024):.2f} MB"

    def update_progress(self, removed_size, size_target):
        """Update progress based on removed size.

        Args:
            removed_size (int): Total size removed so far in bytes.
            size_target (int): Target size to remove in bytes.
        """
        percent = int(100 * removed_size / size_target) if size_target > 0 else 100
        self.progress(percent)

    def log(self, message):
        """Log a message to the appropriate output(s).

        Args:
            message (str): The message to log.
        """
        if self.headless:
            print(message)
        else:
            logger.log_signal.emit(message)
        logging.info(message)

    def progress(self, value):
        """Update progress bar or emit progress signal.

        Args:
            value (int): Progress percentage or -1 for indeterminate.
        """
        if not self.headless:
            logger.progress_signal.emit(value)
        # In headless mode, we can skip GUI progress updates


class P4PCleanUI(QWidget):
    """Qt GUI for the Perforce Proxy Cache Cleaner with modernized design and dark mode toggle."""
    def __init__(self):
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

        # Usage example in your P4PCleanUI class:
        self.light_stylesheet = load_stylesheet("resources/css/light_mode.css")
        self.dark_stylesheet = load_stylesheet("resources/css/dark_mode.css")

        # Set default (light) theme
        self.setStyleSheet(self.light_stylesheet)

        # Connect signals (assumes logger is a global object)
        logger.log_signal.connect(self.append_log)
        logger.progress_signal.connect(self.on_progress_update)
        logger.done_signal.connect(self.cleaning_done)

        self.append_log(f"Logs are also saved to: {LOG_FILE}")

    def toggle_theme(self):
        """Switch between light and dark mode."""
        self.dark_mode = not self.dark_mode
        if self.dark_mode:
            self.setStyleSheet(self.dark_stylesheet)
            self.theme_button.setText("☀️ Light Mode")
        else:
            self.setStyleSheet(self.light_stylesheet)
            self.theme_button.setText("🌙 Dark Mode")

    def on_progress_update(self, value):
        if value == -1:
            self.progress.setRange(0, 0)
            self.progress_label.setText("Analyzing files...")
        else:
            if self.progress.maximum() == 0:
                self.progress.setRange(0, 100)
            self.progress.setValue(value)
            self.progress_label.setText(f"Progress: {value}%")

    def browse_path(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Cache Directory")
        if dir_path:
            self.path_input.setText(dir_path)

    def append_log(self, message):
        self.log_output.append(message)
        logging.info(message)

    def start_cleaning(self):
        path = self.path_input.text().strip()
        if not os.path.isdir(path):
            self.append_log("<span style='color:red;font-weight:bold'>Invalid path.</span>")
            return

        self.log_output.clear()
        self.progress.setValue(0)
        self.progress_label.setText("Starting...")
        self.start_button.setEnabled(False)
        dry_run = self.dry_run_checkbox.isChecked()
        cleaner = CacheCleaner(
            path,
            self.low_thresh_input.value(),
            self.high_thresh_input.value(),
            dry_run=dry_run
        )
        cleaner.start()

    def cleaning_done(self):
        self.append_log("<b>Cleaning operation finished.</b>")
        self.progress_label.setText("Done.")
        self.start_button.setEnabled(True)


def run_headless(path, low_thresh, high_thresh, dry_run):
    """Run the cache cleaner in CLI mode without GUI.

    Args:
        path (str): Directory to clean.
        low_thresh (int): Minimum required disk free percent.
        high_thresh (int): Target disk free percent.
        dry_run (bool): If True, simulate cleaning.
    """
    if not os.path.isdir(path):
        print("Invalid path.")
        sys.exit(1)
    cleaner = CacheCleaner(
        path,
        low_thresh,
        high_thresh,
        dry_run=dry_run,
        headless=True
    )
    cleaner.run()

def main():
    """Main entry point for the application. Parses arguments and launches GUI or CLI."""
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
        app.setWindowIcon(QIcon("resources/icons/icon.png"))  # App icon
        window = P4PCleanUI()
        window.resize(650, 900)
        window.show()
        sys.exit(app.exec_())

if __name__ == "__main__":
    main()