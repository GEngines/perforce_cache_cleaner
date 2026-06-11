import argparse
import collections
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import os
import shutil
import sqlite3
import sys
import threading
import time

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QPushButton, QFileDialog, QTextEdit,
    QProgressBar, QHBoxLayout, QSpinBox, QLineEdit, QCheckBox, QGroupBox, QRadioButton,
    QButtonGroup, QListWidget
)
from PySide6.QtGui import QIcon, QFont
from PySide6.QtCore import Qt, Signal, QThread

# Setup AppData log file
APPDATA_DIR = os.path.join(os.environ.get("APPDATA", "."), "P4PCleaner")
os.makedirs(APPDATA_DIR, exist_ok=True)
LOG_FILE = os.path.join(APPDATA_DIR, f"cleaner_{time.strftime('%Y%m%d-%H%M%S')}.log")

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)

DEFAULT_EXCLUDE_FILES = {"p4p.exe", "pdb.lbr", "p4p.conf", "p4ps.exe", "svcinst.exe"}
EXCLUDE_CONFIG_FILE = os.path.join(APPDATA_DIR, "excluded_files.json")


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

    def __init__(self, path,
                 low_thresh,
                 high_thresh,
                 folder_percent_keep,
                 drive_mode=True,
                 dry_run=False,
                 exclude_files=None):
        """
        Initialize the cleaner.

        Args:
            path (str): The directory to clean.
            low_thresh (int): Minimum required disk free percentage.
            high_thresh (int): Target disk free percentage after cleaning.
            folder_percent_keep (int): Percent of disk free to be kept.
            drive_mode (bool): If True, keep only drive folders.
            dry_run (bool): If True, simulate cleaning without deleting files.
        """
        self.path = path
        self.low_thresh = low_thresh
        self.high_thresh = high_thresh
        self.dry_run = dry_run
        self.drive_mode = drive_mode
        self.folder_percent_keep = folder_percent_keep
        self.exclude_files = set(exclude_files or [])
        self._stop_event = threading.Event()

    def request_stop(self):
        """Signal the cleaning loop to exit gracefully on its next iteration."""
        self._stop_event.set()

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
        for _, _, files in os.walk(path):
            for f in files:
                if f.lower() in self.exclude_files:
                    continue
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
        for root, _, files in os.walk(path):
            for f in files:
                if f.lower() in self.exclude_files:
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

    def get_total_cache_size_and_files(self, on_log=None):
        total_size = 0
        file_info = []
        for root, _, files in os.walk(self.path):
            for f in files:
                if f.lower() in self.exclude_files:
                    continue
                fp = os.path.join(root, f)
                try:
                    stat = os.stat(fp)
                    total_size += stat.st_size
                    file_info.append((stat.st_atime, stat.st_size, fp))
                except Exception as e:
                    if on_log: on_log(f"Error accessing {fp}: {e}")
        return total_size, file_info

    def clean(self, on_log, on_progress):
        """
        Perform the cache cleaning operation.

        Args:
            on_log (callable): Function to call with log messages.
            on_progress (callable): Function to call with progress updates.
        """
        mode = "DRY RUN" if self.dry_run else "ACTUAL DELETION"
        on_log(f"Starting cache clean operation ({mode})...")

        # Drive mode: check threshold before touching any files
        size_to_remove = None
        if self.drive_mode:
            disk_total, disk_free, disk_free_percent = self.get_disk_info(self.path)
            on_log(f"Total disk: {self.get_mb(disk_total)} | Free: {self.get_mb(disk_free)} | Free %: {disk_free_percent:.2f}%")
            if disk_free_percent >= self.low_thresh:
                on_log("Disk space above threshold, no action taken.")
                on_progress(100)
                return
            size_to_remove = (self.high_thresh * disk_total / 100) - disk_free
            on_log(f"Need to free {self.get_mb(size_to_remove)} to reach {self.high_thresh}% free.")

        on_progress(-1)
        on_log("Setting up SQLite database for file metadata...")

        # Unique filename per run prevents collisions when multiple instances run
        db_path = os.path.join(APPDATA_DIR, f'p4cleaner_{os.getpid()}_{int(time.time())}.db')
        if os.path.exists(db_path):
            os.remove(db_path)

        conn = sqlite3.connect(db_path)
        try:
            c = conn.cursor()
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("CREATE TABLE files (atime REAL, size INTEGER, path TEXT)")
            c.execute("CREATE INDEX idx_atime ON files (atime)")
            conn.commit()

            # Count files first so scan_dir can report accurate progress
            total_files = self.count_files(self.path)
            insert_batch = []
            SCAN_BATCH = 5000
            for atime, size, path in self.scan_dir(self.path, total_files, on_progress, on_log):
                insert_batch.append((atime, size, path))
                if len(insert_batch) >= SCAN_BATCH:
                    c.executemany("INSERT INTO files VALUES (?, ?, ?)", insert_batch)
                    conn.commit()
                    insert_batch.clear()
            if insert_batch:
                c.executemany("INSERT INTO files VALUES (?, ?, ?)", insert_batch)
                conn.commit()

            c.execute("SELECT COUNT(*), SUM(size) FROM files")
            file_count, total_size = c.fetchone()
            total_size = total_size or 0
            on_log(f"Indexed {file_count} files ({self.get_mb(total_size)}) in database.")

            # Folder mode: derive removal target from SQLite aggregate — no Python list needed
            if not self.drive_mode:
                target_size = total_size * self.folder_percent_keep // 100
                size_to_remove = total_size - target_size
                if size_to_remove <= 0:
                    on_log("Cache size is within the configured percentage, no action taken.")
                    on_progress(100)
                    return
                on_log(f"Will reduce cache to {self.folder_percent_keep}% of current size (target: {self.get_mb(target_size)}).")
                on_log(f"Need to remove {self.get_mb(size_to_remove)}.")

            # Delete oldest files in parallel batches until the space target is met.
            # 10 concurrent workers saturate network/SAN I/O without overwhelming it.
            # Log every LOG_INTERVAL deletions — per-file logging floods the Qt signal
            # queue at millions-of-files scale and is the primary cause of slow runs.
            removed_size = 0
            deleted = 0
            failed = 0
            DELETE_BATCH = 1000
            DELETE_WORKERS = 10
            LOG_INTERVAL = 1000

            with ThreadPoolExecutor(max_workers=DELETE_WORKERS) as executor:
                while removed_size < size_to_remove:
                    if self._stop_event.is_set():
                        on_log("Stop requested, cleaning aborted.")
                        break
                    c.execute("SELECT atime, size, path FROM files ORDER BY atime ASC LIMIT ?", (DELETE_BATCH,))
                    batch = c.fetchall()
                    if not batch:
                        break

                    if self.dry_run:
                        # Sequential in dry-run — just reporting, no I/O to parallelise
                        paths_logged = []
                        for _, size, path in batch:
                            if removed_size >= size_to_remove:
                                break
                            on_log(f"Would delete: {path}")
                            removed_size += size
                            deleted += 1
                            on_progress(round(100 * removed_size / size_to_remove, 1))
                            paths_logged.append((path,))
                        if paths_logged:
                            c.executemany("DELETE FROM files WHERE path = ?", paths_logged)
                            conn.commit()
                    else:
                        # Pre-select only the files needed to reach the target
                        to_delete = []
                        prospective = removed_size
                        for _, size, path in batch:
                            if prospective >= size_to_remove:
                                break
                            to_delete.append((size, path))
                            prospective += size

                        # Submit all to the pool; collect results as they complete
                        future_to_info = {
                            executor.submit(os.remove, path): (size, path)
                            for size, path in to_delete
                        }
                        paths_deleted = []
                        for future in as_completed(future_to_info):
                            size, path = future_to_info[future]
                            exc = future.exception()
                            if exc is None:
                                removed_size += size
                                deleted += 1
                                paths_deleted.append((path,))
                                if deleted % LOG_INTERVAL == 0:
                                    on_log(f"Deleted {deleted} files ({self.get_mb(removed_size)} of {self.get_mb(size_to_remove)})...")
                            else:
                                failed += 1
                                on_log(f"Failed to delete: {path} - {exc}")
                        on_progress(round(100 * removed_size / size_to_remove, 1))
                        if paths_deleted:
                            c.executemany("DELETE FROM files WHERE path = ?", [(p,) for p in paths_deleted])
                            conn.commit()

            action = "would be removed" if self.dry_run else "removed"
            fail_note = f", {failed} failed" if failed else ""
            on_log(f"Total of {self.get_mb(removed_size)} {action}. Deleted {deleted} files{fail_note}.")
            on_progress(100)

        except Exception as e:
            on_log(f"Exception occurred: {e}")
            logging.exception("clean() exception")
        finally:
            try:
                conn.close()
            except Exception:
                pass
            for suffix in ('', '-wal', '-shm'):
                try:
                    os.remove(db_path + suffix)
                except FileNotFoundError:
                    pass


# --- CLI Threaded Worker ---

class ThreadedCacheCleaner(threading.Thread, BaseCacheCleaner):
    """
    Threaded cache cleaner for CLI/headless mode using Python threading.
    """

    def __init__(self, path, low_thresh, high_thresh, folder_keep_percent, drive_mode, dry_run=False,
                 exclude_files=None):
        """
        Initialize the threaded cleaner.

        Args:
            path (str): The directory to clean.
            low_thresh (int): Minimum required disk free percentage.
            high_thresh (int): Target disk free percentage after cleaning.
            folder_keep_percent (int): Target disk free percentage after cleaning.
            drive_mode (bool): Drive mode to use.
            dry_run (bool): If True, simulate cleaning without deleting files.
            exclude_files (list[str]): Files to exclude.
        """
        threading.Thread.__init__(self)
        BaseCacheCleaner.__init__(self, path, low_thresh, high_thresh, folder_keep_percent, drive_mode, dry_run,
                                  exclude_files=list(exclude_files or []))

    def run(self):
        """
        Run the cleaning logic with print/log output.
        """

        def print_and_log(msg):
            print(msg)
            logging.info(msg)

        self.clean(print_and_log, lambda _: None)


# --- Qt QThread Worker ---

class QtCacheCleanerWorker(QThread, BaseCacheCleaner):
    """
    QThread-based cache cleaner for GUI mode, emitting Qt signals for progress and logs.
    """
    progress_signal = Signal(float)
    log_signal = Signal(str)
    done_signal = Signal()

    def __init__(self, path, low_thresh, high_thresh, folder_keep_percent, drive_mode, dry_run=False,
                 exclude_files=None):
        """
        Initialize the QThread worker.

        Args:
            path (str): The directory to clean.
            low_thresh (int): Minimum required disk free percentage.
            high_thresh (int): Target disk free percentage after cleaning.
            folder_keep_percent (int): Target disk free percentage after cleaning.
            drive_mode (bool): Drive mode to use.
            dry_run (bool): If True, simulate cleaning without deleting files.
            exclude_files (list[str]): Files to exclude.
        """
        QThread.__init__(self)
        BaseCacheCleaner.__init__(self,
                                  path,
                                  low_thresh,
                                  high_thresh,
                                  folder_keep_percent,
                                  drive_mode,
                                  dry_run,
                                  exclude_files=list(exclude_files or []))

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

        self.exclude_files = list(DEFAULT_EXCLUDE_FILES)

        # Main layout
        main_layout = QVBoxLayout()
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(30, 30, 30, 30)

        # Theme toggle button
        self.theme_button = QPushButton("🌙 Dark Mode")
        self.theme_button.setCheckable(True)
        self.theme_button.setMaximumWidth(140)
        self.theme_button.clicked.connect(self.toggle_theme)
        main_layout.addWidget(self.theme_button, alignment=Qt.AlignmentFlag.AlignRight)

        # Header
        header = QLabel("Perforce Proxy Cache Cleaner")
        header.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subheader = QLabel("Free your disk by automatically removing old cache files.")
        subheader.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subheader.setStyleSheet("color: #666; margin-bottom: 10px;")
        main_layout.addWidget(header)
        main_layout.addWidget(subheader)

        # Group: Cache Path
        path_group = QGroupBox("Cache Location")

        # Cache Root
        path_layout = QHBoxLayout()
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Select cache directory...")
        self.browse_button = QPushButton("Browse")
        self.browse_button.setIcon(QIcon.fromTheme("folder-open"))
        self.browse_button.clicked.connect(self.browse_path)
        path_layout.addWidget(self.path_input)
        path_layout.addWidget(self.browse_button)
        path_group.setLayout(path_layout)

        # Group: Cache Mode Radio
        mode_group = QGroupBox("Cache Type")
        mode_layout = QHBoxLayout()
        self.drive_radio = QRadioButton("Cache is mapped to entire drive")
        self.folder_radio = QRadioButton("Cache is Shared (part of the drive)")
        self.drive_radio.setChecked(True)
        self.mode_btn_group = QButtonGroup(self)
        self.mode_btn_group.addButton(self.drive_radio)
        self.mode_btn_group.addButton(self.folder_radio)
        mode_layout.addWidget(self.drive_radio)
        mode_layout.addWidget(self.folder_radio)
        mode_group.setLayout(mode_layout)

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

        # Only show these when "Shared" (folder_radio) is picked
        self.percent_label = QLabel("Keep % of cache data:")
        self.percent_spin = QSpinBox()
        self.percent_spin.setMinimum(1)
        self.percent_spin.setMaximum(100)
        self.percent_spin.setValue(80)

        self.dry_run_checkbox = QCheckBox("Dry Run (simulate only)")

        options_layout.addWidget(self.low_thresh_input)
        options_layout.addWidget(self.high_thresh_input)
        options_layout.addWidget(self.percent_label)
        options_layout.addWidget(self.percent_spin)
        options_layout.addWidget(self.dry_run_checkbox)
        options_group.setLayout(options_layout)

        # Group: Excluded Files
        exclude_group = QGroupBox("Excluded Files (do not delete)")
        exclude_layout = QVBoxLayout()

        self.exclude_list_widget = QListWidget()
        self.exclude_list_widget.setMinimumHeight(130)
        for item in self.exclude_files:
            self.exclude_list_widget.addItem(item)

        # Add controls
        add_layout = QHBoxLayout()
        self.exclude_input = QLineEdit()
        self.exclude_input.setPlaceholderText("Add file or pattern...")
        add_btn = QPushButton("Add")
        remove_btn = QPushButton("Remove Selected")
        add_layout.addWidget(self.exclude_input)
        add_layout.addWidget(add_btn)
        add_layout.addWidget(remove_btn)

        exclude_layout.addWidget(self.exclude_list_widget)
        exclude_layout.addLayout(add_layout)
        exclude_group.setLayout(exclude_layout)

        # Add handlers
        add_btn.clicked.connect(self.add_exclude_file)
        remove_btn.clicked.connect(self.remove_exclude_file)

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
        self.progress = QProgressBar()
        self.progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
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

        # Status label (replaces QStatusBar — QStatusBar is a QMainWindow widget
        # and renders a platform-native background inside QWidget layouts)
        self.status_bar = QLabel("Waiting to start...")
        self.status_bar.setObjectName("status_label")

        # Add to main layout
        main_layout.addWidget(path_group)
        main_layout.addWidget(mode_group)
        main_layout.addWidget(options_group)
        main_layout.addWidget(exclude_group)
        main_layout.addWidget(self.start_button)
        main_layout.addWidget(progress_group)
        main_layout.addWidget(logs_group)
        main_layout.addWidget(self.status_bar)
        self.setLayout(main_layout)

        # Connect mode switching to UI update
        self.drive_radio.toggled.connect(self.update_ui_fields)
        self.folder_radio.toggled.connect(self.update_ui_fields)
        self.update_ui_fields()

        # Theme
        self.light_stylesheet = load_stylesheet(resource_path("resources/css/light_mode.css"))
        self.dark_stylesheet = load_stylesheet(resource_path("resources/css/dark_mode.css"))
        self.setStyleSheet(self.light_stylesheet)
        self._gui_log_buffer = collections.deque(maxlen=100)
        self.append_log(f"Logs are also saved to: {LOG_FILE}")

    def closeEvent(self, event):
        cleaner = getattr(self, "cleaner", None)
        if cleaner and cleaner.isRunning():
            cleaner.request_stop()
            cleaner.wait(3000)
            if cleaner.isRunning():
                cleaner.terminate()  # last resort — cooperative stop didn't finish in time
                cleaner.wait()
        event.accept()

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_title_bar_theme(self.dark_mode)

    def _apply_title_bar_theme(self, dark: bool):
        """Sync the Windows title bar colour with the current light/dark theme."""
        if sys.platform == "win32":
            try:
                from ctypes import windll, c_int, byref, sizeof
                DWMWA_USE_IMMERSIVE_DARK_MODE = 20
                hwnd = int(self.winId())
                value = c_int(1 if dark else 0)
                windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, byref(value), sizeof(value)
                )
            except Exception:
                pass

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
        self._apply_title_bar_theme(self.dark_mode)

    def add_exclude_file(self):
        text = self.exclude_input.text().strip()
        if text and text not in self.exclude_files:
            self.exclude_files.append(text)
            self.exclude_list_widget.addItem(text)
            self.exclude_input.clear()

    def remove_exclude_file(self):
        for item in self.exclude_list_widget.selectedItems():
            self.exclude_files.remove(item.text())
            self.exclude_list_widget.takeItem(self.exclude_list_widget.row(item))

    def update_ui_fields(self):
        if self.drive_radio.isChecked():
            self.low_thresh_input.setVisible(True)
            self.high_thresh_input.setVisible(True)
            self.percent_label.setVisible(False)
            self.percent_spin.setVisible(False)
        else:
            self.low_thresh_input.setVisible(False)
            self.high_thresh_input.setVisible(False)
            self.percent_label.setVisible(True)
            self.percent_spin.setVisible(True)

    def on_progress_update(self, value):
        """
        Update the progress bar and label.

        Args:
            value (int/float): The progress percentage, or -1 for indeterminate.
        """
        if value == -1:
            self.progress.setRange(0, 0)
            self.status_bar.setText("Analyzing files...")
        else:
            if self.progress.maximum() != 1000:
                self.progress.setRange(0, 1000)
            self.progress.setFormat(f"{value:.1f}%")
            self.progress.setValue(int(value * 10))
            self.status_bar.setText("Processing files...")

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
        self.log_output.append(message)  # O(1), renders HTML, auto-scrolls
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
        self._gui_log_buffer.clear()
        self.progress.setValue(0)
        self.status_bar.setText("Starting...")
        self.start_button.setEnabled(False)
        dry_run = self.dry_run_checkbox.isChecked()

        self.cleaner = QtCacheCleanerWorker(
            path,
            self.low_thresh_input.value(),
            self.high_thresh_input.value(),
            self.percent_spin.value(),
            self.drive_radio.isChecked(),
            dry_run,
            self.exclude_files
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
        self.status_bar.setText("Done.")
        self.start_button.setEnabled(True)


# --- CLI entry point ---

def run_headless(path, low_thresh, high_thresh, folder_percent_keep, drive_mode, dry_run, exclude_files):
    """
    Run the cache cleaner in CLI mode without GUI.

    Args:
        path (str): Directory to clean.
        low_thresh (int): Minimum required disk free percent.
        high_thresh (int): Target disk free percent.
        drive_mode (bool): drive mode if its entire drive or just folder of a bigger drive.
        folder_percent_keep (int): percentage of the space to be retained.
        dry_run (bool): If True, simulate cleaning.
        exclude_files (list): list of files to be excluded.
    """
    if not os.path.isdir(path):
        print("Invalid path.")
        sys.exit(1)
    worker = ThreadedCacheCleaner(
        path,
        low_thresh,
        high_thresh,
        folder_percent_keep,
        drive_mode,
        dry_run,
        exclude_files
    )
    worker.start()
    worker.join()


# --- CLI Excluded Files Management ---

def load_exclude_files():
    """Load excluded files from config, or use default if not present."""
    if os.path.exists(EXCLUDE_CONFIG_FILE):
        try:
            with open(EXCLUDE_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return list(DEFAULT_EXCLUDE_FILES)


def save_exclude_files(files):
    """Save excluded files config."""
    with open(EXCLUDE_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(files, f)


def cli_show_excluded():
    exclude_files = load_exclude_files()
    print("Currently excluded files/patterns:")
    for i, f in enumerate(exclude_files, 1):
        print(f" {i}. {f}")


def cli_add_excluded(entry):
    files = load_exclude_files()
    if entry in files:
        print(f"Entry '{entry}' already in the excluded list.")
    else:
        files.append(entry)
        save_exclude_files(files)
        print(f"Added '{entry}' to excluded files.")


def cli_remove_excluded(entry):
    files = load_exclude_files()
    if entry not in files:
        print(f"Entry '{entry}' is not in the excluded list.")
    else:
        files.remove(entry)
        save_exclude_files(files)
        print(f"Removed '{entry}' from excluded files.")


def cli_edit_excluded():
    files = load_exclude_files()
    print("Current excluded files/patterns:")
    for i, f in enumerate(files, 1):
        print(f" {i}. {f}")
    print("\nOptions:")
    print("  a <pattern>    Add a pattern")
    print("  r <pattern>    Remove a pattern")
    print("  q              Quit")
    while True:
        cmd = input("Enter command: ").strip()
        if not cmd:
            continue
        if cmd == "q":
            break
        if cmd.startswith("a "):
            pat = cmd[2:].strip()
            if pat and pat not in files:
                files.append(pat)
                print(f"Added '{pat}'")
            else:
                print("Already present or invalid.")
        elif cmd.startswith("r "):
            pat = cmd[2:].strip()
            if pat in files:
                files.remove(pat)
                print(f"Removed '{pat}'")
            else:
                print("Not present.")
        else:
            print("Unknown command.")
        save_exclude_files(files)


def main():
    """
    Main entry point for the application. Parses arguments and launches GUI or CLI.
    """
    parser = argparse.ArgumentParser(description="Perforce Proxy Cache Cleaner")
    parser.add_argument('--path', type=str, help='Cache directory to analyze and clean')
    parser.add_argument('--low', type=int, default=20, help='Low disk free threshold percent (default: 20)')
    parser.add_argument('--high', type=int, default=30, help='High disk free threshold percent (default: 30)')
    parser.add_argument('--percent', type=int, default=80, help='Target percent of cache to keep (folder mode)')
    parser.add_argument('--drive-mode', action='store_true', help='Use entire drive mode (default)')
    parser.add_argument('--folder-mode', action='store_true', help='Use folder mode (regulate cache folder size)')
    parser.add_argument('--dry-run', action='store_true', help='Perform a dry run (no files will be deleted)')
    parser.add_argument('--show-excluded', action='store_true', help='Show excluded files/patterns')
    parser.add_argument('--add-excluded', type=str, help='Add a file or pattern to excluded files')
    parser.add_argument('--remove-excluded', type=str, help='Remove a file or pattern from excluded files')
    parser.add_argument('--edit-excluded', action='store_true', help='Interactively edit the excluded files list')
    args = parser.parse_args()

    # CLI excluded files management
    if args.show_excluded:
        cli_show_excluded()
        return
    if args.add_excluded:
        cli_add_excluded(args.add_excluded)
        return
    if args.remove_excluded:
        cli_remove_excluded(args.remove_excluded)
        return
    if args.edit_excluded:
        cli_edit_excluded()
        return

    if args.folder_mode:
        drive_mode = False
    else:
        drive_mode = True

    exclude_files = load_exclude_files()

    if args.path:
        # Headless (CLI) mode
        run_headless(
            args.path,
            args.low,
            args.high,
            args.percent,
            drive_mode,
            args.dry_run,
            exclude_files=exclude_files
        )
    else:
        # GUI mode
        app = QApplication(sys.argv)
        app.setWindowIcon(QIcon(resource_path("resources/icons/icon.png")))
        window = P4PCleanUI()
        window.resize(650, 1050)
        window.show()
        sys.exit(app.exec())


if __name__ == "__main__":
    main()
