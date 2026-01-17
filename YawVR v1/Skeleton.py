import sys
import time
import threading
import json
import os
import vgamepad as vg

from PySide6.QtWidgets import (QApplication, QMainWindow, QSystemTrayIcon,
                               QMenu, QVBoxLayout, QHBoxLayout, QWidget, QPushButton,
                               QTextEdit, QLabel, QListWidget, QDialog, QLineEdit, QListWidgetItem)
from PySide6.QtGui import QAction, QIcon, QPixmap, QColor, QPainter, QFont, QPen
from PySide6.QtCore import QObject, Signal, Slot, Qt

from logic import InputMapper
from ui_mapper import MappingDialog


# --- CONFIGURATION MANAGER ---
class ConfigManager:
    DEFAULT_CONFIG = {
        "chair_settings": {
            "ip_address": "127.0.0.1", "tcp_port": 50020, "udp_port": 50010
        },
        "mappings": []
    }

    def __init__(self, filename="config.json"):
        # DETERMINE THE REAL PATH
        if getattr(sys, 'frozen', False):
            # If we are running as an .exe, use the executable's folder
            application_path = os.path.dirname(sys.executable)
        else:
            # If we are running as a script, use the script's folder
            application_path = os.path.dirname(os.path.abspath(__file__))

        self.filepath = os.path.join(application_path, filename)
        self.data = self.load()

    def load(self):
        if not os.path.exists(self.filepath):
            return self.DEFAULT_CONFIG.copy()
        try:
            with open(self.filepath, 'r') as f: return json.load(f)
        except: return self.DEFAULT_CONFIG.copy()

    def save(self):
        with open(self.filepath, 'w') as f: json.dump(self.data, f, indent=4)


# --- MAPPING LIST WINDOW (TIMELINE VIEW) ---
class MappingListWindow(QDialog):
    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Controller Mappings (Timeline View)")
        self.resize(700, 500)
        self.config = config_manager

        layout = QVBoxLayout(self)
        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self.edit_mapping)
        layout.addWidget(self.list_widget)

        btn_box = QHBoxLayout()
        btn_add = QPushButton("Add New Action")
        btn_add.clicked.connect(self.add_mapping)
        btn_del = QPushButton("Delete Selected")
        btn_del.clicked.connect(self.delete_mapping)
        btn_box.addWidget(btn_add)
        btn_box.addWidget(btn_del)
        layout.addLayout(btn_box)

        self.refresh_list()

    def refresh_list(self):
        self.list_widget.clear()
        raw_mappings = self.config.data.get('mappings', [])

        # 1. GROUP BY PHYSICAL INPUT
        # Key: "DeviceName_InputType_InputID" -> List of mappings
        grouped = {}
        for i, m in enumerate(raw_mappings):
            key = f"{m.get('phys_device_name')}__{m.get('phys_input_type')}__{m.get('phys_input_id')}"
            if key not in grouped: grouped[key] = []
            # Store original index to allow editing/deleting later
            m['_original_index'] = i
            grouped[key].append(m)

        # 2. RENDER GROUPS
        for key, group in grouped.items():
            # Header Item (The Physical Button)
            first = group[0]
            header_text = f"► INPUT: {first.get('phys_device_name')} [{first.get('phys_input_type')} {first.get('phys_input_id')}]"
            item_head = QListWidgetItem(header_text)
            item_head.setBackground(QColor("#333333"))
            item_head.setForeground(QColor("white"))
            item_head.setFlags(Qt.ItemIsEnabled)  # Header not clickable
            self.list_widget.addItem(item_head)

            # Sort actions by Start Delay
            group.sort(key=lambda x: int(x.get('start_delay', 0)))

            # 3. RENDER ACTIONS (The Queue)
            for m in group:
                delay = m.get('start_delay', 0)
                desc = m.get('comment', 'Action')
                target = m.get('target', 'Unknown')

                # Visual Indentation for Timeline
                time_prefix = f"   +{delay}ms: " if delay > 0 else "   INSTANT: "

                if m.get('action_type') == 'rumble':
                    info = f"{time_prefix} RUMBLE {target} ({desc})"
                elif m.get('action_type') == 'sequence':
                    info = f"{time_prefix} PULSE ({desc})"
                else:
                    info = f"{time_prefix} {m.get('action_type')} -> {target} ({desc})"

                item = QListWidgetItem(info)
                # Store the index in the actual list so we know what to delete
                item.setData(Qt.UserRole, m['_original_index'])
                self.list_widget.addItem(item)

            # Spacer
            self.list_widget.addItem(QListWidgetItem(""))

    def add_mapping(self):
        dlg = MappingDialog()
        if dlg.exec():
            self.config.data['mappings'].append(dlg.data)
            self.config.save()
            self.refresh_list()

    def edit_mapping(self, item):
        # Retrieve original index from UserRole data
        idx = item.data(Qt.UserRole)
        if idx is None: return  # Clicked a header or spacer

        old_data = self.config.data['mappings'][idx]
        dlg = MappingDialog(mapping_data=old_data)
        if dlg.exec():
            self.config.data['mappings'][idx] = dlg.data
            self.config.save()
            self.refresh_list()

    def delete_mapping(self):
        item = self.list_widget.currentItem()
        if not item: return
        idx = item.data(Qt.UserRole)
        if idx is None: return

        # Delete from list
        del self.config.data['mappings'][idx]
        self.config.save()
        self.refresh_list()


# --- WORKER SIGNALS ---
class WorkerSignals(QObject):
    log = Signal(str)
    status_engine = Signal(bool)
    status_chair = Signal(bool)


# --- THE ENGINE ---
class ControllerEngine:
    def __init__(self, signals, config_manager, shared_pad):
        self.signals = signals
        self.config = config_manager
        self.virtual_pad = shared_pad
        self.running = False
        self.thread = None
        self.chair_connected = False
        self.mapper = None

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()
            self.signals.status_engine.emit(True)
            self.signals.log.emit("Engine Started.")

    def stop(self):
        if self.running:
            self.running = False
            if self.thread: self.thread.join()
            self.signals.status_engine.emit(False)
            self.signals.log.emit("Engine Stopped.")

    def _check_chair_connection(self):
        if self.mapper: return self.mapper.is_chair_connected()
        return False

    def _run_loop(self):
        try:
            self.chair_connected = False  # Force UI update on reconnect
            self.mapper = InputMapper(self.config.data, self.virtual_pad)
            self.signals.log.emit(f"Mapper initialized.")

            tick_count = 0
            while self.running:
                if tick_count % 20 == 0:
                    is_connected = self._check_chair_connection()
                    if is_connected != self.chair_connected or tick_count == 0:
                        self.chair_connected = is_connected
                        self.signals.status_chair.emit(is_connected)
                        if is_connected and tick_count > 0:
                            self.signals.log.emit("Chair Connected (TCP).")
                        elif not is_connected and tick_count > 0:
                            self.signals.log.emit("Chair Disconnected.")

                self.mapper.process_inputs()
                tick_count += 1
                time.sleep(0.02)
        except Exception as e:
            self.signals.log.emit(f"CRITICAL ERROR: {e}")
        finally:
            if self.mapper: self.mapper.cleanup()


# --- MAIN WINDOW ---
class MainWindow(QMainWindow):
    def __init__(self, engine, tray_icon):
        super().__init__()
        self.engine = engine
        self.tray_icon = tray_icon
        self.setWindowTitle("YawVR Controller Bridge")
        self.resize(500, 450)
        self.icon_orange = self._create_letter_icon("darkorange", "Y")
        self.icon_green = self._create_letter_icon("green", "Y")

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.lbl_status = QLabel("Status: STOPPED")
        self.lbl_status.setStyleSheet("color: red; font-weight: bold; font-size: 14px;")
        layout.addWidget(self.lbl_status)

        ip_layout = QHBoxLayout()
        ip_layout.addWidget(QLabel("Target IP:"))
        self.txt_ip = QLineEdit()
        self.txt_ip.setText(self.engine.config.data['chair_settings']['ip_address'])
        ip_layout.addWidget(self.txt_ip)
        self.btn_save_ip = QPushButton("Save")
        self.btn_save_ip.clicked.connect(self.save_ip_settings)
        self.btn_save_ip.setFixedWidth(60)
        ip_layout.addWidget(self.btn_save_ip)
        layout.addLayout(ip_layout)

        btn_layout = QHBoxLayout()
        self.btn_toggle = QPushButton("Start Engine")
        self.btn_toggle.clicked.connect(self.toggle_engine)
        btn_layout.addWidget(self.btn_toggle)
        self.btn_maps = QPushButton("Manage Mappings")
        self.btn_maps.clicked.connect(self.open_mapping_editor)
        btn_layout.addWidget(self.btn_maps)
        layout.addLayout(btn_layout)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        layout.addWidget(self.txt_log)
        self.force_quit = False

    def save_ip_settings(self):
        new_ip = self.txt_ip.text().strip()
        if new_ip:
            self.engine.config.data['chair_settings']['ip_address'] = new_ip
            self.engine.config.save()
            self.update_log(f"Configuration Saved: Target IP set to {new_ip}")

    def _create_letter_icon(self, color_name, letter):
        pixmap = QPixmap(64, 64)
        pixmap.fill(QColor(color_name))
        painter = QPainter(pixmap)
        painter.setPen(QPen(Qt.white))
        painter.setFont(QFont("Arial", 40, QFont.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, letter)
        painter.end()
        return QIcon(pixmap)

    def toggle_engine(self):
        if self.engine.running:
            self.engine.stop()
        else:
            self.engine.start()

    def open_mapping_editor(self):
        was_running = self.engine.running
        if was_running:
            self.engine.stop()
            self.update_log("Configuration Mode.")

        editor = MappingListWindow(self.engine.config, self)
        editor.exec()

        if was_running: self.engine.start()

    @Slot(str)
    def update_log(self, message):
        self.txt_log.append(message)

    @Slot(bool)
    def update_engine_status(self, is_running):
        if is_running:
            self.lbl_status.setText("Status: SEARCHING...")
            self.lbl_status.setStyleSheet("color: orange; font-weight: bold;")
            self.btn_toggle.setText("Stop Engine")
            self.tray_icon.setIcon(self.icon_orange)
            self.action_toggle.setText("Stop Engine")
        else:
            self.lbl_status.setText("Status: STOPPED")
            self.lbl_status.setStyleSheet("color: red; font-weight: bold;")
            self.btn_toggle.setText("Start Engine")
            self.tray_icon.setIcon(self.icon_orange)
            self.action_toggle.setText("Start Engine")

    @Slot(bool)
    def update_chair_status(self, is_connected):
        if is_connected:
            self.lbl_status.setText("Status: CHAIR CONNECTED")
            self.lbl_status.setStyleSheet("color: green; font-weight: bold;")
            self.tray_icon.setIcon(self.icon_green)
        else:
            self.lbl_status.setText("Status: SEARCHING...")
            self.lbl_status.setStyleSheet("color: orange; font-weight: bold;")
            self.tray_icon.setIcon(self.icon_orange)

    def closeEvent(self, event):
        if not self.force_quit:
            event.ignore()
            self.hide()
            self.engine.signals.log.emit("Minimized to tray.")
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    print("Initializing Virtual Controller...")
    persistent_pad = vg.VX360Gamepad()

    config = ConfigManager()
    signals = WorkerSignals()
    engine = ControllerEngine(signals, config, persistent_pad)

    initial_pix = QPixmap(64, 64)
    initial_pix.fill(QColor("darkorange"))
    painter = QPainter(initial_pix)
    painter.setPen(QPen(Qt.white))
    painter.setFont(QFont("Arial", 40, QFont.Bold))
    painter.drawText(initial_pix.rect(), Qt.AlignCenter, "Y")
    painter.end()
    tray_icon = QSystemTrayIcon(QIcon(initial_pix), app)

    window = MainWindow(engine, tray_icon)
    signals.log.connect(window.update_log)
    signals.status_engine.connect(window.update_engine_status)
    signals.status_chair.connect(window.update_chair_status)

    menu = QMenu()
    action_conf = QAction("Settings", app)
    action_conf.triggered.connect(window.show)
    menu.addAction(action_conf)
    window.action_toggle = QAction("Start Engine", app)
    window.action_toggle.triggered.connect(window.toggle_engine)
    menu.addAction(window.action_toggle)
    menu.addSeparator()
    action_quit = QAction("Quit", app)

    def quit_app():
        engine.stop()
        window.force_quit = True
        app.quit()

    action_quit.triggered.connect(quit_app)
    menu.addAction(action_quit)

    tray_icon.setContextMenu(menu)
    tray_icon.show()
    tray_icon.activated.connect(lambda r: window.show() if r == QSystemTrayIcon.DoubleClick else None)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()