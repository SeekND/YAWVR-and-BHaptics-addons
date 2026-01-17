import sys
import json
import threading
import asyncio
import os
from PySide6.QtWidgets import (QApplication, QMainWindow, QSystemTrayIcon, QMenu,
                               QVBoxLayout, QHBoxLayout, QWidget, QPushButton,
                               QTextEdit, QLabel, QListWidget)
from PySide6.QtGui import QAction, QIcon, QPixmap, QColor, QPainter, QFont
from PySide6.QtCore import QObject, Signal, Slot, Qt

# Imports
from bhaptics_logic import InputMonitor
from bhaptics_ui import BhapticsMappingDialog, EffectCreatorDialog


# --- HELPER: ICON GENERATOR ---
def create_icon(color_name, text="B"):
    pix = QPixmap(64, 64)
    pix.fill(QColor(color_name))
    painter = QPainter(pix)
    painter.setPen(QColor("white"))
    font = QFont("Segoe UI", 40, QFont.Bold)
    painter.setFont(font)
    painter.drawText(pix.rect(), Qt.AlignCenter, text)
    painter.end()
    return QIcon(pix)


class ConfigManager:
    FILENAME = "bhaptics_config.json"
    DEFAULT = {"mappings": [], "custom_effects": []}

    def __init__(self):
        self.data = self.load()

    def load(self):
        if not os.path.exists(self.FILENAME):
            return self.DEFAULT.copy()
        try:
            with open(self.FILENAME, 'r') as f:
                return json.load(f)
        except:
            return self.DEFAULT.copy()

    def save(self):
        with open(self.FILENAME, 'w') as f:
            json.dump(self.data, f, indent=4)


class WorkerSignals(QObject):
    log = Signal(str)
    status = Signal(bool)


class HapticEngine:
    def __init__(self, signals, config_manager):
        self.signals = signals
        self.config = config_manager
        self.running = False
        self.thread = None
        self.monitor = None

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()
            self.signals.status.emit(True)

    def stop(self):
        if self.running:
            self.running = False
            if self.monitor:
                self.monitor.stop()
            if self.thread:
                self.thread.join()
            self.signals.status.emit(False)
            self.signals.log.emit("Engine Stopped.")

    def _run_loop(self):
        self.signals.log.emit("Starting Asyncio Loop...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        self.monitor = InputMonitor(self.config.data)

        try:
            loop.run_until_complete(self.monitor.run_loop())
        except Exception as e:
            self.signals.log.emit(f"Engine Error: {e}")
        finally:
            loop.close()
            self.signals.log.emit("Async Loop Closed.")


class MainWindow(QMainWindow):
    def __init__(self, engine, tray_icon):
        super().__init__()
        self.engine = engine
        self.tray_icon = tray_icon
        self.setWindowTitle("bHaptics Bridge (Async)")
        self.resize(600, 500)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.lbl_status = QLabel("Status: STOPPED")
        self.lbl_status.setStyleSheet("color: red; font-weight: bold; font-size: 16px;")
        layout.addWidget(self.lbl_status)

        self.btn_toggle = QPushButton("Start Engine")
        self.btn_toggle.clicked.connect(self.toggle_engine)
        layout.addWidget(self.btn_toggle)

        layout.addWidget(QLabel("Mappings:"))
        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self.edit_mapping)
        layout.addWidget(self.list_widget)

        btn_box = QHBoxLayout()
        btn_add = QPushButton("Add Mapping")
        btn_add.clicked.connect(self.add_mapping)
        btn_fx = QPushButton("Effects Creator")  # NEW BUTTON
        btn_fx.clicked.connect(self.open_effect_creator)
        btn_del = QPushButton("Delete")
        btn_del.clicked.connect(self.delete_mapping)
        btn_box.addWidget(btn_add)
        btn_box.addWidget(btn_fx)
        btn_box.addWidget(btn_del)
        layout.addLayout(btn_box)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(150)
        layout.addWidget(self.txt_log)

        self.refresh_list()
        self.force_quit = False

    def refresh_list(self):
        self.list_widget.clear()
        for m in self.engine.config.data.get('mappings', []):
            self.list_widget.addItem(f"{m.get('name')} -> {m.get('effect_name')}")

    def add_mapping(self):
        was_running = self.engine.running
        if was_running: self.engine.stop()

        all_maps = self.engine.config.data['mappings']
        # PASS CONFIG DATA
        dlg = BhapticsMappingDialog(all_mappings=all_maps, config_data=self.engine.config.data, parent=self)

        if dlg.exec():
            self.engine.config.data['mappings'].append(dlg.data)
            self.engine.config.save()
            self.refresh_list()
            self.update_log(f"Added: {dlg.data.get('name')}")

        if was_running: self.engine.start()

    def edit_mapping(self, item):
        row = self.list_widget.row(item)
        if row < 0: return
        was_running = self.engine.running
        if was_running: self.engine.stop()

        old_data = self.engine.config.data['mappings'][row]
        all_maps = self.engine.config.data['mappings']
        # PASS CONFIG DATA
        dlg = BhapticsMappingDialog(mapping_data=old_data, all_mappings=all_maps, config_data=self.engine.config.data,
                                    parent=self)
        if dlg.exec():
            self.engine.config.data['mappings'][row] = dlg.data
            self.engine.config.save()
            self.refresh_list()

        if was_running: self.engine.start()

    def open_effect_creator(self):
        was_running = self.engine.running
        if was_running: self.engine.stop()

        dlg = EffectCreatorDialog(config_data=self.engine.config.data, parent=self)
        if dlg.exec():
            self.engine.config.save()
            self.update_log("Effects Saved.")

        if was_running: self.engine.start()

    def delete_mapping(self):
        row = self.list_widget.currentRow()
        if row >= 0:
            del self.engine.config.data['mappings'][row]
            self.engine.config.save()
            self.refresh_list()

    def toggle_engine(self):
        if self.engine.running:
            self.engine.stop()
        else:
            self.engine.start()

    @Slot(str)
    def update_log(self, msg):
        self.txt_log.append(msg)

    @Slot(bool)
    def update_status(self, is_running):
        if is_running:
            self.lbl_status.setText("Status: RUNNING")
            self.lbl_status.setStyleSheet("color: green; font-weight: bold;")
            self.btn_toggle.setText("Stop Engine")
            self.tray_icon.setIcon(create_icon("green", "B"))
            self.tray_icon.setToolTip("bHaptics Bridge: RUNNING")
        else:
            self.lbl_status.setText("Status: STOPPED")
            self.lbl_status.setStyleSheet("color: red; font-weight: bold;")
            self.btn_toggle.setText("Start Engine")
            self.tray_icon.setIcon(create_icon("darkorange", "B"))
            self.tray_icon.setToolTip("bHaptics Bridge: STOPPED")

    def closeEvent(self, event):
        if not self.force_quit:
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(
                "bHaptics Bridge",
                "Application minimized to tray.",
                QSystemTrayIcon.Information,
                2000
            )
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    config = ConfigManager()
    signals = WorkerSignals()
    engine = HapticEngine(signals, config)

    tray_icon = QSystemTrayIcon(create_icon("darkorange", "B"), app)
    tray_icon.setToolTip("bHaptics Bridge: STOPPED")

    window = MainWindow(engine, tray_icon)
    signals.log.connect(window.update_log)
    signals.status.connect(window.update_status)

    menu = QMenu()
    action_open = QAction("Open", app)
    action_open.triggered.connect(window.show)
    menu.addAction(action_open)

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