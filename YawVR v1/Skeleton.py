import sys
import time
import threading
import json
import os
import vgamepad as vg

from PySide6.QtWidgets import (QApplication, QMainWindow, QSystemTrayIcon,
                               QMenu, QVBoxLayout, QHBoxLayout, QWidget, QPushButton,
                               QTextEdit, QLabel, QListWidget, QDialog, QLineEdit)
from PySide6.QtGui import QAction, QIcon, QPixmap, QColor, QPainter, QFont, QPen
from PySide6.QtCore import QObject, Signal, Slot, Qt

from logic import InputMapper
from ui_mapper import MappingDialog


# --- CONFIGURATION MANAGER ---
class ConfigManager:
    DEFAULT_CONFIG = {
        "chair_settings": {
            "ip_address": "127.0.0.1",
            "tcp_port": 50020,
            "udp_port": 50010
        },
        "mappings": []
    }

    def __init__(self, filename="config.json"):
        self.filename = filename
        self.data = self.load()

    def load(self):
        if not os.path.exists(self.filename):
            return self.DEFAULT_CONFIG.copy()
        try:
            with open(self.filename, 'r') as f:
                return json.load(f)
        except Exception as e:
            return self.DEFAULT_CONFIG.copy()

    def save(self):
        with open(self.filename, 'w') as f:
            json.dump(self.data, f, indent=4)


# --- MAPPING LIST WINDOW ---
class MappingListWindow(QDialog):
    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Controller Mappings")
        self.resize(600, 400)
        self.config = config_manager

        layout = QVBoxLayout(self)
        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self.edit_mapping)
        self.refresh_list()
        layout.addWidget(self.list_widget)

        btn_box = QHBoxLayout()
        btn_add = QPushButton("Add New")
        btn_add.clicked.connect(self.add_mapping)
        btn_del = QPushButton("Delete Selected")
        btn_del.clicked.connect(self.delete_mapping)
        btn_box.addWidget(btn_add)
        btn_box.addWidget(btn_del)
        layout.addLayout(btn_box)

    def refresh_list(self):
        self.list_widget.clear()
        mappings = self.config.data.get('mappings', [])
        for m in mappings:
            # 1. Description
            desc = m.get('comment', '')
            if not desc: desc = "Mapping"

            # 2. Input Source
            dev_name = m.get('phys_device_name', 'Unknown')
            inp_type = m.get('phys_input_type')
            inp_id = m.get('phys_input_id')

            # 3. Output Target
            target = m.get('target', 'Unknown')
            if m.get('action_type') == 'sequence':
                opts = m.get('options', {})
                target = f"Sequence [{opts.get('t1')}->{opts.get('t2')}]"

            # Format: "Left Gun [Joystick 1: button 0] -> XUSB_GAMEPAD_A"
            info = f"{desc} [{dev_name}: {inp_type} {inp_id}] -> {target}"
            self.list_widget.addItem(info)

    def add_mapping(self):
        dlg = MappingDialog()
        if dlg.exec():
            self.config.data['mappings'].append(dlg.data)
            self.config.save()
            self.refresh_list()

    def edit_mapping(self, item):
        row = self.list_widget.row(item)
        if row < 0: return
        old_data = self.config.data['mappings'][row]
        dlg = MappingDialog(mapping_data=old_data)
        if dlg.exec():
            self.config.data['mappings'][row] = dlg.data
            self.config.save()
            self.refresh_list()

    def delete_mapping(self):
        row = self.list_widget.currentRow()
        if row >= 0:
            del self.config.data['mappings'][row]
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
            if self.thread:
                self.thread.join()
            self.signals.status_engine.emit(False)
            self.signals.log.emit("Engine Stopped.")

    def _check_chair_connection(self):
        if self.mapper:
            return self.mapper.is_chair_connected()
        return False

    def _run_loop(self):
        try:
            # RESET CONNECTION STATE
            # This forces the UI to update immediately when we reconnect
            self.chair_connected = False

            # Initialize Logic
            self.mapper = InputMapper(self.config.data, self.virtual_pad)
            self.signals.log.emit(f"Mapper initialized.")

            tick_count = 0
            while self.running:
                # 1. Check Chair Status
                if tick_count % 20 == 0:
                    is_connected = self._check_chair_connection()

                    # Force update if it's the first tick (tick_count 0)
                    if is_connected != self.chair_connected or tick_count == 0:
                        self.chair_connected = is_connected
                        self.signals.status_chair.emit(is_connected)
                        if is_connected:
                            # Only log if we just changed state
                            if tick_count > 0: self.signals.log.emit("Chair Connected (TCP).")
                        else:
                            if tick_count > 0: self.signals.log.emit("Chair Disconnected.")

                # 2. Process Inputs
                self.mapper.process_inputs()
                tick_count += 1
                time.sleep(0.02)  # 50Hz

        except Exception as e:
            self.signals.log.emit(f"CRITICAL ERROR: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if self.mapper:
                self.mapper.cleanup()


# --- MAIN WINDOW ---
class MainWindow(QMainWindow):
    def __init__(self, engine, tray_icon):
        super().__init__()
        self.engine = engine
        self.tray_icon = tray_icon
        self.setWindowTitle("YawVR Controller Bridge")
        self.resize(500, 450)

        # Icons
        self.icon_orange = self._create_letter_icon("darkorange", "Y")
        self.icon_green = self._create_letter_icon("green", "Y")

        # Layout
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.lbl_status = QLabel("Status: STOPPED")
        self.lbl_status.setStyleSheet("color: red; font-weight: bold; font-size: 14px;")
        layout.addWidget(self.lbl_status)

        # IP Row
        ip_layout = QHBoxLayout()
        ip_layout.addWidget(QLabel("Target IP:"))
        self.txt_ip = QLineEdit()
        current_ip = self.engine.config.data['chair_settings']['ip_address']
        self.txt_ip.setText(current_ip)
        ip_layout.addWidget(self.txt_ip)
        self.btn_save_ip = QPushButton("Save")
        self.btn_save_ip.clicked.connect(self.save_ip_settings)
        self.btn_save_ip.setFixedWidth(60)
        ip_layout.addWidget(self.btn_save_ip)
        layout.addLayout(ip_layout)

        # Buttons
        btn_layout = QHBoxLayout()
        self.btn_toggle = QPushButton("Start Engine")
        self.btn_toggle.clicked.connect(self.toggle_engine)
        btn_layout.addWidget(self.btn_toggle)

        self.btn_maps = QPushButton("Manage Mappings")
        self.btn_maps.clicked.connect(self.open_mapping_editor)
        btn_layout.addWidget(self.btn_maps)
        layout.addLayout(btn_layout)

        # Log
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
        font = QFont("Arial", 40, QFont.Bold)
        painter.setFont(font)
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
            self.update_log("Engine paused for configuration.")

        editor = MappingListWindow(self.engine.config, self)
        editor.exec()

        if was_running:
            self.engine.start()

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
            self.engine.signals.log.emit("Window minimized to tray.")
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

    # Initial Icon
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