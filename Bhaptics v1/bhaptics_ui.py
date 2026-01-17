import sys
import pygame
import win32api
import json
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QComboBox, QSpinBox, QCheckBox, QPushButton,
                               QGroupBox, QFormLayout, QLineEdit, QWidget, QSlider,
                               QListWidget, QListWidgetItem, QTabWidget, QGridLayout, QScrollArea)
from PySide6.QtCore import Qt, QTimer, Signal

from bhaptics_logic import KEY_MAP


# --- VEST VISUALIZER WIDGET ---
class VestMotorBtn(QPushButton):
    """Round Checkable Button representing a motor"""

    def __init__(self, index, parent=None):
        super().__init__(parent)
        self.index = index
        self.setCheckable(True)
        self.setFixedSize(30, 30)
        self.setToolTip(f"Motor {index}")
        # Round style
        self.setStyleSheet("""
            QPushButton {
                background-color: #ddd;
                border-radius: 15px;
                border: 2px solid #999;
            }
            QPushButton:checked {
                background-color: #ff5500;
                border: 2px solid #cc4400;
            }
        """)


class VestWidget(QWidget):
    """Visual Grid of the TactSuit (Front & Back)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.motors = {}  # Index -> Btn

        layout = QHBoxLayout(self)

        # FRONT GRID (Indices 0-19)
        grp_front = QGroupBox("Front (0-19)")
        gl_front = QGridLayout()
        # Approximate bHaptics Layout (4x5)
        # 0  1  2  3
        # 4  5  6  7
        # 8  9  10 11
        # 12 13 14 15
        # 16 17 18 19
        front_indices = [
            [0, 1, 2, 3],
            [4, 5, 6, 7],
            [8, 9, 10, 11],
            [12, 13, 14, 15],
            [16, 17, 18, 19]
        ]
        self._build_grid(gl_front, front_indices, 0)
        grp_front.setLayout(gl_front)

        # BACK GRID (Indices 20-39)
        grp_back = QGroupBox("Back (20-39)")
        gl_back = QGridLayout()
        # 20 21 22 23
        # ...
        back_indices = [
            [20, 21, 22, 23],
            [24, 25, 26, 27],
            [28, 29, 30, 31],
            [32, 33, 34, 35],
            [36, 37, 38, 39]
        ]
        self._build_grid(gl_back, back_indices, 0)
        grp_back.setLayout(gl_back)

        layout.addWidget(grp_front)
        layout.addWidget(grp_back)

    def _build_grid(self, layout, map_arr, offset):
        for r, row in enumerate(map_arr):
            for c, idx in enumerate(row):
                btn = VestMotorBtn(idx)
                layout.addWidget(btn, r, c)
                self.motors[idx] = btn

    def get_selected(self):
        return [idx for idx, btn in self.motors.items() if btn.isChecked()]

    def set_selected(self, indices):
        for idx, btn in self.motors.items():
            btn.setChecked(idx in indices)

    def clear(self):
        for btn in self.motors.values():
            btn.setChecked(False)


# --- EFFECT CREATOR DIALOG ---
class EffectCreatorDialog(QDialog):
    def __init__(self, config_data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Custom Effects Creator")
        self.resize(800, 600)
        self.config_data = config_data

        # Ensure custom list exists
        if "custom_effects" not in self.config_data:
            self.config_data["custom_effects"] = []

        layout = QHBoxLayout(self)

        # LEFT: List of Effects
        left_panel = QVBoxLayout()
        left_panel.addWidget(QLabel("Saved Effects:"))
        self.list_effects = QListWidget()
        self.list_effects.itemClicked.connect(self.load_effect)
        left_panel.addWidget(self.list_effects)

        btn_new = QPushButton("New Effect")
        btn_new.clicked.connect(self.new_effect)
        btn_del = QPushButton("Delete Selected")
        btn_del.clicked.connect(self.delete_effect)
        left_panel.addWidget(btn_new)
        left_panel.addWidget(btn_del)

        # RIGHT: Editor
        self.tabs = QTabWidget()

        # TAB 1: STATIC
        self.tab_static = QWidget()
        l_static = QVBoxLayout(self.tab_static)

        form_static = QFormLayout()
        self.txt_name_static = QLineEdit()
        self.spin_int_static = QSpinBox()
        self.spin_int_static.setRange(0, 100)
        self.spin_int_static.setValue(100)
        self.spin_dur_static = QSpinBox()
        self.spin_dur_static.setRange(10, 2000)
        self.spin_dur_static.setValue(100)
        self.spin_dur_static.setSuffix(" ms")

        form_static.addRow("Name:", self.txt_name_static)
        form_static.addRow("Intensity:", self.spin_int_static)
        form_static.addRow("Duration:", self.spin_dur_static)
        l_static.addLayout(form_static)

        self.vest_static = VestWidget()
        l_static.addWidget(self.vest_static)

        btn_save_static = QPushButton("Save Static Effect")
        btn_save_static.clicked.connect(self.save_static)
        l_static.addWidget(btn_save_static)

        self.tabs.addTab(self.tab_static, "Mode A: Static Frame")

        # TAB 2: SEQUENCE
        self.tab_seq = QWidget()
        l_seq = QVBoxLayout(self.tab_seq)

        form_seq = QFormLayout()
        self.txt_name_seq = QLineEdit()
        form_seq.addRow("Sequence Name:", self.txt_name_seq)
        l_seq.addLayout(form_seq)

        l_seq.addWidget(QLabel("Frames:"))
        self.list_frames = QListWidget()
        self.list_frames.itemClicked.connect(self.load_frame)
        l_seq.addWidget(self.list_frames)

        # Frame Editor (Embedded)
        grp_frame = QGroupBox("Current Frame Editor")
        l_frame = QVBoxLayout()

        f_sets = QHBoxLayout()
        self.spin_int_frame = QSpinBox()
        self.spin_int_frame.setRange(0, 100);
        self.spin_int_frame.setValue(100);
        self.spin_int_frame.setPrefix("Int: ")
        self.spin_dur_frame = QSpinBox()
        self.spin_dur_frame.setRange(10, 2000);
        self.spin_dur_frame.setValue(100);
        self.spin_dur_frame.setSuffix(" ms");
        self.spin_dur_frame.setPrefix("Dur: ")
        self.spin_delay_frame = QSpinBox()
        self.spin_delay_frame.setRange(0, 2000);
        self.spin_delay_frame.setValue(50);
        self.spin_delay_frame.setSuffix(" ms");
        self.spin_delay_frame.setPrefix("Wait: ")

        f_sets.addWidget(self.spin_int_frame)
        f_sets.addWidget(self.spin_dur_frame)
        f_sets.addWidget(self.spin_delay_frame)
        l_frame.addLayout(f_sets)

        self.vest_frame = VestWidget()
        l_frame.addWidget(self.vest_frame)

        btn_add_frame = QPushButton("Add/Update Frame")
        btn_add_frame.clicked.connect(self.save_frame)
        btn_del_frame = QPushButton("Remove Frame")
        btn_del_frame.clicked.connect(self.delete_frame)

        l_frame.addWidget(btn_add_frame)
        l_frame.addWidget(btn_del_frame)
        grp_frame.setLayout(l_frame)
        l_seq.addWidget(grp_frame)

        btn_save_seq = QPushButton("Save Sequence Effect")
        btn_save_seq.clicked.connect(self.save_sequence)
        l_seq.addWidget(btn_save_seq)

        self.tabs.addTab(self.tab_seq, "Mode B: Sequence")

        # Layout Assembly
        layout.addLayout(left_panel, 1)
        layout.addWidget(self.tabs, 3)

        self.current_seq_frames = []  # Temp storage
        self.refresh_list()

    def refresh_list(self):
        self.list_effects.clear()
        for fx in self.config_data["custom_effects"]:
            self.list_effects.addItem(f"{fx['name']} ({fx['type']})")

    def new_effect(self):
        self.txt_name_static.clear()
        self.vest_static.clear()
        self.txt_name_seq.clear()
        self.current_seq_frames = []
        self.refresh_frames_list()

    def delete_effect(self):
        row = self.list_effects.currentRow()
        if row >= 0:
            del self.config_data["custom_effects"][row]
            self.refresh_list()

    def load_effect(self, item):
        row = self.list_effects.row(item)
        data = self.config_data["custom_effects"][row]

        if data['type'] == 'static':
            self.tabs.setCurrentIndex(0)
            self.txt_name_static.setText(data['name'])
            self.spin_int_static.setValue(data['intensity'])
            self.spin_dur_static.setValue(data['duration'])
            self.vest_static.set_selected(data['motors'])
        else:
            self.tabs.setCurrentIndex(1)
            self.txt_name_seq.setText(data['name'])
            self.current_seq_frames = data['frames']
            self.refresh_frames_list()

    # --- STATIC LOGIC ---
    def save_static(self):
        name = self.txt_name_static.text()
        if not name: return

        new_fx = {
            "name": name,
            "type": "static",
            "motors": self.vest_static.get_selected(),
            "intensity": self.spin_int_static.value(),
            "duration": self.spin_dur_static.value()
        }
        self._upsert_effect(new_fx)

    # --- SEQUENCE LOGIC ---
    def refresh_frames_list(self):
        self.list_frames.clear()
        for i, f in enumerate(self.current_seq_frames):
            self.list_frames.addItem(f"Frame {i + 1}: {len(f['motors'])} motors, {f['duration']}ms")

    def load_frame(self, item):
        row = self.list_frames.row(item)
        frame = self.current_seq_frames[row]
        self.vest_frame.set_selected(frame['motors'])
        self.spin_int_frame.setValue(frame['intensity'])
        self.spin_dur_frame.setValue(frame['duration'])
        self.spin_delay_frame.setValue(frame['delay'])

    def save_frame(self):
        frame = {
            "motors": self.vest_frame.get_selected(),
            "intensity": self.spin_int_frame.value(),
            "duration": self.spin_dur_frame.value(),
            "delay": self.spin_delay_frame.value()
        }

        row = self.list_frames.currentRow()
        if row >= 0:
            self.current_seq_frames[row] = frame
        else:
            self.current_seq_frames.append(frame)
        self.refresh_frames_list()

    def delete_frame(self):
        row = self.list_frames.currentRow()
        if row >= 0:
            del self.current_seq_frames[row]
            self.refresh_frames_list()

    def save_sequence(self):
        name = self.txt_name_seq.text()
        if not name or not self.current_seq_frames: return

        new_fx = {
            "name": name,
            "type": "sequence",
            "frames": self.current_seq_frames
        }
        self._upsert_effect(new_fx)

    def _upsert_effect(self, new_fx):
        # Update if exists, else add
        found = False
        for i, fx in enumerate(self.config_data["custom_effects"]):
            if fx['name'] == new_fx['name']:
                self.config_data["custom_effects"][i] = new_fx
                found = True
                break
        if not found:
            self.config_data["custom_effects"].append(new_fx)

        self.refresh_list()


# --- EXISTING INPUT DETECTOR (Unchanged) ---
class UniversalInputDetector(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Listening for Input...")
        self.resize(400, 150)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Press Key, Mouse, or Joystick...", alignment=Qt.AlignCenter))
        self.lbl_status = QLabel("Waiting...", alignment=Qt.AlignCenter)
        self.lbl_status.setStyleSheet("font-size: 14px; font-weight: bold; color: blue;")
        layout.addWidget(self.lbl_status)
        self.detected_data = None
        if not pygame.get_init(): pygame.init()
        if not pygame.joystick.get_init(): pygame.joystick.init()
        self.joysticks = [pygame.joystick.Joystick(x) for x in range(pygame.joystick.get_count())]
        for j in self.joysticks: j.init()
        self.prev_state = {}
        for k, code in KEY_MAP.items():
            self.prev_state[k] = (win32api.GetAsyncKeyState(code) & 0x8000) != 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_inputs)
        self.timer.start(20)

    def poll_inputs(self):
        for event in pygame.event.get():
            if event.type == pygame.JOYBUTTONDOWN:
                self.found("joy_button", event.joy, event.button, f"Joy {event.joy} Btn {event.button}")
                return
            elif event.type == pygame.JOYAXISMOTION:
                if abs(event.value) > 0.5:
                    self.found("joy_axis", event.joy, event.axis, f"Joy {event.joy} Axis {event.axis}")
                    return
        for key_name, vk_code in KEY_MAP.items():
            is_down = (win32api.GetAsyncKeyState(vk_code) & 0x8000) != 0
            was_down = self.prev_state.get(key_name, False)
            if is_down and not was_down:
                input_type = "mouse" if "MOUSE" in key_name else "keyboard"
                self.found(input_type, 0, key_name, f"{input_type.title()}: {key_name}")
                return
            self.prev_state[key_name] = is_down

    def found(self, i_type, device_idx, i_id, desc):
        self.detected_data = {
            "input_type": i_type, "device_index": device_idx,
            "input_id": i_id, "description": desc
        }
        self.lbl_status.setText(f"Detected: {desc}")
        QTimer.singleShot(500, self.accept)
        self.timer.stop()


class BhapticsMappingDialog(QDialog):
    def __init__(self, mapping_data=None, all_mappings=None, config_data=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Haptic Bind")
        self.resize(500, 780)
        self.data = mapping_data or {}
        self.config_full = config_data  # Needed to load custom effects list
        self.existing_bind_names = [m.get('name') for m in (all_mappings or [])]

        layout = QVBoxLayout(self)

        # 1. INPUT
        grp_input = QGroupBox("1. Input Source")
        l_input = QFormLayout()
        self.lbl_input_desc = QLineEdit()
        self.lbl_input_desc.setReadOnly(True)
        self.lbl_input_desc.setPlaceholderText("Click Detect...")
        btn_detect = QPushButton("Detect Input")
        btn_detect.clicked.connect(self.run_detection)
        l_input.addRow("Source:", self.lbl_input_desc)
        l_input.addRow("", btn_detect)
        grp_input.setLayout(l_input)
        layout.addWidget(grp_input)

        # 2. ACTION
        grp_action = QGroupBox("2. Haptic Action")
        l_action = QFormLayout()
        self.txt_name = QLineEdit()
        self.combo_effect = QComboBox()

        # HARDCODED EFFECTS
        defaults = [
            "front_rear_center", "front_rear_lower_edges",
            "front_outter_right_chest", "front_inner_right_chest"
        ]
        self.combo_effect.addItems(defaults)

        # CUSTOM EFFECTS
        if self.config_full and "custom_effects" in self.config_full:
            for fx in self.config_full["custom_effects"]:
                self.combo_effect.addItem(fx["name"])

        l_action.addRow("Name:", self.txt_name)
        l_action.addRow("Effect:", self.combo_effect)
        grp_action.setLayout(l_action)
        layout.addWidget(grp_action)

        # 3. BUTTON OPTIONS
        self.grp_btn_opts = QGroupBox("3. Button Options")
        l_btn = QFormLayout()
        self.spin_hold = QSpinBox()
        self.spin_hold.setRange(0, 5000);
        self.spin_hold.setSuffix(" ms");
        self.spin_hold.setSpecialValueText("Instant")
        self.chk_turbo = QCheckBox("Turbo Mode")
        self.spin_turbo_rate = QSpinBox()
        self.spin_turbo_rate.setRange(50, 2000);
        self.spin_turbo_rate.setValue(100);
        self.spin_turbo_rate.setSuffix(" ms")
        l_btn.addRow("Hold Timer:", self.spin_hold)
        l_btn.addRow(self.chk_turbo)
        l_btn.addRow("Repeat Rate:", self.spin_turbo_rate)
        self.grp_btn_opts.setLayout(l_btn)
        layout.addWidget(self.grp_btn_opts)

        # 4. AXIS OPTIONS
        self.grp_axis_opts = QGroupBox("3. Axis Options")
        l_axis = QFormLayout()
        self.combo_axis_dir = QComboBox()
        self.combo_axis_dir.addItem("Both (0 to +1 AND 0 to -1)", "both")
        self.combo_axis_dir.addItem("Forward Only (0 to +1)", "positive")
        self.combo_axis_dir.addItem("Backward Only (0 to -1)", "negative")
        self.slider_max = QSlider(Qt.Horizontal)
        self.slider_max.setRange(0, 100);
        self.slider_max.setValue(100)
        self.lbl_max_val = QLabel("100%")
        self.slider_max.valueChanged.connect(lambda v: self.lbl_max_val.setText(f"{v}%"))
        self.slider_sat = QSlider(Qt.Horizontal)
        self.slider_sat.setRange(1, 100);
        self.slider_sat.setValue(100)
        self.lbl_sat_val = QLabel("100% (Normal)")
        self.slider_sat.valueChanged.connect(self.update_sat_label)
        l_axis.addRow("Direction:", self.combo_axis_dir)
        l_axis.addRow("Max Strength:", self.slider_max)
        l_axis.addRow("", self.lbl_max_val)
        l_axis.addRow("Edge Threshold:", self.slider_sat)
        l_axis.addRow("", self.lbl_sat_val)
        self.grp_axis_opts.setLayout(l_axis)
        layout.addWidget(self.grp_axis_opts)

        # 5. INTERACTIONS
        grp_interact = QGroupBox("4. Interactions (Disabling)")
        l_interact = QVBoxLayout()
        self.chk_start_disabled = QCheckBox("Start Disabled (Must be enabled by another bind)")
        self.chk_start_disabled.setStyleSheet("color: red; font-weight: bold;")
        l_interact.addWidget(self.chk_start_disabled)
        l_interact.addWidget(QLabel("-----------------"))
        l_interact.addWidget(QLabel("When this bind triggers:"))
        hbox = QHBoxLayout()
        vbox_dis = QVBoxLayout();
        vbox_dis.addWidget(QLabel("DISABLE these:"))
        self.list_disable = QListWidget()
        vbox_dis.addWidget(self.list_disable)
        vbox_en = QVBoxLayout();
        vbox_en.addWidget(QLabel("ENABLE these:"))
        self.list_enable = QListWidget()
        vbox_en.addWidget(self.list_enable)
        hbox.addLayout(vbox_dis);
        hbox.addLayout(vbox_en)
        l_interact.addLayout(hbox)
        grp_interact.setLayout(l_interact)
        layout.addWidget(grp_interact)

        for name in self.existing_bind_names:
            item_d = QListWidgetItem(name);
            item_d.setFlags(item_d.flags() | Qt.ItemIsUserCheckable);
            item_d.setCheckState(Qt.Unchecked)
            self.list_disable.addItem(item_d)
            item_e = QListWidgetItem(name);
            item_e.setFlags(item_e.flags() | Qt.ItemIsUserCheckable);
            item_e.setCheckState(Qt.Unchecked)
            self.list_enable.addItem(item_e)

        btn_save = QPushButton("Save Mapping")
        btn_save.clicked.connect(self.save)
        layout.addWidget(btn_save)
        self.load_ui()

    def update_sat_label(self, val):
        self.lbl_sat_val.setText(f"{val}% (Reach Max Early)" if val < 100 else "100% (Normal)")

    def run_detection(self):
        dlg = UniversalInputDetector(self)
        if dlg.exec():
            res = dlg.detected_data
            self.data.update(res)
            self.lbl_input_desc.setText(res['description'])
            if "axis" in res['input_type']:
                self.grp_axis_opts.show();
                self.grp_btn_opts.hide()
            else:
                self.grp_axis_opts.hide();
                self.grp_btn_opts.show()

    def load_ui(self):
        if not self.data:
            self.grp_axis_opts.hide()
            return
        self.lbl_input_desc.setText(self.data.get('description', ''))
        self.txt_name.setText(self.data.get('name', ''))
        idx = self.combo_effect.findText(self.data.get('effect_name', ''))
        if idx >= 0: self.combo_effect.setCurrentIndex(idx)
        self.spin_hold.setValue(self.data.get('hold_time', 0))
        self.chk_turbo.setChecked(self.data.get('turbo_mode', False))
        self.spin_turbo_rate.setValue(self.data.get('turbo_rate', 100))
        self.slider_max.setValue(self.data.get('max_intensity', 100))
        self.slider_sat.setValue(self.data.get('saturation', 100))
        dir_idx = self.combo_axis_dir.findData(self.data.get('axis_direction', 'both'))
        if dir_idx >= 0: self.combo_axis_dir.setCurrentIndex(dir_idx)
        self.chk_start_disabled.setChecked(self.data.get('start_disabled', False))

        disabled_set = set(self.data.get('disable_others', []))
        enabled_set = set(self.data.get('enable_others', []))
        for i in range(self.list_disable.count()):
            item = self.list_disable.item(i)
            if item.text() in disabled_set: item.setCheckState(Qt.Checked)
        for i in range(self.list_enable.count()):
            item = self.list_enable.item(i)
            if item.text() in enabled_set: item.setCheckState(Qt.Checked)
        if "axis" in self.data.get('input_type', ''):
            self.grp_axis_opts.show();
            self.grp_btn_opts.hide()
        else:
            self.grp_axis_opts.hide();
            self.grp_btn_opts.show()

    def save(self):
        if not self.data.get('input_type'): return
        self.data['name'] = self.txt_name.text()
        self.data['effect_name'] = self.combo_effect.currentText()
        if self.grp_btn_opts.isVisible():
            self.data['hold_time'] = self.spin_hold.value()
            self.data['turbo_mode'] = self.chk_turbo.isChecked()
            self.data['turbo_rate'] = self.spin_turbo_rate.value()
        if self.grp_axis_opts.isVisible():
            self.data['max_intensity'] = self.slider_max.value()
            self.data['saturation'] = self.slider_sat.value()
            self.data['axis_direction'] = self.combo_axis_dir.currentData()
        self.data['start_disabled'] = self.chk_start_disabled.isChecked()
        dis_list = []
        for i in range(self.list_disable.count()):
            item = self.list_disable.item(i)
            if item.checkState() == Qt.Checked: dis_list.append(item.text())
        self.data['disable_others'] = dis_list
        en_list = []
        for i in range(self.list_enable.count()):
            item = self.list_enable.item(i)
            if item.checkState() == Qt.Checked: en_list.append(item.text())
        self.data['enable_others'] = en_list
        self.accept()