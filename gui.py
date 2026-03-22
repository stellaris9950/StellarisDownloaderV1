import logging
import sys
import os
import json
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QLabel, QLineEdit, QComboBox,
    QPushButton, QTextEdit, QSplitter, QFrame, QScrollArea,
    QDialog, QDialogButtonBox, QFormLayout, QFileDialog,
    QMenuBar, QMenu, QMessageBox, QProgressBar, QCheckBox, QProgressDialog
)
from PySide6.QtCore import Qt, QThread, Signal, QSize, QUrl, QByteArray, QTimer, QObject, Slot, QUrlQuery
from PySide6.QtGui import QPixmap, QDesktopServices, QAction, QTextCursor, QTextCharFormat, QColor, QFont
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel

from core.app_updater import UpdateError, check_for_updates, download_release_asset, launch_updater_for_package
from core.database import ModDatabase
from core.i18n import tr
from core.library_root import rebuild_database_from_library_root, switch_library_root, validate_library_root
from core.runtime_paths import configure_logging, get_db_path, get_settings_path
from core.settings import SettingsManager
from core.updater import check_all_mods_for_updates, update_mod, update_all_mods
from core.version import __version__
from core.steamcmd import download_mod
from core.workshop_api import fetch_mod_metadata


def resolve_workshop_title(workshop_id, db_path=None):
    if db_path:
        mod_record = ModDatabase(db_path).get_mod(workshop_id)
        if mod_record and mod_record.get("title"):
            return mod_record["title"]

    metadata = fetch_mod_metadata(workshop_id)
    if metadata and metadata.get("title"):
        return metadata["title"]
    return tr("unknown_mod")


class WorkshopTitleLookupThread(QThread):
    resolved = Signal(str, str)

    def __init__(self, workshop_id, db_path=None):
        super().__init__()
        self.workshop_id = workshop_id
        self.db_path = db_path

    def run(self):
        title = resolve_workshop_title(self.workshop_id, self.db_path)
        self.resolved.emit(self.workshop_id, title)


class AppUpdateCheckThread(QThread):
    finished = Signal(dict)
    error = Signal(str)

    def run(self):
        try:
            self.finished.emit(check_for_updates())
        except Exception as e:
            self.error.emit(str(e))


class AppUpdateDownloadThread(QThread):
    progress = Signal(int, int)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, release_info):
        super().__init__()
        self.release_info = release_info

    def run(self):
        try:
            path = download_release_asset(
                self.release_info,
                progress_callback=lambda current, total: self.progress.emit(current, total),
            )
            self.finished.emit(str(path))
        except Exception as e:
            self.error.emit(str(e))


class WorkshopQueueBridge(QObject):
    queue_toggled = Signal(str)

    @Slot(str)
    def toggleQueueItem(self, workshop_id):
        self.queue_toggled.emit(workshop_id)


class ModListItem(QListWidgetItem):
    """Custom list item for mod display."""
    
    def __init__(self, mod_data):
        super().__init__()
        self.mod_data = mod_data
        self.setText(mod_data.get('title') or tr('unknown_mod'))
    
    def get_sort_key(self, sort_by):
        """Get sort key based on sort criteria."""
        if sort_by == 'alphabetical':
            return (self.mod_data.get('title') or '').lower()
        elif sort_by == 'last_workshop_update':
            return self.mod_data.get('remote_updated_at', 0)
        elif sort_by == 'last_download_time':
            return self.mod_data.get('last_downloaded_at', 0)
        elif sort_by == 'file_size':
            # Calculate file size if content_path exists
            content_path = self.mod_data.get('content_path')
            if content_path and os.path.exists(content_path):
                try:
                    total_size = 0
                    for dirpath, dirnames, filenames in os.walk(content_path):
                        for filename in filenames:
                            filepath = os.path.join(dirpath, filename)
                            total_size += os.path.getsize(filepath)
                    return total_size
                except:
                    pass
            return 0
        return 0

class DownloadFromUrlIdDialog(QDialog):
    """Dialog for entering workshop IDs and queueing downloads."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.setWindowTitle(tr("dialog_download_workshop_mods"))
        self.setModal(True)
        self.resize(520, 400)

        self.queue = []
        self.queue_titles = {}
        self.title_lookup_threads = {}

        main_layout = QVBoxLayout(self)

        input_layout = QHBoxLayout()
        self.workshop_id_edit = QLineEdit()
        self.workshop_id_edit.setPlaceholderText(tr("label_workshop_id_or_url"))
        input_layout.addWidget(self.workshop_id_edit)

        self.add_button = QPushButton(tr("button_add_to_list"))
        self.add_button.clicked.connect(self.add_to_list)
        input_layout.addWidget(self.add_button)

        self.clear_button = QPushButton(tr("button_clear"))
        self.clear_button.clicked.connect(self.clear_input)
        input_layout.addWidget(self.clear_button)

        main_layout.addLayout(input_layout)

        self.download_button = QPushButton(tr("button_download"))
        self.download_button.clicked.connect(self.on_download)
        self.download_button.setMinimumHeight(40)
        main_layout.addWidget(self.download_button)

        queue_controls = QHBoxLayout()
        self.queue_label = QLabel(f"{tr('label_mods_to_download')} (0)")
        queue_controls.addWidget(self.queue_label)

        queue_controls.addStretch()

        self.remove_selected_button = QPushButton(tr("button_remove_selected"))
        self.remove_selected_button.clicked.connect(self.remove_selected)
        queue_controls.addWidget(self.remove_selected_button)

        self.clear_list_button = QPushButton(tr("button_clear_list"))
        self.clear_list_button.clicked.connect(self.clear_list)
        queue_controls.addWidget(self.clear_list_button)

        main_layout.addLayout(queue_controls)

        self.queue_list = QListWidget()
        self.queue_list.setSelectionMode(QListWidget.MultiSelection)
        self.queue_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.queue_list.customContextMenuRequested.connect(self.show_queue_context_menu)
        main_layout.addWidget(self.queue_list)

        bottom_buttons = QDialogButtonBox(QDialogButtonBox.Close)
        bottom_buttons.rejected.connect(self.reject)
        main_layout.addWidget(bottom_buttons)

        self.setLayout(main_layout)

    @staticmethod
    def extract_workshop_id(raw_text):
        raw_text = (raw_text or "").strip()
        if not raw_text:
            return None

        # direct numeric id
        if raw_text.isdigit():
            return raw_text

        try:
            from urllib.parse import urlparse, parse_qs
            import re

            parsed = urlparse(raw_text)
            query = parse_qs(parsed.query)

            workshop_id = None
            if "id" in query and query["id"] and query["id"][0].isdigit():
                workshop_id = query["id"][0]

            if not workshop_id:
                # path e.g. /sharedfiles/filedetails/?id=123 or /filedetails/123/
                path_parts = [part for part in parsed.path.split("/") if part]
                if path_parts and path_parts[-1].isdigit():
                    workshop_id = path_parts[-1]

            if not workshop_id:
                # fallback: find long numeric token, ignoring searchtext possibility
                pooled = raw_text
                match = re.search(r"(\d{6,20})", pooled)
                if match:
                    workshop_id = match.group(1)

            if workshop_id and workshop_id.isdigit():
                return workshop_id
        except Exception:
            pass

        return None

    def update_queue_ui(self):
        self.queue_list.clear()
        for wid in self.queue:
            item = QListWidgetItem(
                tr("label_queue_item").format(
                    title=self.queue_titles.get(wid, tr("unknown_mod")),
                    workshop_id=wid
                )
            )
            item.setData(Qt.UserRole, wid)
            self.queue_list.addItem(item)
        self.queue_label.setText(f"{tr('label_mods_to_download')} ({len(self.queue)})")

    def ensure_queue_title_async(self, workshop_id):
        if self.queue_titles.get(workshop_id) not in {None, tr("unknown_mod")}:
            return
        if workshop_id in self.title_lookup_threads:
            return

        db_path = self.parent_window.db_path if self.parent_window else None
        worker = WorkshopTitleLookupThread(workshop_id, db_path)
        worker.resolved.connect(self.on_queue_title_resolved)
        worker.finished.connect(lambda wid=workshop_id: self.title_lookup_threads.pop(wid, None))
        self.title_lookup_threads[workshop_id] = worker
        worker.start()

    def on_queue_title_resolved(self, workshop_id, title):
        self.queue_titles[workshop_id] = title or tr("unknown_mod")
        if workshop_id in self.queue:
            self.update_queue_ui()

    def add_to_list(self):
        raw = self.workshop_id_edit.text().strip()
        wid = self.extract_workshop_id(raw)
        if not wid:
            QMessageBox.warning(self, tr("warning_invalid_workshop_id_title"), tr("warning_invalid_workshop_id_message"))
            return
        if wid in self.queue:
            QMessageBox.information(self, tr("info_duplicate_title"), tr("info_duplicate_message").format(workshop_id=wid))
            self.workshop_id_edit.clear()
            return

        self.queue.append(wid)
        self.queue_titles.setdefault(wid, tr("unknown_mod"))
        self.update_queue_ui()
        self.ensure_queue_title_async(wid)
        self.workshop_id_edit.clear()

    def clear_input(self):
        self.workshop_id_edit.clear()

    def remove_selected(self):
        selections = self.queue_list.selectedItems()
        if not selections:
            return
        for item in selections:
            wid = item.data(Qt.UserRole)
            if wid in self.queue:
                self.queue.remove(wid)
        self.update_queue_ui()

    def clear_list(self):
        self.queue = []
        self.update_queue_ui()

    def show_queue_context_menu(self, position):
        item = self.queue_list.itemAt(position)
        if not item:
            return

        selected_items = self.queue_list.selectedItems()
        selected_ids = [selected_item.data(Qt.UserRole) for selected_item in selected_items]
        wid = item.data(Qt.UserRole)
        remove_ids = [wid]
        remove_label = tr("button_remove_this_mod")
        if len(selected_ids) > 1 and wid in selected_ids:
            remove_ids = selected_ids
            remove_label = tr("button_remove_selected_mods")

        menu = QMenu(self)
        remove_action = menu.addAction(remove_label)
        chosen_action = menu.exec(self.queue_list.mapToGlobal(position))
        if chosen_action == remove_action:
            self.queue = [queue_id for queue_id in self.queue if queue_id not in remove_ids]
            self.update_queue_ui()

    def on_download(self):
        current_raw = self.workshop_id_edit.text().strip()
        current_id = self.extract_workshop_id(current_raw)

        if self.queue and current_id:
            response = QMessageBox.question(
                self,
                tr("question_add_current_input_title"),
                tr("question_add_current_input_message").format(workshop_id=current_id),
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes
            )

            if response == QMessageBox.Cancel:
                return
            if response == QMessageBox.Yes:
                if current_id not in self.queue:
                    self.queue.append(current_id)
                    self.queue_titles.setdefault(current_id, tr("unknown_mod"))
                    self.ensure_queue_title_async(current_id)
                self.update_queue_ui()
            # if No then just use existing queue without adding input item

        if not self.queue:
            if not current_id:
                QMessageBox.warning(self, tr("warning_no_mod_selected_title"), tr("warning_no_mod_selected_message"))
                return
            self.queue = [current_id]
            self.queue_titles.setdefault(current_id, tr("unknown_mod"))
            self.ensure_queue_title_async(current_id)

        # now queue has item(s)
        started = self.parent_window.start_download_for_ids(self.queue.copy())
        if not started:
            return
        # clear queue after starting
        self.queue = []
        self.update_queue_ui()
        self.workshop_id_edit.clear()


class RestrictedWorkshopPage(QWebEnginePage):
    """Restrict browser navigation to Steam-owned pages."""

    def __init__(self, block_callback=None, queue_toggle_callback=None, parent=None):
        super().__init__(parent)
        self.block_callback = block_callback
        self.queue_toggle_callback = queue_toggle_callback

    @staticmethod
    def is_allowed_url(url):
        if not url.isValid():
            return False
        if url.scheme() == "about" and url.toString() == "about:blank":
            return True
        if url.scheme() not in {"http", "https"}:
            return False
        host = url.host().lower()
        allowed_hosts = {
            "steamcommunity.com",
            "store.steampowered.com",
            "help.steampowered.com",
            "steampowered.com",
            "www.steamcommunity.com",
            "www.steampowered.com",
        }
        if host in allowed_hosts:
            return True
        return host.endswith(".steampowered.com")

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.scheme() == "stellarisqueue":
            if self.queue_toggle_callback:
                query = QUrlQuery(url)
                workshop_id = query.queryItemValue("id")
                if workshop_id:
                    QTimer.singleShot(0, lambda wid=workshop_id: self.queue_toggle_callback(wid))
            return False

        allowed = self.is_allowed_url(url)
        if allowed:
            return True
        if is_main_frame and self.block_callback:
            QTimer.singleShot(0, self.block_callback)
        return False

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        queue_prefix = "__STELLARIS_QUEUE__"
        if message.startswith(queue_prefix) and self.queue_toggle_callback:
            workshop_id = message[len(queue_prefix):].strip()
            if workshop_id:
                QTimer.singleShot(0, lambda wid=workshop_id: self.queue_toggle_callback(wid))
            return
        super().javaScriptConsoleMessage(level, message, line_number, source_id)


class WorkshopBrowserDialog(QDialog):
    """Embedded browser shell for browsing the Stellaris Workshop."""

    WORKSHOP_URL = "https://steamcommunity.com/app/281990/workshop/"
    WORKSHOP_CARD_ROOT_SELECTORS = [
        ".workshopItem",
        ".workshopItemCollection",
        ".browseItem",
        ".item",
        ".search_result_row",
        ".workshopBrowseItems > div",
        ".workshopBrowseItems .workshopItemPreviewHolder",
        ".workshopItemPreviewHolder",
    ]
    WORKSHOP_LINK_SELECTOR = 'a[href*="sharedfiles/filedetails"]'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.queue = []
        self.queue_titles = {}
        self.title_lookup_threads = {}
        self.current_workshop_id = None
        self.queue_bridge = WorkshopQueueBridge()
        self.queue_bridge.queue_toggled.connect(self.toggle_queue_item_from_js)

        self.setWindowTitle(tr("dialog_workshop_browser"))
        self.resize(1400, 850)
        self.setModal(True)

        main_layout = QVBoxLayout(self)

        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter, 1)

        queue_panel = QWidget()
        queue_layout = QVBoxLayout(queue_panel)
        queue_layout.setContentsMargins(8, 8, 8, 8)
        queue_layout.setSpacing(8)

        self.queue_label = QLabel(f"{tr('label_selected_mods')} (0)")
        queue_layout.addWidget(self.queue_label)

        self.queue_list = QListWidget()
        self.queue_list.setSelectionMode(QListWidget.MultiSelection)
        self.queue_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.queue_list.customContextMenuRequested.connect(self.show_queue_context_menu)
        queue_layout.addWidget(self.queue_list, 1)

        self.queue_add_button = QPushButton(tr("button_add_to_list"))
        self.queue_add_button.clicked.connect(self.add_current_mod)
        queue_layout.addWidget(self.queue_add_button)

        self.download_queue_button = QPushButton(tr("button_download_queue"))
        self.download_queue_button.clicked.connect(self.download_queue)
        queue_layout.addWidget(self.download_queue_button)

        self.remove_selected_button = QPushButton(tr("button_remove_selected"))
        self.remove_selected_button.clicked.connect(self.remove_selected)
        queue_layout.addWidget(self.remove_selected_button)

        self.clear_list_button = QPushButton(tr("button_clear_list"))
        self.clear_list_button.clicked.connect(self.clear_list)
        queue_layout.addWidget(self.clear_list_button)

        splitter.addWidget(queue_panel)

        browser_panel = QWidget()
        browser_layout = QVBoxLayout(browser_panel)
        browser_layout.setContentsMargins(8, 8, 8, 8)
        browser_layout.setSpacing(8)

        self.page_title_label = QLabel(tr("label_current_page"))
        browser_layout.addWidget(self.page_title_label)

        self.url_edit = QLineEdit()
        self.url_edit.setReadOnly(True)
        browser_layout.addWidget(self.url_edit)

        nav_layout = QHBoxLayout()
        self.back_button = QPushButton(tr("button_back"))
        self.back_button.clicked.connect(self.go_back)
        nav_layout.addWidget(self.back_button)

        self.forward_button = QPushButton(tr("button_forward"))
        self.forward_button.clicked.connect(self.go_forward)
        nav_layout.addWidget(self.forward_button)

        self.reload_button = QPushButton(tr("button_reload"))
        self.reload_button.clicked.connect(self.reload_page)
        nav_layout.addWidget(self.reload_button)

        self.add_current_button = QPushButton(tr("button_add_current_mod"))
        self.add_current_button.clicked.connect(self.add_current_mod)
        self.add_current_button.setVisible(False)
        nav_layout.addWidget(self.add_current_button)

        nav_layout.addStretch()
        browser_layout.addLayout(nav_layout)

        self.browser_view = QWebEngineView()
        self.browser_page = RestrictedWorkshopPage(self.show_blocked_page, self.toggle_queue_item_from_js, self.browser_view)
        self.web_channel = QWebChannel(self.browser_page)
        self.web_channel.registerObject("stellarisBridge", self.queue_bridge)
        self.browser_page.setWebChannel(self.web_channel)
        self.browser_view.setPage(self.browser_page)
        self.browser_view.urlChanged.connect(self.on_browser_url_changed)
        self.browser_view.titleChanged.connect(self.on_browser_title_changed)
        self.browser_view.loadFinished.connect(self.on_browser_load_finished)
        browser_layout.addWidget(self.browser_view, 1)

        splitter.addWidget(browser_panel)

        splitter.setSizes([360, 1040])

        self.update_current_mod_state()
        self.browser_view.setUrl(QUrl(self.WORKSHOP_URL))

    @staticmethod
    def extract_mod_page_workshop_id(url):
        if isinstance(url, QUrl):
            url = url.toString()
        if not url:
            return None

        parsed_url = QUrl(url)
        if parsed_url.host().lower() != "steamcommunity.com":
            return None
        if not parsed_url.path().lower().startswith("/sharedfiles/filedetails"):
            return None
        return DownloadFromUrlIdDialog.extract_workshop_id(url)

    def show_blocked_page(self):
        self.browser_view.setHtml("")
        self.url_edit.clear()
        self.page_title_label.setText(tr("label_current_page"))
        self.current_workshop_id = None
        self.update_current_mod_state()

    def update_current_mod_state(self):
        has_mod = bool(self.current_workshop_id)
        self.add_current_button.setVisible(has_mod)
        self.queue_add_button.setEnabled(has_mod)

    def get_downloaded_workshop_ids(self):
        if not self.parent_window:
            return []
        db = ModDatabase(self.parent_window.db_path)
        return [
            mod["workshop_id"]
            for mod in db.list_all_mods()
            if mod.get("status") == "success"
        ]

    def get_queue_sync_script(self):
        queued_ids_json = json.dumps(self.queue)
        downloaded_ids_json = json.dumps(self.get_downloaded_workshop_ids())
        add_tooltip = json.dumps(tr("tooltip_add_to_queue"))
        remove_tooltip = json.dumps(tr("tooltip_remove_from_queue"))
        downloaded_tooltip = json.dumps(tr("tooltip_already_downloaded"))
        card_selectors_json = json.dumps(self.WORKSHOP_CARD_ROOT_SELECTORS)
        link_selector_json = json.dumps(self.WORKSHOP_LINK_SELECTOR)

        return f"""
(function() {{
    const cardSelectors = {card_selectors_json};
    const linkSelector = {link_selector_json};
    const queuedIds = {queued_ids_json};
    const downloadedIds = {downloaded_ids_json};
    const addTooltip = {add_tooltip};
    const removeTooltip = {remove_tooltip};
    const downloadedTooltip = {downloaded_tooltip};

    function ensureBridge(callback) {{
        function initChannel() {{
            if (window.stellarisBridge) {{
                callback();
                return;
            }}
            new QWebChannel(qt.webChannelTransport, function(channel) {{
                window.stellarisBridge = channel.objects.stellarisBridge;
                callback();
            }});
        }}

        if (window.stellarisBridge) {{
            callback();
            return;
        }}
        if (typeof QWebChannel !== 'undefined' && window.qt && qt.webChannelTransport) {{
            initChannel();
            return;
        }}
        if (!document.getElementById('stellaris-qt-webchannel-script')) {{
            const script = document.createElement('script');
            script.id = 'stellaris-qt-webchannel-script';
            script.src = 'qrc:///qtwebchannel/qwebchannel.js';
            script.onload = initChannel;
            document.head.appendChild(script);
        }}
    }}

    function requestToggle(workshopId) {{
        ensureBridge(function() {{
            if (window.stellarisBridge && typeof window.stellarisBridge.toggleQueueItem === 'function') {{
                window.stellarisBridge.toggleQueueItem(workshopId);
            }}
        }});
    }}

    function extractWorkshopId(url) {{
        if (!url) return null;
        try {{
            const parsed = new URL(url, window.location.origin);
            const id = parsed.searchParams.get('id');
            if (id && /^\\d+$/.test(id)) return id;
            const pathParts = parsed.pathname.split('/').filter(Boolean);
            for (let i = pathParts.length - 1; i >= 0; i--) {{
                if (/^\\d+$/.test(pathParts[i])) return pathParts[i];
            }}
        }} catch (error) {{
            return null;
        }}
        return null;
    }}

    function findCardRoot(link) {{
        for (const selector of cardSelectors) {{
            const card = link.closest(selector);
            if (card) return card;
        }}
        return null;
    }}

    function applyButtonState(button, workshopId) {{
        if (downloadedIds.includes(workshopId)) {{
            button.textContent = '✓';
            button.title = downloadedTooltip;
            button.dataset.state = 'downloaded';
            button.style.background = '#48b64a';
            button.style.cursor = 'default';
            return;
        }}
        if (queuedIds.includes(workshopId)) {{
            button.textContent = '−';
            button.title = removeTooltip;
            button.dataset.state = 'queued';
            button.style.background = '#f5a623';
            button.style.cursor = 'pointer';
            return;
        }}
        button.textContent = '+';
        button.title = addTooltip;
        button.dataset.state = 'available';
        button.style.background = '#2f8ef3';
        button.style.cursor = 'pointer';
    }}

    function createButton(card, workshopId) {{
        let button = card.querySelector('.stellaris-queue-button');
        if (!button) {{
            if (getComputedStyle(card).position === 'static') {{
                card.style.position = 'relative';
            }}
            button = document.createElement('button');
            button.className = 'stellaris-queue-button';
            button.type = 'button';
            button.style.position = 'absolute';
            button.style.top = '10px';
            button.style.right = '10px';
            button.style.width = '48px';
            button.style.height = '48px';
            button.style.border = '0';
            button.style.borderRadius = '12px';
            button.style.boxShadow = '0 6px 18px rgba(0,0,0,0.35)';
            button.style.color = '#ffffff';
            button.style.fontSize = '30px';
            button.style.fontWeight = '700';
            button.style.lineHeight = '1';
            button.style.zIndex = '999';
            button.style.opacity = '0.92';
            button.style.transition = 'transform 0.15s ease, opacity 0.15s ease';
            button.onmouseenter = () => {{ button.style.transform = 'scale(1.06)'; button.style.opacity = '1'; }};
            button.onmouseleave = () => {{ button.style.transform = 'scale(1)'; button.style.opacity = '0.92'; }};
            button.addEventListener('click', function(event) {{
                event.preventDefault();
                event.stopPropagation();
                if (!window.stellarisBridge) return;
                if (button.dataset.state === 'downloaded') return;
                window.stellarisBridge.toggleQueueItem(workshopId);
            }});
            card.appendChild(button);
        }}
        button.dataset.workshopId = workshopId;
        applyButtonState(button, workshopId);
    }}

    function injectButtons() {{
        const links = document.querySelectorAll(linkSelector);
        links.forEach((link) => {{
            const workshopId = extractWorkshopId(link.href);
            if (!workshopId) return;
            const card = findCardRoot(link);
            if (!card) return;
            createButton(card, workshopId);
        }});
    }}

    ensureBridge(function() {{
        injectButtons();
        if (!window.__stellarisQueueObserver) {{
            window.__stellarisQueueObserver = new MutationObserver(function() {{
                injectButtons();
            }});
            window.__stellarisQueueObserver.observe(document.body, {{
                childList: true,
                subtree: true
            }});
        }}
        window.__stellarisInjectButtons = injectButtons;
    }});
}})();
"""

    def get_queue_sync_script(self):
        queued_ids_json = json.dumps(self.queue)
        downloaded_ids_json = json.dumps(self.get_downloaded_workshop_ids())
        add_tooltip = json.dumps(tr("tooltip_add_to_queue"))
        remove_tooltip = json.dumps(tr("tooltip_remove_from_queue"))
        downloaded_tooltip = json.dumps(tr("tooltip_already_downloaded"))
        card_selectors_json = json.dumps(self.WORKSHOP_CARD_ROOT_SELECTORS)
        link_selector_json = json.dumps(self.WORKSHOP_LINK_SELECTOR)

        return f"""
(function() {{
    const cardSelectors = {card_selectors_json};
    const linkSelector = {link_selector_json};
    const queuedIds = {queued_ids_json};
    const downloadedIds = {downloaded_ids_json};
    const addTooltip = {add_tooltip};
    const removeTooltip = {remove_tooltip};
    const downloadedTooltip = {downloaded_tooltip};

    function ensureBridge(callback) {{
        function initChannel() {{
            if (window.stellarisBridge) {{
                callback();
                return;
            }}
            if (typeof QWebChannel === 'function' && window.qt && qt.webChannelTransport) {{
                new QWebChannel(qt.webChannelTransport, function(channel) {{
                    window.stellarisBridge = channel.objects.stellarisBridge;
                    callback();
                }});
            }}
        }}

        if (window.stellarisBridge) {{
            callback();
            return;
        }}
        if (typeof QWebChannel === 'function' && window.qt && qt.webChannelTransport) {{
            initChannel();
            return;
        }}
        if (!document.getElementById('stellaris-qt-webchannel-script')) {{
            const script = document.createElement('script');
            script.id = 'stellaris-qt-webchannel-script';
            script.src = 'qrc:///qtwebchannel/qwebchannel.js';
            script.onload = initChannel;
            document.head.appendChild(script);
        }}
    }}

    function requestToggle(workshopId) {{
        console.info('__STELLARIS_QUEUE__' + workshopId);
    }}

    function extractWorkshopId(url) {{
        if (!url) return null;
        try {{
            const parsed = new URL(url, window.location.origin);
            const id = parsed.searchParams.get('id');
            if (id && /^\\d+$/.test(id)) return id;
            const pathParts = parsed.pathname.split('/').filter(Boolean);
            for (let i = pathParts.length - 1; i >= 0; i--) {{
                if (/^\\d+$/.test(pathParts[i])) return pathParts[i];
            }}
        }} catch (error) {{
            return null;
        }}
        return null;
    }}

    function findCardRoot(link) {{
        for (const selector of cardSelectors) {{
            const card = link.closest(selector);
            if (card) return card;
        }}
        let node = link;
        for (let i = 0; i < 6 && node; i++) {{
            if (node.querySelector && node.querySelector('img')) {{
                return node;
            }}
            node = node.parentElement;
        }}
        return null;
    }}

    function applyButtonState(button, workshopId) {{
        if (downloadedIds.includes(workshopId)) {{
            button.textContent = '\\u2713';
            button.title = downloadedTooltip;
            button.dataset.state = 'downloaded';
            button.style.background = '#48b64a';
            button.style.cursor = 'default';
            return;
        }}
        if (queuedIds.includes(workshopId)) {{
            button.textContent = '\\u2212';
            button.title = removeTooltip;
            button.dataset.state = 'queued';
            button.style.background = '#f5a623';
            button.style.cursor = 'pointer';
            return;
        }}
        button.textContent = '+';
        button.title = addTooltip;
        button.dataset.state = 'available';
        button.style.background = '#2f8ef3';
        button.style.cursor = 'pointer';
    }}

    function applyOptimisticToggle(button) {{
        if (button.dataset.state === 'downloaded') {{
            return;
        }}
        if (button.dataset.state === 'queued') {{
            const queuedIndex = queuedIds.indexOf(button.dataset.workshopId);
            if (queuedIndex >= 0) {{
                queuedIds.splice(queuedIndex, 1);
            }}
            button.textContent = '+';
            button.title = addTooltip;
            button.dataset.state = 'available';
            button.style.background = '#2f8ef3';
            button.style.cursor = 'pointer';
            return;
        }}
        if (!queuedIds.includes(button.dataset.workshopId)) {{
            queuedIds.push(button.dataset.workshopId);
        }}
        button.textContent = '\\u2212';
        button.title = removeTooltip;
        button.dataset.state = 'queued';
        button.style.background = '#f5a623';
        button.style.cursor = 'pointer';
    }}

    function createButton(card, workshopId) {{
        let button = card.querySelector('.stellaris-queue-button');
        if (!button) {{
            if (getComputedStyle(card).position === 'static') {{
                card.style.position = 'relative';
            }}
            button = document.createElement('button');
            button.className = 'stellaris-queue-button';
            button.type = 'button';
            button.style.position = 'absolute';
            button.style.top = '8px';
            button.style.right = '8px';
            button.style.width = '34px';
            button.style.height = '34px';
            button.style.border = '0';
            button.style.borderRadius = '10px';
            button.style.boxShadow = '0 6px 18px rgba(0,0,0,0.35)';
            button.style.color = '#ffffff';
            button.style.fontSize = '20px';
            button.style.fontWeight = '700';
            button.style.lineHeight = '1';
            button.style.zIndex = '9999';
            button.style.display = 'flex';
            button.style.alignItems = 'center';
            button.style.justifyContent = 'center';
            button.style.opacity = '0';
            button.style.pointerEvents = 'auto';
            button.style.transition = 'transform 0.15s ease, opacity 0.15s ease';
            card.addEventListener('mouseenter', function() {{
                button.style.opacity = '0.94';
            }});
            card.addEventListener('mouseleave', function() {{
                button.style.opacity = '0';
                button.style.transform = 'scale(1)';
            }});
            button.onmouseenter = () => {{ button.style.transform = 'scale(1.06)'; button.style.opacity = '1'; }};
            button.onmouseleave = () => {{ button.style.transform = 'scale(1)'; button.style.opacity = '0.94'; }};
            button.addEventListener('click', function(event) {{
                event.preventDefault();
                event.stopPropagation();
                if (event.stopImmediatePropagation) event.stopImmediatePropagation();
                if (button.dataset.state === 'downloaded') return;
                applyOptimisticToggle(button);
                requestToggle(workshopId);
            }});
            card.appendChild(button);
        }}
        button.dataset.workshopId = workshopId;
        applyButtonState(button, workshopId);
    }}

    function injectButtons() {{
        const links = document.querySelectorAll(linkSelector);
        links.forEach((link) => {{
            const workshopId = extractWorkshopId(link.href);
            if (!workshopId) return;
            const card = findCardRoot(link);
            if (!card) return;
            createButton(card, workshopId);
        }});
    }}

    injectButtons();
    ensureBridge(function() {{
        injectButtons();
    }});
    if (!window.__stellarisQueueObserver) {{
        window.__stellarisQueueObserver = new MutationObserver(function() {{
            injectButtons();
        }});
        window.__stellarisQueueObserver.observe(document.body, {{
            childList: true,
            subtree: true
        }});
    }}
    window.__stellarisInjectButtons = injectButtons;
    setTimeout(injectButtons, 300);
    setTimeout(injectButtons, 1000);
    setTimeout(injectButtons, 2000);
}})();
"""

    def sync_browser_queue_state(self):
        if not self.browser_view:
            return
        self.browser_view.page().runJavaScript(self.get_queue_sync_script())

    def on_browser_load_finished(self, _ok):
        self.sync_browser_queue_state()

    def update_queue_ui(self):
        self.queue_list.clear()
        for wid in self.queue:
            item = QListWidgetItem(
                tr("label_queue_item").format(
                    title=self.queue_titles.get(wid, tr("unknown_mod")),
                    workshop_id=wid
                )
            )
            item.setData(Qt.UserRole, wid)
            self.queue_list.addItem(item)
        self.queue_label.setText(f"{tr('label_selected_mods')} ({len(self.queue)})")
        self.sync_browser_queue_state()

    def ensure_queue_title_async(self, workshop_id):
        if self.queue_titles.get(workshop_id) not in {None, tr("unknown_mod")}:
            return
        if workshop_id in self.title_lookup_threads:
            return

        db_path = self.parent_window.db_path if self.parent_window else None
        worker = WorkshopTitleLookupThread(workshop_id, db_path)
        worker.resolved.connect(self.on_queue_title_resolved)
        worker.finished.connect(lambda wid=workshop_id: self.title_lookup_threads.pop(wid, None))
        self.title_lookup_threads[workshop_id] = worker
        worker.start()

    def on_queue_title_resolved(self, workshop_id, title):
        self.queue_titles[workshop_id] = title or tr("unknown_mod")
        if workshop_id in self.queue:
            self.update_queue_ui()

    def on_browser_url_changed(self, url):
        self.url_edit.setText(url.toString())
        self.current_workshop_id = self.extract_mod_page_workshop_id(url)
        self.update_current_mod_state()

    def on_browser_title_changed(self, title):
        self.page_title_label.setText(f"{tr('label_current_page')}: {title or self.WORKSHOP_URL}")

    def go_back(self):
        self.browser_view.back()

    def go_forward(self):
        self.browser_view.forward()

    def reload_page(self):
        self.browser_view.reload()

    def add_current_mod(self):
        if not self.current_workshop_id:
            QMessageBox.warning(
                self,
                tr("warning_invalid_workshop_page_title"),
                tr("warning_invalid_workshop_page_message")
            )
            return

        if self.current_workshop_id in self.queue:
            QMessageBox.information(
                self,
                tr("info_duplicate_title"),
                tr("info_duplicate_message").format(workshop_id=self.current_workshop_id)
            )
            return

        self.queue.append(self.current_workshop_id)
        self.queue_titles.setdefault(self.current_workshop_id, tr("unknown_mod"))
        self.update_queue_ui()
        self.ensure_queue_title_async(self.current_workshop_id)

    def toggle_queue_item_from_js(self, workshop_id):
        if workshop_id in self.queue:
            self.queue = [queue_id for queue_id in self.queue if queue_id != workshop_id]
            self.update_queue_ui()
            return

        self.queue.append(workshop_id)
        self.queue_titles.setdefault(workshop_id, tr("unknown_mod"))
        self.update_queue_ui()
        self.ensure_queue_title_async(workshop_id)

    def remove_selected(self):
        for item in self.queue_list.selectedItems():
            wid = item.data(Qt.UserRole)
            if wid in self.queue:
                self.queue.remove(wid)
        self.update_queue_ui()

    def clear_list(self):
        self.queue = []
        self.update_queue_ui()

    def show_queue_context_menu(self, position):
        item = self.queue_list.itemAt(position)
        if not item:
            return

        selected_items = self.queue_list.selectedItems()
        selected_ids = [selected_item.data(Qt.UserRole) for selected_item in selected_items]
        wid = item.data(Qt.UserRole)
        remove_ids = [wid]
        remove_label = tr("button_remove_this_mod")
        if len(selected_ids) > 1 and wid in selected_ids:
            remove_ids = selected_ids
            remove_label = tr("button_remove_selected_mods")

        menu = QMenu(self)
        remove_action = menu.addAction(remove_label)
        chosen_action = menu.exec(self.queue_list.mapToGlobal(position))
        if chosen_action == remove_action:
            self.queue = [queue_id for queue_id in self.queue if queue_id not in remove_ids]
            self.update_queue_ui()

    def download_queue(self):
        if not self.queue:
            QMessageBox.information(self, tr("info_no_mods_title"), tr("info_browser_queue_empty"))
            return
        started = self.parent_window.start_download_for_ids(self.queue.copy())
        if not started:
            return
        self.queue = []
        self.update_queue_ui()


class CheckUpdatesDialog(QDialog):
    """Dialog for checking and selecting mods to update."""
    
    def __init__(self, mods, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dialog_check_for_updates"))
        self.setModal(True)
        self.resize(600, 400)
        
        layout = QVBoxLayout()
        
        # Progress bar for checking updates
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        # Check updates button
        self.check_button = QPushButton(tr("menu_check_updates"))
        self.check_button.clicked.connect(self.check_updates)
        layout.addWidget(self.check_button)
        
        # Mod list with checkboxes
        self.mod_list_widget = QWidget()
        self.mod_list_layout = QVBoxLayout()
        self.mod_checkboxes = []
        
        scroll_area = QScrollArea()
        scroll_area.setWidget(self.mod_list_widget)
        scroll_area.setWidgetResizable(True)
        layout.addWidget(scroll_area)
        
        # Select all/none buttons
        button_layout = QHBoxLayout()
        select_all_btn = QPushButton(tr("button_select_all"))
        select_all_btn.clicked.connect(self.select_all)
        select_none_btn = QPushButton(tr("button_select_none"))
        select_none_btn.clicked.connect(self.select_none)
        button_layout.addWidget(select_all_btn)
        button_layout.addWidget(select_none_btn)
        layout.addLayout(button_layout)
        
        # Update selected button
        self.update_button = QPushButton(tr("button_update_selected_mods"))
        self.update_button.clicked.connect(self.update_selected)
        self.update_button.setEnabled(False)
        layout.addWidget(self.update_button)
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Close, Qt.Horizontal, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
        self.setLayout(layout)
        self.mods = mods
        self.update_results = []
        
        # Initialize with mod list
        self.populate_mod_list()
    
    def populate_mod_list(self):
        """Populate the mod list with checkboxes."""
        # Clear existing
        for checkbox in self.mod_checkboxes:
            checkbox.setParent(None)
        self.mod_checkboxes.clear()
        
        for mod in self.mods:
            checkbox = QCheckBox(f"{mod['title'] or 'Unknown'} (ID: {mod['workshop_id']})")
            checkbox.mod_data = mod
            self.mod_list_layout.addWidget(checkbox)
            self.mod_checkboxes.append(checkbox)
        
        self.mod_list_widget.setLayout(self.mod_list_layout)
    
    def check_updates(self):
        """Check for updates."""
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # Indeterminate progress
        
        # Run update check in thread
        self.check_thread = UpdateCheckThread(self.mods)
        self.check_thread.finished.connect(self.on_check_finished)
        self.check_thread.start()
    
    def on_check_finished(self, results):
        """Handle update check completion."""
        self.progress_bar.setVisible(False)
        self.update_results = results
        
        # Update checkboxes with update status
        for checkbox in self.mod_checkboxes:
            mod_id = checkbox.mod_data['workshop_id']
            result = next((r for r in results if r['workshop_id'] == mod_id), None)
            if result:
                status = result['status']
                if status == 'update_available':
                    checkbox.setText(f"✓ UPDATE AVAILABLE: {checkbox.mod_data['title'] or 'Unknown'} (ID: {mod_id})")
                    checkbox.setChecked(True)
                elif status == 'up_to_date':
                    checkbox.setText(f"✓ Up to date: {checkbox.mod_data['title'] or 'Unknown'} (ID: {mod_id})")
                else:
                    checkbox.setText(f"✗ Check failed: {checkbox.mod_data['title'] or 'Unknown'} (ID: {mod_id})")
        
        self.update_button.setEnabled(True)
    
    def select_all(self):
        """Select all mods."""
        for checkbox in self.mod_checkboxes:
            checkbox.setChecked(True)
    
    def select_none(self):
        """Deselect all mods."""
        for checkbox in self.mod_checkboxes:
            checkbox.setChecked(False)
    
    def update_selected(self):
        """Update selected mods."""
        selected_mods = [cb.mod_data for cb in self.mod_checkboxes if cb.isChecked()]
        if not selected_mods:
            QMessageBox.information(self, tr("info_no_selection_title"), tr("info_no_selection_update_mods"))
            return

        parent_window = self.parent()
        if not hasattr(parent_window, "require_valid_library_root"):
            QMessageBox.warning(self, tr("dialog_library_root_required"), tr("warning_library_root_validation_unavailable"))
            return

        download_root = parent_window.require_valid_library_root()
        if not download_root:
            return
        
        # Show progress
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(selected_mods))
        self.progress_bar.setValue(0)
        
        # Update mods
        updated = 0
        failed = 0
        for i, mod in enumerate(selected_mods):
            try:
                result = update_mod(mod['workshop_id'], download_root, get_db_path())
                if result.get('status') == 'success':
                    updated += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
            self.progress_bar.setValue(i + 1)
        
        self.progress_bar.setVisible(False)
        
        # Show results
        QMessageBox.information(
            self, "Update Complete", 
            f"Updated: {updated} mods\nFailed: {failed} mods"
        )
        
        # Refresh parent if available
        if hasattr(self.parent(), 'refresh_mod_list'):
            self.parent().refresh_mod_list()

class SettingsDialog(QDialog):
    """Settings dialog."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dialog_settings"))
        self.setModal(True)
        self.resize(500, 240)
        self.root_changed = False
        self.root_change_warning_acknowledged = False
        self._suppress_root_change_prompt = False
        self.language_change_notified = False
        self.original_settings = {
            "library_root": "",
            "language": "en",
            "refresh_mod_db_on_startup": False,
        }
        
        layout = QVBoxLayout()
        
        form_layout = QFormLayout()
        
        self.library_root_edit = QLineEdit()
        self.library_root_edit.textEdited.connect(self.on_library_root_text_edited)
        browse_button = QPushButton(tr("button_browse"))
        browse_button.clicked.connect(self.browse_library_root)
        
        root_layout = QHBoxLayout()
        root_layout.addWidget(self.library_root_edit)
        root_layout.addWidget(browse_button)
        
        form_layout.addRow(tr("label_library_root"), root_layout)

        self.language_combo = QComboBox()
        self.language_combo.addItem(tr("language_english"), "en")
        self.language_combo.addItem(tr("language_simplified_chinese"), "zh")
        self.language_combo.currentIndexChanged.connect(self.on_language_changed)
        form_layout.addRow(tr("label_language"), self.language_combo)

        startup_section_spacer = QWidget()
        startup_section_spacer.setFixedHeight(12)
        form_layout.addRow("", startup_section_spacer)

        self.refresh_mod_db_checkbox = QCheckBox(tr("label_refresh_mod_db_on_startup"))
        form_layout.addRow(tr("label_startup"), self.refresh_mod_db_checkbox)
        self.refresh_mod_db_warning_label = QLabel(tr("label_refresh_mod_db_on_startup_warning"))
        self.refresh_mod_db_warning_label.setWordWrap(True)
        form_layout.addRow("", self.refresh_mod_db_warning_label)
        
        layout.addLayout(form_layout)
        
        settings = SettingsManager(get_settings_path())
        current_root = settings.get_library_root()
        if current_root:
            self.original_settings["library_root"] = str(Path(current_root).expanduser().resolve())
            self.library_root_edit.setText(self.original_settings["library_root"])
        current_language = settings.get_language()
        self.original_settings["language"] = current_language
        self.language_combo.setCurrentIndex(max(self.language_combo.findData(current_language), 0))
        refresh_mod_db_on_startup = settings.get_refresh_mod_db_on_startup()
        self.original_settings["refresh_mod_db_on_startup"] = refresh_mod_db_on_startup
        self.refresh_mod_db_checkbox.setChecked(refresh_mod_db_on_startup)
        
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel,
            Qt.Horizontal, self
        )
        buttons.button(QDialogButtonBox.Save).setText(tr("button_save"))
        buttons.button(QDialogButtonBox.Cancel).setText(tr("button_cancel"))
        buttons.accepted.connect(self.save_settings)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
        self.setLayout(layout)
    
    def browse_library_root(self):
        """Browse for library root directory."""
        if not self.confirm_root_change_intent():
            return

        directory = QFileDialog.getExistingDirectory(self, tr("label_library_root"))
        if directory:
            self.library_root_edit.setText(directory)

    def normalize_root_text(self, root_text):
        root_text = (root_text or "").strip()
        if not root_text:
            return ""
        return str(Path(root_text).expanduser().resolve())

    def get_current_settings_state(self):
        return {
            "library_root": self.normalize_root_text(self.library_root_edit.text()),
            "language": self.language_combo.currentData(),
            "refresh_mod_db_on_startup": self.refresh_mod_db_checkbox.isChecked(),
        }

    def has_library_root_changed(self):
        return self.get_current_settings_state()["library_root"] != self.original_settings["library_root"]

    def has_settings_changed(self):
        return self.get_current_settings_state() != self.original_settings

    def confirm_root_change_intent(self):
        if not self.original_settings["library_root"]:
            return True
        if self.root_change_warning_acknowledged:
            return True

        response = QMessageBox.warning(
            self,
            tr("dialog_change_library_root"),
            tr("warning_change_library_root_message"),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel
        )
        if response != QMessageBox.Yes:
            return False

        self.root_change_warning_acknowledged = True
        return True

    def on_library_root_text_edited(self, _text):
        if self._suppress_root_change_prompt:
            return
        if not self.has_library_root_changed():
            return
        if self.confirm_root_change_intent():
            return

        self._suppress_root_change_prompt = True
        self.library_root_edit.setText(self.original_settings["library_root"])
        self._suppress_root_change_prompt = False

    def on_language_changed(self, _index):
        current_language = self.language_combo.currentData()
        if current_language == self.original_settings["language"]:
            return
        if self.language_change_notified:
            return
        QMessageBox.information(
            self,
            tr("dialog_language_changed"),
            tr("info_language_restart_message")
        )
        self.language_change_notified = True
    
    def save_settings(self):
        """Save settings."""
        try:
            current_settings = self.get_current_settings_state()
            new_root = current_settings["library_root"]
            new_language = current_settings["language"]
            new_refresh_mod_db_on_startup = current_settings["refresh_mod_db_on_startup"]
            changed = self.has_settings_changed()
            library_root_changed = new_root != self.original_settings["library_root"]
            language_changed = new_language != self.original_settings["language"]
            refresh_mod_db_on_startup_changed = (
                new_refresh_mod_db_on_startup != self.original_settings["refresh_mod_db_on_startup"]
            )

            if not new_root and self.original_settings["library_root"]:
                QMessageBox.warning(self, tr("warning_invalid_setting_title"), tr("warning_library_root_empty"))
                return

            if changed:
                response = QMessageBox.question(
                    self,
                    tr("dialog_save_changed_settings"),
                    tr("question_save_changed_settings_message"),
                    QMessageBox.Yes | QMessageBox.Cancel,
                    QMessageBox.Yes
                )
                if response != QMessageBox.Yes:
                    return

            parent_window = self.parent()
            db_path = parent_window.db_path if hasattr(parent_window, "db_path") else get_db_path()
            if library_root_changed and new_root:
                progress_dialog = OperationProgressDialog(tr("dialog_loading_library"), self)
                progress_dialog.set_overall(0, 0)
                progress_dialog.set_current(tr("status_scanning_library_root"))
                progress_dialog.show()

                worker = SwitchLibraryRootThread(get_settings_path(), db_path, new_root)
                worker.progress.connect(
                    lambda done, total, current: [
                        progress_dialog.set_overall(done, total),
                        progress_dialog.set_current(current),
                    ]
                )
                worker.log.connect(progress_dialog.append_log)

                def on_finished(result):
                    progress_dialog.append_log(
                        tr("log_loading_library_complete").format(count=result.get("imported_count", 0))
                    )
                    progress_dialog.mark_done()
                    self.apply_non_root_settings_changes(
                        new_language,
                        language_changed,
                        new_refresh_mod_db_on_startup,
                        refresh_mod_db_on_startup_changed,
                    )
                    self.root_changed = True
                    self.original_settings = current_settings
                    QMessageBox.information(
                        self,
                        tr("info_settings_saved_title"),
                        tr("info_settings_saved_message").format(count=result.get("imported_count", 0))
                    )
                    progress_dialog.close()
                    self.accept()

                def on_error(error_message):
                    progress_dialog.close()
                    QMessageBox.critical(
                        self,
                        tr("error_title"),
                        tr("error_save_settings_message").format(error=error_message),
                    )

                worker.finished.connect(on_finished)
                worker.error.connect(on_error)
                self._switch_root_worker = worker
                worker.start()
                return

            self.apply_non_root_settings_changes(
                new_language,
                language_changed,
                new_refresh_mod_db_on_startup,
                refresh_mod_db_on_startup_changed,
            )
            self.root_changed = library_root_changed
            self.original_settings = current_settings
            QMessageBox.information(
                self,
                tr("info_settings_saved_title"),
                tr("info_settings_saved_simple")
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, tr("error_title"), tr("error_save_settings_message").format(error=e))

    def apply_non_root_settings_changes(self, new_language, language_changed, new_refresh_mod_db_on_startup, refresh_mod_db_on_startup_changed):
        settings_manager = SettingsManager(get_settings_path())
        if language_changed:
            settings_manager.set_language(new_language)
        if refresh_mod_db_on_startup_changed:
            settings_manager.set_refresh_mod_db_on_startup(new_refresh_mod_db_on_startup)

class UpdateCheckThread(QThread):
    """Thread for checking mod updates."""
    
    progress = Signal(int, int, str)
    finished = Signal(list)
    error = Signal(str)
    
    def __init__(self, mods):
        super().__init__()
        self.mods = mods
    
    def run(self):
        """Run update check."""
        from core.updater import check_mod_for_updates
        try:
            results = []
            total = len(self.mods)
            for index, mod in enumerate(self.mods, start=1):
                workshop_id = mod['workshop_id']
                status = mod.get('status')
                self.progress.emit(index, total, f"Checking {workshop_id}")
                check_result = check_mod_for_updates(workshop_id, mod.get('remote_updated_at'))
                results.append(check_result)
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class DownloadModThread(QThread):
    """Thread for downloading a single mod."""
    
    started_signal = Signal(str)
    progress = Signal(str)
    finished = Signal(dict)
    error = Signal(str)
    
    def __init__(self, workshop_id, download_root, db_path):
        super().__init__()
        self.workshop_id = workshop_id
        self.download_root = download_root
        self.db_path = db_path
    
    def run(self):
        try:
            self.started_signal.emit(self.workshop_id)
            self.progress.emit(f"Starting download for {self.workshop_id}")
            result = download_mod(self.workshop_id, self.download_root, self.db_path)
            self.progress.emit(f"Download completed for {self.workshop_id} ({result.get('status')})")
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class UpdateModsThread(QThread):
    """Thread for updating a set of mods."""
    
    progress = Signal(int, int, str)
    log = Signal(str)
    finished = Signal(dict)
    error = Signal(str)
    
    def __init__(self, workshop_ids, download_root, db_path):
        super().__init__()
        self.workshop_ids = workshop_ids
        self.download_root = download_root
        self.db_path = db_path
    
    def run(self):
        try:
            total = len(self.workshop_ids)
            updated = 0
            failed = 0
            details = []
            for index, workshop_id in enumerate(self.workshop_ids, start=1):
                self.progress.emit(index, total, f"Updating {workshop_id}")
                self.log.emit(f"Updating {workshop_id}...")
                try:
                    result = update_mod(workshop_id, self.download_root, self.db_path)
                    details.append({"workshop_id": workshop_id, "result": result})
                    if result.get('status') == 'success':
                        updated += 1
                        self.log.emit(f"{workshop_id} updated successfully")
                    else:
                        failed += 1
                        err = result.get('error', 'unknown')
                        self.log.emit(f"{workshop_id} update failed: {err}")
                except Exception as e:
                    failed += 1
                    self.log.emit(f"{workshop_id} update raised exception: {e}")
                self.progress.emit(index, total, f"Completed {workshop_id}")
            self.finished.emit({"updated": updated, "failed": failed, "details": details})
        except Exception as e:
            self.error.emit(str(e))


class StartupLibraryRefreshThread(QThread):
    progress = Signal(int, int, str)
    log = Signal(str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, db_path, library_root):
        super().__init__()
        self.db_path = db_path
        self.library_root = library_root

    def run(self):
        try:
            def on_progress(current, total, token):
                if token == "scan_started":
                    current_text = tr("status_scanning_library_root")
                else:
                    current_text = tr("status_loading_library_mod").format(workshop_id=token)
                self.progress.emit(current, total, current_text)

            def on_log(workshop_id):
                self.log.emit(tr("status_loading_library_mod").format(workshop_id=workshop_id))

            result = rebuild_database_from_library_root(
                self.db_path,
                self.library_root,
                progress_callback=on_progress,
                log_callback=on_log,
            )
            self.log.emit(tr("log_loading_library_rebuilding_database"))
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class SwitchLibraryRootThread(QThread):
    progress = Signal(int, int, str)
    log = Signal(str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, settings_path, db_path, new_library_root):
        super().__init__()
        self.settings_path = settings_path
        self.db_path = db_path
        self.new_library_root = new_library_root

    def run(self):
        try:
            def on_progress(current, total, token):
                if token == "scan_started":
                    current_text = tr("status_scanning_library_root")
                else:
                    current_text = tr("status_loading_library_mod").format(workshop_id=token)
                self.progress.emit(current, total, current_text)

            def on_log(workshop_id):
                self.log.emit(tr("status_loading_library_mod").format(workshop_id=workshop_id))

            result = switch_library_root(
                self.settings_path,
                self.db_path,
                self.new_library_root,
                progress_callback=on_progress,
                log_callback=on_log,
            )
            self.log.emit(tr("log_loading_library_rebuilding_database"))
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class OperationProgressDialog(QDialog):
    """Reusable progress dialog for background operations."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumSize(500, 320)

        self.layout = QVBoxLayout(self)

        self.overall_label = QLabel(tr("label_overall_progress"))
        self.layout.addWidget(self.overall_label)

        self.overall_bar = QProgressBar()
        self.overall_bar.setRange(0, 100)
        self.layout.addWidget(self.overall_bar)

        self.current_label = QLabel(tr("label_current_item"))
        self.layout.addWidget(self.current_label)

        self.current_bar = QProgressBar()
        self.current_bar.setRange(0, 0)
        self.layout.addWidget(self.current_bar)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setPlainText("")
        self.layout.addWidget(self.log_text)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Close)
        self.button_box.button(QDialogButtonBox.Close).setEnabled(False)
        self.button_box.rejected.connect(self.close)
        self.layout.addWidget(self.button_box)

        self.completed = False

    def set_overall(self, current, total):
        if total > 0:
            self.overall_bar.setRange(0, total)
            self.overall_bar.setValue(current)
            self.overall_label.setText(tr("label_overall_value").format(current=current, total=total))
        else:
            self.overall_bar.setRange(0, 0)
            self.overall_label.setText(tr("label_overall_processing"))

    def set_current(self, text):
        self.current_label.setText(tr("label_current_value").format(text=text))

    def append_log(self, message):
        self.log_text.append(message)

    def mark_done(self):
        self.current_bar.setRange(0, 1)
        self.current_bar.setValue(1)
        self.append_log(tr("log_operation_completed"))
        self.button_box.button(QDialogButtonBox.Close).setEnabled(True)
        self.completed = True


class OutdatedModsDialog(QDialog):
    """Dialog for selecting and updating outdated mods."""

    def __init__(self, outdated_mods, parent=None):
        super().__init__(parent)
        self.outdated_mods = outdated_mods
        self.parent_window = parent
        self.setWindowTitle(tr("dialog_outdated_mods"))
        self.setMinimumSize(600, 400)
        self.resize(600, 400)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Mod list (like main GUI)
        self.mod_list = QListWidget()
        self.mod_list.setSelectionMode(QListWidget.MultiSelection)
        self.mod_list.itemSelectionChanged.connect(self.update_selection_count)
        layout.addWidget(self.mod_list)

        # Selection counter
        self.selection_label = QLabel("0/0 mods selected")
        self.selection_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self.selection_label)

        # Control buttons - first row
        control_layout = QHBoxLayout()
        control_layout.setSpacing(10)

        select_all_btn = QPushButton(tr("button_select_all"))
        select_all_btn.clicked.connect(self.select_all)
        control_layout.addWidget(select_all_btn)

        clear_btn = QPushButton(tr("button_clear"))
        clear_btn.clicked.connect(self.clear_selection)
        control_layout.addWidget(clear_btn)

        self.update_selected_button = QPushButton(tr("button_update_selected"))
        self.update_selected_button.clicked.connect(self.update_selected)
        control_layout.addWidget(self.update_selected_button)

        layout.addLayout(control_layout)

        # Update All button - second row
        update_all_layout = QHBoxLayout()
        self.update_all_button = QPushButton(tr("button_update_all"))
        self.update_all_button.clicked.connect(self.update_all)
        self.update_all_button.setMinimumHeight(35)  # Slightly taller
        update_all_layout.addWidget(self.update_all_button)
        layout.addLayout(update_all_layout)

        # Close button at bottom
        close_box = QDialogButtonBox(QDialogButtonBox.Close)
        close_box.rejected.connect(self.reject)
        layout.addWidget(close_box)

        self.populate_mod_list()

    def populate_mod_list(self):
        """Populate the mod list with items."""
        self.mod_list.clear()
        for mod in self.outdated_mods:
            title = mod.get('latest_title') or str(mod.get('workshop_id'))
            item_text = f"{title} (ID: {mod.get('workshop_id')})"
            item = QListWidgetItem(item_text)
            item.mod_data = mod
            item.setSelected(True)  # Start with all selected
            self.mod_list.addItem(item)
        self.update_selection_count()

    def update_selection_count(self):
        """Update the selection counter label."""
        selected_count = len(self.mod_list.selectedItems())
        total_count = self.mod_list.count()
        self.selection_label.setText(tr("label_selection_count").format(selected=selected_count, total=total_count))

    def select_all(self):
        """Select all mods."""
        for i in range(self.mod_list.count()):
            self.mod_list.item(i).setSelected(True)

    def clear_selection(self):
        """Clear all selections."""
        for i in range(self.mod_list.count()):
            self.mod_list.item(i).setSelected(False)

    def update_selected(self):
        selected_items = self.mod_list.selectedItems()
        if not selected_items:
            QMessageBox.information(self, tr("info_no_selection_title"), tr("info_no_outdated_mods_selected"))
            return

        download_root = self.parent_window.require_valid_library_root()
        if not download_root:
            return

        workshop_ids = [item.mod_data['workshop_id'] for item in selected_items]
        progress_dialog = OperationProgressDialog(tr("dialog_updating_selected_mods"), self)
        progress_dialog.show()

        worker = UpdateModsThread(workshop_ids, download_root, self.parent_window.db_path)
        worker.progress.connect(lambda done, total, current: [progress_dialog.set_overall(done, total), progress_dialog.set_current(current)])
        worker.log.connect(progress_dialog.append_log)
        worker.error.connect(lambda err: QMessageBox.critical(self, tr("error_title"), err))
        worker.finished.connect(lambda result: [progress_dialog.append_log(f"Updated: {result['updated']} failed: {result['failed']}"), progress_dialog.mark_done(), progress_dialog.close(), self.parent_window.refresh_mod_list()])
        self.parent_window.worker_threads.append(worker)  # Keep reference
        worker.start()

    def update_all(self):
        """Update all outdated mods shown in this dialog."""
        if not self.outdated_mods:
            QMessageBox.information(self, tr("info_no_mods_title"), tr("info_no_mods_found"))
            return

        download_root = self.parent_window.require_valid_library_root()
        if not download_root:
            return

        workshop_ids = [mod['workshop_id'] for mod in self.outdated_mods]
        progress_dialog = OperationProgressDialog(tr("dialog_updating_all_outdated_mods"), self)
        progress_dialog.show()

        worker = UpdateModsThread(workshop_ids, download_root, self.parent_window.db_path)
        worker.progress.connect(lambda done, total, current: [progress_dialog.set_overall(done, total), progress_dialog.set_current(current)])
        worker.log.connect(progress_dialog.append_log)
        worker.error.connect(lambda err: QMessageBox.critical(self, tr("error_title"), err))
        worker.finished.connect(lambda result: [progress_dialog.append_log(f"Updated: {result['updated']} failed: {result['failed']}"), progress_dialog.mark_done(), progress_dialog.close(), self.parent_window.refresh_mod_list()])
        self.parent_window.worker_threads.append(worker)  # Keep reference
        worker.start()


class ModDetailPanel(QWidget):
    """Panel showing details of selected mod."""
    
    def __init__(self):
        super().__init__()
        self.init_ui()
    
    def init_ui(self):
        """Initialize the UI."""
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)
        
        # Preview image - larger
        self.preview_label = QLabel()
        self.preview_label.setFixedSize(360, 270)
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setStyleSheet("border: 1px solid #ccc; background-color: #f0f0f0;")
        self.preview_label.setText(tr("no_preview_available"))
        layout.addWidget(self.preview_label)
        
        # Add spacing
        layout.addSpacing(8)
        
        # Mod info
        info_layout = QVBoxLayout()
        info_layout.setSpacing(3)
        
        self.title_label = QLabel(tr("select_mod_to_view_details"))
        self.title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        info_layout.addWidget(self.title_label)
        
        self.author_label = QLabel("")
        info_layout.addWidget(self.author_label)
        
        self.file_size_label = QLabel("")
        info_layout.addWidget(self.file_size_label)
        
        self.last_update_label = QLabel("")
        info_layout.addWidget(self.last_update_label)
        
        self.last_download_label = QLabel("")
        info_layout.addWidget(self.last_download_label)
        
        # Workshop URL - clickable link style
        workshop_url_container = QVBoxLayout()
        workshop_url_container.setSpacing(2)
        workshop_url_label_title = QLabel(tr("label_workshop_url"))
        workshop_url_label_title.setStyleSheet("font-weight: bold;")
        workshop_url_container.addWidget(workshop_url_label_title)
        
        self.workshop_url_label = QLabel("")
        self.workshop_url_label.setOpenExternalLinks(True)
        self.workshop_url_label.setStyleSheet("color: blue; text-decoration: underline;")
        self.workshop_url_label.setWordWrap(True)
        self.workshop_url_label.linkActivated.connect(self.open_workshop_url_from_signal)
        workshop_url_container.addWidget(self.workshop_url_label)
        info_layout.addLayout(workshop_url_container)
        
        # File path - clickable link style
        file_path_container = QVBoxLayout()
        file_path_container.setSpacing(2)
        file_path_label_title = QLabel(tr("label_file_path"))
        file_path_label_title.setStyleSheet("font-weight: bold;")
        file_path_container.addWidget(file_path_label_title)
        
        self.file_path_label = QLabel("")
        self.file_path_label.setStyleSheet("color: blue; text-decoration: underline;")
        self.file_path_label.setWordWrap(True)
        self.file_path_label.setOpenExternalLinks(True)
        self.file_path_label.linkActivated.connect(self.open_mod_folder_from_signal)
        file_path_container.addWidget(self.file_path_label)
        info_layout.addLayout(file_path_container)
        
        layout.addLayout(info_layout)
        
        # Add spacing before description
        layout.addSpacing(5)
        
        # Description area - with label directly above
        desc_label = QLabel(tr("label_description"))
        desc_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(desc_label)
        
        self.description_text = QTextEdit()
        self.description_text.setReadOnly(True)
        self.description_text.setMinimumHeight(80)
        layout.addWidget(self.description_text)
        
        self.setLayout(layout)
    
    def update_mod_details(self, mod_data):
        """Update the panel with mod details."""
        if not mod_data:
            self.clear_details()
            return
        
        # Store current mod data for link handling
        self.current_mod_data = mod_data
        
        # Title
        self.title_label.setText(mod_data.get('title') or tr('unknown_mod'))
        
        # Author
        creator = mod_data.get('creator')
        if creator:
            self.author_label.setText(tr("label_author").format(creator=creator))
        else:
            self.author_label.setText(tr("label_author_unknown"))
        
        # File size
        file_size = mod_data.get('file_size')
        if file_size:
            size_mb = file_size / (1024 * 1024)
            self.file_size_label.setText(tr("label_size").format(size=size_mb))
        else:
            # Calculate from content_path if available
            content_path = mod_data.get('content_path')
            if content_path and os.path.exists(content_path):
                try:
                    total_size = 0
                    for dirpath, dirnames, filenames in os.walk(content_path):
                        for filename in filenames:
                            filepath = os.path.join(dirpath, filename)
                            total_size += os.path.getsize(filepath)
                    size_mb = total_size / (1024 * 1024)
                    self.file_size_label.setText(tr("label_size").format(size=size_mb))
                except:
                    self.file_size_label.setText(tr("label_size_unknown"))
            else:
                self.file_size_label.setText(tr("label_size_unknown"))
        
        # Last workshop update
        remote_updated = mod_data.get('remote_updated_at')
        if remote_updated:
            from datetime import datetime
            dt = datetime.fromtimestamp(remote_updated)
            self.last_update_label.setText(tr("label_last_workshop_update").format(timestamp=dt.strftime('%Y-%m-%d %H:%M')))
        else:
            self.last_update_label.setText(tr("label_last_workshop_update_unknown"))
        
        # Last download time
        last_downloaded = mod_data.get('last_downloaded_at')
        if last_downloaded:
            from datetime import datetime
            dt = datetime.fromtimestamp(last_downloaded)
            self.last_download_label.setText(tr("label_last_downloaded").format(timestamp=dt.strftime('%Y-%m-%d %H:%M')))
        else:
            self.last_download_label.setText(tr("label_last_downloaded_never"))
        
        # Workshop URL - as clickable link
        workshop_id = mod_data.get('workshop_id')
        if workshop_id:
            url = f"https://steamcommunity.com/sharedfiles/filedetails/?id={workshop_id}"
            self.workshop_url_label.setText(f'<a href="{url}">{url}</a>')
        else:
            self.workshop_url_label.setText(tr("no_workshop_url_available"))
        
        # File path - as clickable link
        content_path = mod_data.get('content_path')
        if content_path:
            file_url = QUrl.fromLocalFile(content_path).toString()
            self.file_path_label.setText(f'<a href="{file_url}">{content_path}</a>')
        else:
            self.file_path_label.setText(tr("no_local_files"))
        
        # Description
        description = mod_data.get('description')
        if description:
            self.description_text.setPlainText(description)
        else:
            self.description_text.setPlainText(tr('no_description_available'))
        
        # Preview image - download and display
        preview_url = mod_data.get('preview_url')
        if preview_url:
            self.load_preview_image(preview_url)
        else:
            self.preview_label.setText(tr("no_preview_available"))
    
    def load_preview_image(self, preview_url):
        """Download and display preview image from URL."""
        try:
            import urllib.request
            # Download image with timeout
            with urllib.request.urlopen(preview_url, timeout=5) as response:
                image_data = response.read()
            
            # Create pixmap from data
            pixmap = QPixmap()
            if pixmap.loadFromData(QByteArray(image_data)):
                # Scale to fit the label while maintaining aspect ratio
                scaled_pixmap = pixmap.scaledToWidth(360, Qt.SmoothTransformation)
                self.preview_label.setPixmap(scaled_pixmap)
            else:
                self.preview_label.setText(tr("preview_unavailable"))
        except Exception as e:
            # Silently fail and show no preview
            self.preview_label.setText(tr("preview_unavailable"))
    
    def clear_details(self):
        """Clear all details."""
        self.current_mod_data = None
        self.title_label.setText(tr("select_mod_to_view_details"))
        self.author_label.setText("")
        self.file_size_label.setText("")
        self.last_update_label.setText("")
        self.last_download_label.setText("")
        self.workshop_url_label.setText("")
        self.file_path_label.setText("")
        self.description_text.setPlainText("")
        self.preview_label.setText(tr("no_preview_available"))
    
    def open_workshop_url_from_signal(self, link):
        """Open workshop URL from link signal."""
        if link.startswith('http'):
            QDesktopServices.openUrl(QUrl(link))
    
    def open_mod_folder_from_signal(self, link):
        """Open mod folder from link signal."""
        # Extract the file path from the link
        if self.current_mod_data:
            content_path = self.current_mod_data.get('content_path')
            if content_path and os.path.exists(content_path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(content_path))

class MainWindow(QMainWindow):
    """Main application window."""
    
    def __init__(self):
        super().__init__()
        self.db_path = get_db_path()
        self.settings_path = get_settings_path()
        # Keep references to worker threads to prevent garbage collection
        self.worker_threads = []
        self.init_ui()
        self.refresh_mod_list()
        QTimer.singleShot(0, self.refresh_mod_db_on_startup_if_enabled)
    
    def init_ui(self):
        """Initialize the user interface."""
        self.setWindowTitle(tr("app_title"))
        self.setGeometry(100, 100, 1400, 800)
        
        # Create central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Top bar with menus
        self.create_menu_bar()
        
        # Two-pane layout
        splitter = QSplitter(Qt.Horizontal)
        splitter.setContentsMargins(0, 0, 0, 0)
        
        # Left pane: Mod detail panel (narrower)
        self.detail_panel = ModDetailPanel()
        splitter.addWidget(self.detail_panel)
        
        # Right pane: Mod list with search/sort controls  
        list_container = QWidget()
        list_layout = QVBoxLayout(list_container)
        list_layout.setContentsMargins(5, 5, 5, 5)
        list_layout.setSpacing(5)
        
        # Search and sort controls - NOW IN RIGHT PANE ONLY
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(5)
        
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("placeholder_search_mods"))
        self.search_edit.textChanged.connect(self.filter_mods)
        controls_layout.addWidget(QLabel(tr("label_search")))
        controls_layout.addWidget(self.search_edit)

        controls_layout.addWidget(QLabel(tr("label_sort")))
        self.sort_combo = QComboBox()
        self.sort_combo.addItem(tr("sort_alphabetical"), "alphabetical")
        self.sort_combo.addItem(tr("sort_last_workshop_update"), "last_workshop_update")
        self.sort_combo.addItem(tr("sort_last_download_time"), "last_download_time")
        self.sort_combo.addItem(tr("sort_file_size"), "file_size")
        self.sort_combo.currentTextChanged.connect(self.sort_mods)
        controls_layout.addWidget(self.sort_combo)
        
        list_layout.addLayout(controls_layout)
        
        # Mod list
        self.mod_list = QListWidget()
        self.mod_list.itemClicked.connect(self.on_mod_selected)
        list_layout.addWidget(self.mod_list)
        
        splitter.addWidget(list_container)
        
        # Set initial splitter proportions (detail panel wider now)
        splitter.setSizes([500, 900])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        
        main_layout.addWidget(splitter)
        
        central_widget.setLayout(main_layout)
    
    def create_menu_bar(self):
        """Create the menu bar."""
        menubar = self.menuBar()
        
        # Workshop menu
        workshop_menu = menubar.addMenu(tr('menu_workshop'))
        
        download_action = QAction(tr('menu_download_from_url_id'), self)
        download_action.triggered.connect(self.show_download_from_url_or_id)
        workshop_menu.addAction(download_action)

        browse_workshop_action = QAction(tr('menu_browse_workshop'), self)
        browse_workshop_action.triggered.connect(self.show_workshop_browser)
        workshop_menu.addAction(browse_workshop_action)
        
        check_updates_action = QAction(tr('menu_check_updates'), self)
        check_updates_action.triggered.connect(self.show_check_updates)
        workshop_menu.addAction(check_updates_action)

        # Settings menu
        settings_menu = menubar.addMenu(tr('menu_settings'))
        
        settings_action = QAction(tr('menu_settings'), self)
        settings_action.triggered.connect(self.show_settings)
        settings_menu.addAction(settings_action)

        check_app_updates_action = QAction(tr('menu_check_app_updates'), self)
        check_app_updates_action.triggered.connect(self.show_app_update_check)
        settings_menu.addAction(check_app_updates_action)
    
    def show_download_from_url_or_id(self):
        """Show the unified download dialog for URL/ID."""
        dialog = DownloadFromUrlIdDialog(self)
        dialog.exec()
        self.refresh_mod_list()

    def show_workshop_browser(self):
        dialog = WorkshopBrowserDialog(self)
        dialog.exec()
        self.refresh_mod_list()

    def require_valid_library_root(self):
        settings = SettingsManager(self.settings_path)
        library_root = settings.get_library_root()
        is_valid, detail = validate_library_root(library_root)
        if is_valid:
            return library_root

        message_box = QMessageBox(self)
        message_box.setIcon(QMessageBox.Warning)
        message_box.setWindowTitle(tr("dialog_library_root_required"))
        message_box.setText(tr("warning_library_root_required_message"))
        if detail:
            message_box.setInformativeText(detail)
        open_settings_button = message_box.addButton(tr("button_open_settings"), QMessageBox.AcceptRole)
        message_box.addButton(tr("button_cancel"), QMessageBox.RejectRole)
        message_box.exec()

        if message_box.clickedButton() == open_settings_button:
            if self.show_settings():
                settings = SettingsManager(self.settings_path)
                library_root = settings.get_library_root()
                is_valid, detail = validate_library_root(library_root)
                if is_valid:
                    return library_root

        return None

    def on_download_finished(self, result, progress_dialog):
        progress_dialog.append_log(f"Download finished: {result.get('status')}")
        progress_dialog.set_overall(1, 1)
        progress_dialog.mark_done()

        if result.get('status') == 'success':
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle(tr("info_download_complete_title"))
            msg_box.setText(tr("info_download_complete_message").format(title=result.get('title') or tr('unknown_mod')))
            msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg_box.finished.connect(lambda: progress_dialog.close())
            msg_box.exec()
            self.refresh_mod_list()
        else:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle(tr("error_download_failed_title"))
            msg_box.setText(tr("error_download_failed_message").format(error=result.get('error', 'Unknown error')))
            msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg_box.finished.connect(lambda: progress_dialog.close())
            msg_box.exec()

    def start_download_for_ids(self, workshop_ids):
        download_root = self.require_valid_library_root()
        if not download_root:
            return False

        if not workshop_ids:
            QMessageBox.warning(self, tr("info_no_mods_title"), tr("warning_no_mod_selected_message"))
            return False

        self.download_queue = workshop_ids.copy()
        self.download_results = []

        progress_dialog = OperationProgressDialog(tr("dialog_downloading_mods"), self)
        progress_dialog.set_overall(0, len(self.download_queue))
        progress_dialog.set_current("Starting downloads...")
        progress_dialog.show()

        def start_next():
            if not self.download_queue:
                # done
                success = sum(1 for r in self.download_results if r.get('status') == 'success')
                failed = len(self.download_results) - success
                progress_dialog.mark_done()
                self.refresh_mod_list()

                summary = f"Download complete: {success} success, {failed} failed."
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle(tr("question_download_summary_title"))
                msg_box.setText(tr("question_download_summary_message").format(success=success, failed=failed))
                msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
                msg_box.finished.connect(lambda: progress_dialog.close())
                msg_box.exec()

                return

            current = self.download_queue.pop(0)
            progress_dialog.set_current(f"Downloading {current}")
            progress_dialog.append_log(f"Start downloading {current}")

            worker = DownloadModThread(current, download_root, self.db_path)
            worker.started_signal.connect(lambda wid: progress_dialog.set_current(f"Downloading {wid}"))
            worker.progress.connect(progress_dialog.append_log)

            def on_finished(res, wid=current):
                self.download_results.append(res)
                completed = len(self.download_results)
                progress_dialog.set_overall(completed, len(workshop_ids))
                if res.get('status') == 'success':
                    progress_dialog.append_log(f"{wid} downloaded successfully")
                else:
                    progress_dialog.append_log(f"{wid} download failed: {res.get('error', 'Unknown')}")
                start_next()

            worker.finished.connect(on_finished)
            worker.error.connect(lambda err: progress_dialog.append_log(f"{current} error: {err}"))
            self.worker_threads.append(worker)
            worker.start()

        start_next()
        return True

    def show_check_updates(self):
        """Show check updates flow"""
        db = ModDatabase(self.db_path)
        mods = db.list_all_mods()

        if not mods:
            QMessageBox.information(self, tr("info_no_mods_title"), tr("info_no_mods_found"))
            return

        answer = QMessageBox.question(
            self,
            tr("question_check_updates_title"),
            tr("question_check_updates_message"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if answer != QMessageBox.Yes:
            return

        progress_dialog = OperationProgressDialog(tr("dialog_checking_updates"), self)
        progress_dialog.set_overall(0, len(mods))
        progress_dialog.set_current("Starting update check...")
        progress_dialog.show()

        worker = UpdateCheckThread(mods)
        worker.progress.connect(lambda done, total, current: [progress_dialog.set_overall(done, total), progress_dialog.set_current(current)])
        worker.error.connect(lambda err: QMessageBox.critical(self, tr("error_update_check_title"), err))

        def on_check_finished(results):
            outdated = [r for r in results if r.get('status') == 'update_available']
            progress_dialog.append_log(f"Update check complete: {len(outdated)} updates found")
            progress_dialog.mark_done()

            if not outdated:
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle(tr("info_all_up_to_date_title"))
                msg_box.setText(tr("info_all_up_to_date_message"))
                msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
                msg_box.finished.connect(lambda: progress_dialog.close())
                msg_box.exec()
                self.refresh_mod_list()
                return

            dialog = OutdatedModsDialog(outdated, self)
            dialog.exec()

        worker.finished.connect(on_check_finished)
        self.worker_threads.append(worker)  # Keep reference
        worker.start()

    def show_update_all(self):
        """Run update-all flow: check and apply updates for outdated mods."""
        download_root = self.require_valid_library_root()
        if not download_root:
            return

        db = ModDatabase(self.db_path)
        mods = db.list_all_mods()

        if not mods:
            QMessageBox.information(self, tr("info_no_mods_title"), tr("info_no_mods_found"))
            return

        answer = QMessageBox.question(
            self,
            tr("question_update_all_mods_title"),
            tr("question_update_all_mods_message"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if answer != QMessageBox.Yes:
            return

        progress_dialog = OperationProgressDialog(tr("dialog_checking_updates"), self)
        progress_dialog.set_overall(0, len(mods))
        progress_dialog.set_current("Starting update check...")
        progress_dialog.show()

        worker_check = UpdateCheckThread(mods)
        worker_check.progress.connect(lambda done, total, current: [progress_dialog.set_overall(done, total), progress_dialog.set_current(current)])
        worker_check.error.connect(lambda err: QMessageBox.critical(self, tr("error_update_check_title"), err))

        def on_check_finished(results):
            outdated = [r for r in results if r.get('status') == 'update_available']
            if not outdated:
                progress_dialog.append_log("No updates needed.")
                progress_dialog.mark_done()
                QMessageBox.information(self, tr("info_all_up_to_date_title"), tr("info_all_up_to_date_message"))
                self.refresh_mod_list()
                return

            progress_dialog.append_log(f"{len(outdated)} mods need update. Starting update phase...")
            # Start update worker with outdated IDs
            workshop_ids = [item['workshop_id'] for item in outdated]
            update_dialog = OperationProgressDialog(tr("dialog_updating_all_outdated_mods"), self)
            update_dialog.set_overall(0, len(workshop_ids))
            update_dialog.show()

            def update_progress(done, total, current):
                update_dialog.set_overall(done, total)
                update_dialog.set_current(current)

            def update_finished(result):
                update_dialog.append_log(f"Updated: {result['updated']}, Failed: {result['failed']}")
                update_dialog.mark_done()
                self.refresh_mod_list()

            updater = UpdateModsThread(workshop_ids, download_root, self.db_path)
            updater.progress.connect(update_progress)
            updater.log.connect(update_dialog.append_log)
            updater.error.connect(lambda err: QMessageBox.critical(self, tr("error_update_title"), err))
            updater.finished.connect(update_finished)
            updater.start()

            progress_dialog.mark_done()

    def show_settings(self):
        """Show settings dialog."""
        dialog = SettingsDialog(self)
        dialog.exec()
        if dialog.result() == QDialog.Accepted:
            self.refresh_mod_list()
            return True
        return False

    @staticmethod
    def summarize_release_notes(notes, max_length=600):
        notes = (notes or "").strip()
        if not notes:
            return ""
        if len(notes) <= max_length:
            return notes
        return notes[:max_length].rstrip() + "..."

    def show_app_update_check(self):
        progress_dialog = OperationProgressDialog(tr("dialog_checking_updates"), self)
        progress_dialog.set_overall(0, 0)
        progress_dialog.set_current(tr("dialog_checking_updates"))
        progress_dialog.show()

        worker = AppUpdateCheckThread()

        def on_finished(result):
            progress_dialog.mark_done()
            progress_dialog.close()
            release = result["release"]
            if not result["update_available"]:
                QMessageBox.information(
                    self,
                    tr("dialog_app_up_to_date"),
                    tr("info_app_is_up_to_date_message"),
                )
                return

            message_box = QMessageBox(self)
            message_box.setIcon(QMessageBox.Information)
            message_box.setWindowTitle(tr("dialog_app_update_available"))
            message_box.setText(
                tr("info_app_update_available_message").format(
                    current_version=result["current_version"],
                    latest_version=result["latest_version"],
                )
            )
            informative_text = tr("info_update_will_restart_message")
            notes_summary = self.summarize_release_notes(release.notes)
            if notes_summary:
                informative_text += f"\n\n{tr('info_app_update_notes_label')}\n{notes_summary}"
                message_box.setDetailedText(release.notes)
            message_box.setInformativeText(informative_text)
            update_now_button = message_box.addButton(tr("button_update_now"), QMessageBox.AcceptRole)
            message_box.addButton(tr("button_later"), QMessageBox.RejectRole)
            message_box.exec()

            if message_box.clickedButton() == update_now_button:
                self.start_app_update_download(release)

        def on_error(error_message):
            progress_dialog.close()
            QMessageBox.warning(
                self,
                tr("dialog_app_update_error"),
                tr("error_update_check_failed_message").format(error=error_message),
            )

        worker.finished.connect(on_finished)
        worker.error.connect(on_error)
        self.worker_threads.append(worker)
        worker.start()

    def start_app_update_download(self, release):
        progress_dialog = OperationProgressDialog(tr("dialog_downloading_update"), self)
        progress_dialog.set_overall(0, 0)
        progress_dialog.set_current(tr("status_downloading_update"))
        progress_dialog.show()

        worker = AppUpdateDownloadThread(release)

        def on_progress(downloaded, total):
            progress_dialog.set_overall(downloaded, total)
            if total > 0:
                progress_dialog.set_current(
                    tr("status_update_download_progress").format(
                        current_mb=downloaded / (1024 * 1024),
                        total_mb=total / (1024 * 1024),
                    )
                )
            else:
                progress_dialog.set_current(tr("status_downloading_update"))

        def on_finished(package_path):
            progress_dialog.append_log(tr("log_update_package_downloaded").format(path=package_path))
            progress_dialog.mark_done()
            try:
                launch_updater_for_package(Path(package_path))
            except UpdateError as exc:
                progress_dialog.close()
                QMessageBox.critical(
                    self,
                    tr("dialog_app_update_error"),
                    tr("error_updater_launch_failed_message").format(error=exc),
                )
                return

            progress_dialog.close()
            QApplication.instance().quit()

        def on_error(error_message):
            progress_dialog.close()
            QMessageBox.warning(
                self,
                tr("dialog_app_update_error"),
                tr("error_update_download_failed_message").format(error=error_message),
            )

        worker.progress.connect(on_progress)
        worker.finished.connect(on_finished)
        worker.error.connect(on_error)
        self.worker_threads.append(worker)
        worker.start()

    def refresh_mod_db_on_startup_if_enabled(self):
        settings = SettingsManager(self.settings_path)
        if not settings.get_refresh_mod_db_on_startup():
            return

        library_root = settings.get_library_root()
        is_valid, detail = validate_library_root(library_root)
        if not is_valid or not library_root:
            if detail:
                logging.info(f"Skipping startup mod database refresh: {detail}")
            return

        self.mod_list.clear()
        self.all_mods = []
        self.detail_panel.clear_details()

        progress_dialog = OperationProgressDialog(tr("dialog_loading_library"), self)
        progress_dialog.set_overall(0, 0)
        progress_dialog.set_current(tr("status_scanning_library_root"))
        progress_dialog.show()

        worker = StartupLibraryRefreshThread(self.db_path, library_root)
        worker.progress.connect(
            lambda done, total, current: [
                progress_dialog.set_overall(done, total),
                progress_dialog.set_current(current),
            ]
        )
        worker.log.connect(progress_dialog.append_log)

        def on_finished(result):
            progress_dialog.append_log(
                tr("log_loading_library_complete").format(count=result.get("imported_count", 0))
            )
            progress_dialog.mark_done()
            self.refresh_mod_list()
            progress_dialog.close()

        def on_error(error_message):
            logging.error("Startup mod database refresh failed: %s", error_message)
            progress_dialog.close()
            QMessageBox.warning(
                self,
                tr("error_title"),
                tr("error_startup_refresh_mod_db_message").format(error=error_message),
            )

        worker.finished.connect(on_finished)
        worker.error.connect(on_error)
        self.worker_threads.append(worker)
        worker.start()
    
    def refresh_mod_list(self):
        """Refresh the mod list from database."""
        # Store current search and sort settings
        current_search = self.search_edit.text()
        current_sort = self.sort_combo.currentData()
        
        self.mod_list.clear()
        self.all_mods = []  # Store all mod data
        
        db = ModDatabase(self.db_path)
        mods = db.list_all_mods()
        
        for mod in mods:
            self.all_mods.append(mod)
            item = ModListItem(mod)
            self.mod_list.addItem(item)
        
        # Restore search and sort
        self.search_edit.setText(current_search)
        index = self.sort_combo.findData(current_sort)
        if index >= 0:
            self.sort_combo.setCurrentIndex(index)
        self.filter_mods()
        self.sort_mods()
    
    def filter_mods(self):
        """Filter mods based on search text."""
        search_text = self.search_edit.text().lower()
        
        for i in range(self.mod_list.count()):
            item = self.mod_list.item(i)
            mod_data = item.mod_data
            title = (mod_data.get('title') or '').lower()
            workshop_id = str(mod_data.get('workshop_id', '')).lower()
            
            visible = (search_text in title or search_text in workshop_id)
            item.setHidden(not visible)
    
    def sort_mods(self):
        """Sort mods based on selected criteria."""
        if not hasattr(self, 'all_mods'):
            return
            
        sort_by = self.sort_combo.currentData()
        
        # Sort the mod data
        if sort_by == 'alphabetical':
            self.all_mods.sort(key=lambda m: (m.get('title') or '').lower())
        elif sort_by == 'last_workshop_update':
            self.all_mods.sort(key=lambda m: m.get('remote_updated_at', 0), reverse=True)
        elif sort_by == 'last_download_time':
            self.all_mods.sort(key=lambda m: m.get('last_downloaded_at', 0), reverse=True)
        elif sort_by == 'file_size':
            def get_file_size(mod):
                content_path = mod.get('content_path')
                if content_path and os.path.exists(content_path):
                    try:
                        total_size = 0
                        for dirpath, dirnames, filenames in os.walk(content_path):
                            for filename in filenames:
                                filepath = os.path.join(dirpath, filename)
                                total_size += os.path.getsize(filepath)
                        return total_size
                    except:
                        pass
                return 0
            self.all_mods.sort(key=get_file_size, reverse=True)
        
        # Rebuild the list widget
        self.mod_list.clear()
        for mod in self.all_mods:
            item = ModListItem(mod)
            self.mod_list.addItem(item)
        
        # Reapply filter
        self.filter_mods()
    
    def on_mod_selected(self, item):
        """Handle mod selection."""
        if item:
            self.detail_panel.update_mod_details(item.mod_data)

def main():
    """Main application entry point."""
    configure_logging()
    app = QApplication(sys.argv)
    
    # Set application properties
    app.setApplicationName(tr("app_title"))
    app.setApplicationVersion(__version__)
    app.setOrganizationName(tr("app_title"))
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
