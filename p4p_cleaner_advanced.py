import sys
import os
import threading
import shutil
import logging
import argparse
from PySide2.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QPushButton,
    QFileDialog, QTextEdit, QProgressBar, QHBoxLayout, QSpinBox, QLineEdit, QCheckBox
)
from PySide2.QtCore import Signal, QObject

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
    """Qt GUI for the Perforce Proxy Cache Cleaner."""
    def __init__(self):
        """Initialize the main window and layout of the GUI."""
        super().__init__()
        self.setWindowTitle("Perforce Proxy Cache Cleaner")
        self.resize(600, 400)

        self.layout = QVBoxLayout()

        self.path_label = QLabel("Cache Path:")
        self.path_input = QLineEdit()
        self.browse_button = QPushButton("Browse")
        self.browse_button.clicked.connect(self.browse_path)

        self.low_thresh_input = QSpinBox()
        self.low_thresh_input.setRange(1, 100)
        self.low_thresh_input.setValue(20)
        self.low_thresh_input.setPrefix("Low %: ")

        self.high_thresh_input = QSpinBox()
        self.high_thresh_input.setRange(1, 100)
        self.high_thresh_input.setValue(30)
        self.high_thresh_input.setPrefix("High %: ")

        self.dry_run_checkbox = QCheckBox("Dry Run (no actual deletion)")

        self.start_button = QPushButton("Start Cleaning")
        self.start_button.clicked.connect(self.start_cleaning)

        self.progress = QProgressBar()
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)

        path_layout = QHBoxLayout()
        path_layout.addWidget(self.path_input)
        path_layout.addWidget(self.browse_button)

        thresh_layout = QHBoxLayout()
        thresh_layout.addWidget(self.low_thresh_input)
        thresh_layout.addWidget(self.high_thresh_input)

        self.layout.addWidget(self.path_label)
        self.layout.addLayout(path_layout)
        self.layout.addLayout(thresh_layout)
        self.layout.addWidget(self.dry_run_checkbox)
        self.layout.addWidget(self.start_button)
        self.layout.addWidget(self.progress)
        self.layout.addWidget(self.log_output)

        self.setLayout(self.layout)

        logger.log_signal.connect(self.append_log)
        logger.progress_signal.connect(self.on_progress_update)
        logger.done_signal.connect(self.cleaning_done)

        self.append_log(f"Logs are also saved to: {LOG_FILE}")

    def on_progress_update(self, value):
        """Update the progress bar in the GUI.

        Args:
            value (int): Progress percentage or -1 for indeterminate.
        """
        if value == -1:
            # Indeterminate mode (busy)
            self.progress.setRange(0, 0)
        else:
            # Determinate mode
            if self.progress.maximum() == 0:
                self.progress.setRange(0, 100)
            self.progress.setValue(value)

    def browse_path(self):
        """Open a file dialog to select the cache directory."""
        dir_path = QFileDialog.getExistingDirectory(self, "Select Cache Directory")
        if dir_path:
            self.path_input.setText(dir_path)

    def append_log(self, message):
        """Append a log message to the text area in the GUI.

        Args:
            message (str): The message to append.
        """
        self.log_output.append(message)
        logging.info(message)

    def start_cleaning(self):
        """Start the cleaning process when the user clicks the start button."""
        path = self.path_input.text().strip()
        if not os.path.isdir(path):
            self.append_log("Invalid path.")
            return

        # Clear the log output to start fresh
        self.log_output.clear()

        self.progress.setValue(0)
        self.start_button.setEnabled(False)  # Disable button while cleaning
        dry_run = self.dry_run_checkbox.isChecked()
        cleaner = CacheCleaner(
            path,
            self.low_thresh_input.value(),
            self.high_thresh_input.value(),
            dry_run=dry_run
        )
        cleaner.start()

    def cleaning_done(self):
        """Called when cleaning is finished to re-enable UI controls."""
        self.append_log("Cleaning operation finished.")
        self.start_button.setEnabled(True)  # Re-enable button after cleaning

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
        window = P4PCleanUI()
        window.show()
        sys.exit(app.exec_())

if __name__ == "__main__":
    main()