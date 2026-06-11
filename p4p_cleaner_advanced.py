import argparse
import collections
from concurrent.futures import ThreadPoolExecutor, as_completed
import fnmatch
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
    QButtonGroup, QListWidget, QStackedWidget, QDialog, QSizePolicy
)
from PySide6.QtGui import QIcon, QFont
from PySide6.QtCore import Qt, Signal, QThread

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
    """Resolve a resource path for both dev and PyInstaller-bundled runs."""
    base_path = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    return os.path.join(base_path, relative_path)


def load_stylesheet(path):
    with open(resource_path(path), "r", encoding="utf-8") as f:
        return f.read()


class BaseCacheCleaner:
    """Shared cache-cleaning logic used by both the CLI thread and GUI QThread workers."""

    def __init__(self, path,
                 low_thresh,
                 high_thresh,
                 folder_percent_keep,
                 drive_mode=True,
                 dry_run=False,
                 exclude_files=None):
        self.path = path
        self.low_thresh = low_thresh
        self.high_thresh = high_thresh
        self.dry_run = dry_run
        self.drive_mode = drive_mode
        self.folder_percent_keep = folder_percent_keep
        self.exclude_files = set(exclude_files or [])
        self._stop_event = threading.Event()
        self.last_plan_path = None

    def request_stop(self):
        """Signal the cleaning loop to exit gracefully on its next iteration."""
        self._stop_event.set()

    def get_disk_info(self, path):
        usage = shutil.disk_usage(path)
        return usage.total, usage.free, (usage.free / usage.total) * 100

    def get_mb(self, size):
        return f"{size / (1024 * 1024):.2f} MB"

    def count_files(self, path):
        count = 0
        for _, _, files in os.walk(path):
            for f in files:
                fl = f.lower()
                if any(fnmatch.fnmatch(fl, p) for p in self.exclude_files):
                    continue
                count += 1
        return count

    def scan_dir(self, path, total_files, on_progress=None, on_log=None):
        """Yield (atime, size, path) for every non-excluded file under path."""
        scanned = 0
        for root, _, files in os.walk(path):
            for f in files:
                fl = f.lower()
                if any(fnmatch.fnmatch(fl, p) for p in self.exclude_files):
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
                fl = f.lower()
                if any(fnmatch.fnmatch(fl, p) for p in self.exclude_files):
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
        Perform the cache cleaning operation in four phases:
          1. Scan  — walk the cache directory and build a SQLite index
          2. Plan  — identify the oldest files that satisfy the removal target
          3. Record — write the candidate list to a text file on disk
          4. Execute — delete from the text file using concurrent workers
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

        # ── Phase 1: Scan ────────────────────────────────────────────────────
        on_progress(-1)
        on_log("Phase 1/4: Scanning files into database...")

        db_path = os.path.join(APPDATA_DIR, f'p4cleaner_{os.getpid()}_{int(time.time())}.db')
        if os.path.exists(db_path):
            os.remove(db_path)

        conn = sqlite3.connect(db_path)
        try:
            c = conn.cursor()
            c.execute("PRAGMA synchronous=OFF")      # safe: temp DB, never recovered on crash
            c.execute("PRAGMA cache_size=-65536")  # 64 MB page cache
            c.execute("PRAGMA temp_store=MEMORY")
            c.execute("CREATE TABLE files (atime REAL, size INTEGER, path TEXT)")
            conn.commit()

            insert_batch = []
            SCAN_BATCH = 50000
            scanned = 0
            for atime, size, path in self.scan_dir(self.path, None, None, on_log):
                insert_batch.append((atime, size, path))
                scanned += 1
                if len(insert_batch) >= SCAN_BATCH:
                    c.executemany("INSERT INTO files VALUES (?, ?, ?)", insert_batch)
                    conn.commit()
                    insert_batch.clear()
                    on_log(f"  Scanned {scanned:,} files...")
            if insert_batch:
                c.executemany("INSERT INTO files VALUES (?, ?, ?)", insert_batch)
                conn.commit()

            on_log(f"  Building sort index on {scanned:,} files...")
            c.execute("CREATE INDEX idx_atime ON files (atime)")
            conn.commit()

            c.execute("SELECT COUNT(*), SUM(size) FROM files")
            file_count, total_size = c.fetchone()
            total_size = total_size or 0
            on_log(f"Phase 1/4 done: {file_count:,} files ({self.get_mb(total_size)}) indexed.")

            if not self.drive_mode:
                target_size = total_size * self.folder_percent_keep // 100
                size_to_remove = total_size - target_size
                if size_to_remove <= 0:
                    on_log("Cache size is within the configured percentage, no action taken.")
                    on_progress(100)
                    return
                on_log(f"Will reduce cache to {self.folder_percent_keep}% of current size "
                       f"(target: {self.get_mb(target_size)}).")

            assert size_to_remove is not None  # always set above; narrows type for checker
            on_log(f"Phase 2/4: Identifying candidates (need to free {self.get_mb(size_to_remove)})...")
            plan_path = os.path.join(APPDATA_DIR, f'p4cleaner_plan_{os.getpid()}_{int(time.time())}.txt')
            plan_size = 0
            plan_count = 0

            plan_cursor = conn.cursor()
            plan_cursor.execute("SELECT path, size FROM files ORDER BY atime ASC")
            with open(plan_path, 'w', encoding='utf-8') as pf:
                for path, size in plan_cursor:
                    if plan_size >= size_to_remove:
                        break
                    pf.write(f"{path}\t{size}\n")
                    plan_size += size
                    plan_count += 1
            plan_cursor.close()

            self.last_plan_path = plan_path
            on_log(f"Phase 2/4 done: {plan_count:,} files ({self.get_mb(plan_size)}) identified.")
            on_log(f"Phase 3/4: Candidate list saved to: {plan_path}")

            c.close()
            conn.close()
            try:
                os.remove(db_path)
            except FileNotFoundError:
                pass

            if self.dry_run:
                on_log("Dry run complete — no files deleted. Review the candidate list above.")
                on_progress(100)
                return

            on_log(f"Phase 4/4: Deleting {plan_count:,} files...")
            removed_size = 0
            deleted = 0
            failed = 0
            DELETE_BATCH = 1000
            DELETE_WORKERS = 10
            LOG_INTERVAL = 1000

            def _read_plan_batches():
                with open(plan_path, 'r', encoding='utf-8') as pf:
                    batch = []
                    for line in pf:
                        parts = line.rstrip('\n').split('\t', 1)
                        if len(parts) == 2:
                            batch.append((parts[0], int(parts[1])))
                            if len(batch) >= DELETE_BATCH:
                                yield batch
                                batch = []
                    if batch:
                        yield batch

            with ThreadPoolExecutor(max_workers=DELETE_WORKERS) as executor:
                for batch in _read_plan_batches():
                    if self._stop_event.is_set():
                        on_log("Stop requested, cleaning aborted.")
                        break
                    future_to_info = {
                        executor.submit(os.remove, path): (path, size)
                        for path, size in batch
                    }
                    for future in as_completed(future_to_info):
                        path, size = future_to_info[future]
                        exc = future.exception()
                        if exc is None:
                            removed_size += size
                            deleted += 1
                            if deleted % LOG_INTERVAL == 0:
                                on_log(f"  Deleted {deleted:,}/{plan_count:,} files "
                                       f"({self.get_mb(removed_size)})...")
                        else:
                            failed += 1
                            on_log(f"Failed: {path} — {exc}")
                    on_progress(round(100 * deleted / plan_count, 1) if plan_count else 100.0)

            fail_note = f", {failed:,} failed" if failed else ""
            on_log(f"Phase 4/4 done: {self.get_mb(removed_size)} removed, "
                   f"{deleted:,} files deleted{fail_note}.")
            on_log(f"Audit log retained at: {plan_path}")
            on_progress(100)

        except Exception as e:
            on_log(f"Exception occurred: {e}")
            logging.exception("clean() exception")
        finally:
            try:
                conn.close()
            except Exception:
                pass
            try:
                os.remove(db_path)
            except (FileNotFoundError, PermissionError):
                pass


class ThreadedCacheCleaner(threading.Thread, BaseCacheCleaner):
    """CLI worker — runs clean() in a background thread, printing to stdout."""

    def __init__(self, path, low_thresh, high_thresh, folder_keep_percent, drive_mode, dry_run=False,
                 exclude_files=None):
        threading.Thread.__init__(self)
        BaseCacheCleaner.__init__(self, path, low_thresh, high_thresh, folder_keep_percent, drive_mode, dry_run,
                                  exclude_files=list(exclude_files or []))

    def run(self):
        def print_and_log(msg):
            print(msg)
            logging.info(msg)
        self.clean(print_and_log, lambda _: None)


class QtCacheCleanerWorker(QThread):
    """GUI worker — runs clean() in a QThread, forwarding output via Qt signals."""
    progress_signal = Signal(float)
    log_signal = Signal(str)
    done_signal = Signal()

    def __init__(self, path, low_thresh, high_thresh, folder_keep_percent, drive_mode, dry_run=False,
                 exclude_files=None):
        QThread.__init__(self)
        self._cleaner = BaseCacheCleaner(path, low_thresh, high_thresh, folder_keep_percent, drive_mode, dry_run,
                                         exclude_files=list(exclude_files or []))

    def request_stop(self):
        self._cleaner.request_stop()

    def run(self):
        self._cleaner.clean(self.log_signal.emit, self.progress_signal.emit)
        self.done_signal.emit()


# --- Log pop-out window ---

class ArrowSpinBox(QSpinBox):
    """QSpinBox that shows an arrow cursor over the +/- buttons instead of the I-beam."""

    def mouseMoveEvent(self, event):
        over_buttons = event.position().x() >= self.width() - 18
        self.setCursor(Qt.CursorShape.ArrowCursor if over_buttons else Qt.CursorShape.IBeamCursor)
        super().mouseMoveEvent(event)


class LogPopOut(QDialog):
    """Detached, resizable log viewer that mirrors the main log in real time."""

    def __init__(self, parent: "P4PCleanUI", current_html: str, stylesheet: str):
        super().__init__(parent, Qt.WindowType.Window)
        self._owner = parent  # typed reference used in closeEvent
        self.setWindowTitle("Cleaning Log — Perforce Cache Cleaner")
        self.resize(900, 600)
        self.setStyleSheet(stylesheet)

        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        btn_row = QHBoxLayout()
        open_btn = QPushButton("Open Log File")
        open_btn.setObjectName("secondary_btn")
        open_btn.clicked.connect(lambda: parent._open_file(LOG_FILE))
        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("secondary_btn")
        clear_btn.clicked.connect(self._clear)
        btn_row.addStretch()
        btn_row.addWidget(open_btn)
        btn_row.addWidget(clear_btn)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 10))
        self.log_text.setHtml(current_html)

        layout.addLayout(btn_row)
        layout.addWidget(self.log_text)
        self.setLayout(layout)

    def append(self, message: str):
        self.log_text.append(message)

    def _clear(self):
        self.log_text.clear()

    def closeEvent(self, event):
        self._owner._popout = None
        event.accept()


# --- GUI ---

class P4PCleanUI(QWidget):
    """
    Qt GUI for the Perforce Proxy Cache Cleaner with modernized design and dark mode toggle.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Perforce Proxy Cache Cleaner")
        self.resize(700, 850)
        self.dark_mode = False
        self._popout: LogPopOut | None = None

        self.exclude_files = list(DEFAULT_EXCLUDE_FILES)

        main_layout = QVBoxLayout()
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(20, 20, 20, 20)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        header = QLabel("Perforce Proxy Cache Cleaner")
        header.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        subheader = QLabel("Automatically remove the oldest unused cache files to reclaim disk space.")
        subheader.setObjectName("subheader_label")
        title_col.addWidget(header)
        title_col.addWidget(subheader)

        self.theme_button = QPushButton("🌙  Dark")
        self.theme_button.setObjectName("theme_btn")
        self.theme_button.setCheckable(True)
        self.theme_button.setFixedSize(80, 30)
        self.theme_button.clicked.connect(self.toggle_theme)

        header_row.addLayout(title_col)
        header_row.addStretch()
        header_row.addWidget(self.theme_button, alignment=Qt.AlignmentFlag.AlignTop)
        main_layout.addLayout(header_row)

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

        options_group = QGroupBox("Cleaning Options")
        options_layout = QHBoxLayout()

        drive_page = QWidget()
        drive_page_layout = QHBoxLayout(drive_page)
        drive_page_layout.setContentsMargins(0, 0, 0, 0)
        self.low_thresh_input = ArrowSpinBox()
        self.low_thresh_input.setRange(1, 100)
        self.low_thresh_input.setValue(20)
        self.low_thresh_input.setSuffix("% (min free)")
        self.high_thresh_input = ArrowSpinBox()
        self.high_thresh_input.setRange(1, 100)
        self.high_thresh_input.setValue(30)
        self.high_thresh_input.setSuffix("% (target free)")
        drive_page_layout.addWidget(self.low_thresh_input)
        drive_page_layout.addWidget(self.high_thresh_input)
        drive_page_layout.addStretch()

        folder_page = QWidget()
        folder_page_layout = QHBoxLayout(folder_page)
        folder_page_layout.setContentsMargins(0, 0, 0, 0)
        self.percent_label = QLabel("Keep % of cache data:")
        self.percent_spin = ArrowSpinBox()
        self.percent_spin.setMinimum(1)
        self.percent_spin.setMaximum(100)
        self.percent_spin.setValue(80)
        folder_page_layout.addWidget(self.percent_label)
        folder_page_layout.addWidget(self.percent_spin)
        folder_page_layout.addStretch()

        # Stack holds both pages — layout never reflows when switching
        self.mode_stack = QStackedWidget()
        self.mode_stack.addWidget(drive_page)
        self.mode_stack.addWidget(folder_page)

        self.dry_run_checkbox = QCheckBox("Dry Run (simulate only)")

        options_layout.addWidget(self.mode_stack)
        options_layout.addWidget(self.dry_run_checkbox)
        options_group.setLayout(options_layout)

        self.exclude_group = QGroupBox(self._exclude_group_title())
        exclude_layout = QVBoxLayout()
        exclude_layout.setSpacing(6)

        hint = QLabel("Glob patterns supported: exact name, *.ext, prefix_*, etc. Matched case-insensitively against filenames.")
        hint.setObjectName("hint_label")
        hint.setWordWrap(True)

        self.exclude_list_widget = QListWidget()
        self.exclude_list_widget.setMinimumHeight(90)
        self.exclude_list_widget.setMaximumHeight(120)
        for item in self.exclude_files:
            self.exclude_list_widget.addItem(item)

        add_layout = QHBoxLayout()
        self.exclude_input = QLineEdit()
        self.exclude_input.setPlaceholderText("e.g.  *.lbr  or  cache_tmp_*  or  exact_file.txt")
        add_btn = QPushButton("Add")
        add_btn.setObjectName("secondary_btn")
        remove_btn = QPushButton("Remove")
        remove_btn.setObjectName("secondary_btn")
        add_layout.addWidget(self.exclude_input)
        add_layout.addWidget(add_btn)
        add_layout.addWidget(remove_btn)

        exclude_layout.addWidget(hint)
        exclude_layout.addWidget(self.exclude_list_widget)
        exclude_layout.addLayout(add_layout)
        self.exclude_group.setLayout(exclude_layout)

        add_btn.clicked.connect(self.add_exclude_file)
        remove_btn.clicked.connect(self.remove_exclude_file)
        self.exclude_input.returnPressed.connect(self.add_exclude_file)

        self.start_button = QPushButton("Start Cleaning")
        self.start_button.setStyleSheet(
            "QPushButton {background-color: #2d89ef; color: white; border-radius: 6px; padding: 8px 20px; font-size: 16px;} QPushButton:disabled {background-color: #999;}"
        )
        self.start_button.setFixedHeight(40)
        self.start_button.clicked.connect(self.start_cleaning)

        status_row = QHBoxLayout()
        self.status_bar = QLabel("Waiting to start...")
        self.status_bar.setObjectName("status_label")
        self.status_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.progress = QProgressBar()
        self.progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress.setFixedWidth(280)
        status_row.addWidget(self.status_bar)
        status_row.addWidget(self.progress)

        logs_group = QGroupBox("Logs")
        logs_layout = QVBoxLayout()
        logs_layout.setSpacing(6)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFont(QFont("Consolas", 10))
        self.log_output.setMinimumHeight(200)

        log_btn_row = QHBoxLayout()
        self.open_plan_button = QPushButton("Open Plan File")
        self.open_plan_button.setObjectName("secondary_btn")
        self.open_plan_button.setEnabled(False)
        self.open_plan_button.clicked.connect(self._open_plan_file)
        open_log_btn = QPushButton("Open Log File")
        open_log_btn.setObjectName("secondary_btn")
        open_log_btn.clicked.connect(lambda: self._open_file(LOG_FILE))
        popout_btn = QPushButton("Pop Out")
        popout_btn.setObjectName("secondary_btn")
        popout_btn.clicked.connect(self._show_popout)
        clear_log_btn = QPushButton("Clear")
        clear_log_btn.setObjectName("secondary_btn")
        clear_log_btn.clicked.connect(self.log_output.clear)
        log_btn_row.addStretch()
        log_btn_row.addWidget(self.open_plan_button)
        log_btn_row.addWidget(open_log_btn)
        log_btn_row.addWidget(popout_btn)
        log_btn_row.addWidget(clear_log_btn)

        logs_layout.addLayout(log_btn_row)
        logs_layout.addWidget(self.log_output)
        logs_group.setLayout(logs_layout)

        main_layout.addWidget(path_group)
        main_layout.addWidget(mode_group)
        main_layout.addWidget(options_group)
        main_layout.addWidget(self.exclude_group)
        main_layout.addWidget(self.start_button)
        main_layout.addWidget(logs_group, stretch=1)
        main_layout.addLayout(status_row)
        self.setLayout(main_layout)

        self.drive_radio.toggled.connect(self.update_ui_fields)
        self.folder_radio.toggled.connect(self.update_ui_fields)
        self.update_ui_fields()

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
        self.dark_mode = not self.dark_mode
        if self.dark_mode:
            self.setStyleSheet(self.dark_stylesheet)
            self.theme_button.setText("☀️  Light")
        else:
            self.setStyleSheet(self.light_stylesheet)
            self.theme_button.setText("🌙  Dark")
        self._apply_title_bar_theme(self.dark_mode)

    def _exclude_group_title(self) -> str:
        n = len(self.exclude_files)
        return f"Excluded Files — {n} pattern{'s' if n != 1 else ''}"

    def add_exclude_file(self):
        text = self.exclude_input.text().strip().lower()
        if text and text not in self.exclude_files:
            self.exclude_files.append(text)
            self.exclude_list_widget.addItem(text)
            self.exclude_input.clear()
            self.exclude_group.setTitle(self._exclude_group_title())

    def remove_exclude_file(self):
        for item in self.exclude_list_widget.selectedItems():
            self.exclude_files.remove(item.text())
            self.exclude_list_widget.takeItem(self.exclude_list_widget.row(item))
        self.exclude_group.setTitle(self._exclude_group_title())

    def update_ui_fields(self):
        self.mode_stack.setCurrentIndex(0 if self.drive_radio.isChecked() else 1)

    def on_progress_update(self, value):
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
        dir_path = QFileDialog.getExistingDirectory(self, "Select Cache Directory")
        if dir_path:
            self.path_input.setText(dir_path)

    def append_log(self, message):
        self._gui_log_buffer.append(message)
        self.log_output.append(message)
        if self._popout is not None:
            self._popout.append(message)
        logging.info(message)

    def _open_file(self, path: str):
        if not os.path.exists(path):
            return
        if hasattr(os, "startfile"):
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])

    def _open_plan_file(self):
        plan_path = getattr(getattr(self, "cleaner", None), "_cleaner", None)
        plan_path = getattr(plan_path, "last_plan_path", None)
        if plan_path:
            self._open_file(plan_path)

    def _show_popout(self):
        if self._popout is not None and self._popout.isVisible():
            self._popout.raise_()
            self._popout.activateWindow()
            return
        stylesheet = self.dark_stylesheet if self.dark_mode else self.light_stylesheet
        self._popout = LogPopOut(self, self.log_output.toHtml(), stylesheet)
        self._popout.show()

    def start_cleaning(self):
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
        self.append_log("<b>Cleaning operation finished.</b>")
        self.status_bar.setText("Done.")
        self.start_button.setEnabled(True)
        plan_path = getattr(getattr(self, "cleaner", None), "_cleaner", None)
        plan_path = getattr(plan_path, "last_plan_path", None)
        if plan_path and os.path.exists(plan_path):
            self.open_plan_button.setEnabled(True)
            self.open_plan_button.setToolTip(plan_path)


# --- CLI entry point ---

def run_headless(path, low_thresh, high_thresh, folder_percent_keep, drive_mode, dry_run, exclude_files):
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
        app = QApplication(sys.argv)
        app.setWindowIcon(QIcon(resource_path("resources/icons/icon.png")))
        window = P4PCleanUI()
        window.resize(650, 1050)
        window.show()
        sys.exit(app.exec())


if __name__ == "__main__":
    main()
