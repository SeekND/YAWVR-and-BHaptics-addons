import pygame
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QComboBox, QSpinBox, QCheckBox, QPushButton,
                               QListWidget, QGroupBox, QFormLayout, QLineEdit,
                               QTabWidget, QWidget, QMessageBox, QSlider)
from PySide6.QtCore import Qt


class InputDetector(QDialog):
    """
    Detects input from Joysticks.
    """

    def __init__(self, target_device_index=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Listening...")
        self.resize(350, 120)
        self.target_index = target_device_index

        layout = QVBoxLayout(self)

        target_str = "ANY Device"
        if self.target_index is not None:
            try:
                if not pygame.get_init(): pygame.init()
                if not pygame.joystick.get_init(): pygame.joystick.init()
                if self.target_index < pygame.joystick.get_count():
                    name = pygame.joystick.Joystick(self.target_index).get_name()
                    target_str = f"{name} (Index: {self.target_index})"
                else:
                    target_str = f"Device Index {self.target_index}"
            except:
                target_str = f"Device Index {self.target_index}"

        layout.addWidget(QLabel(f"Listening for input on:\n{target_str}", alignment=Qt.AlignCenter))
        layout.addWidget(QLabel("(Press a button or move an axis)", alignment=Qt.AlignCenter))

        self.lbl_status = QLabel("Waiting...", alignment=Qt.AlignCenter)
        self.lbl_status.setStyleSheet("color: blue; font-weight: bold;")
        layout.addWidget(self.lbl_status)

        self.detected_input = None

        if not pygame.get_init(): pygame.init()
        if not pygame.joystick.get_init(): pygame.joystick.init()

        self.joysticks = []
        self.instance_id_to_index = {}

        for i in range(pygame.joystick.get_count()):
            joy = pygame.joystick.Joystick(i)
            if not joy.get_init(): joy.init()
            self.joysticks.append(joy)
            self.instance_id_to_index[joy.get_instance_id()] = i

        self.startTimer(20)

    def timerEvent(self, event):
        for e in pygame.event.get():
            if e.type not in (pygame.JOYBUTTONDOWN, pygame.JOYAXISMOTION):
                continue

            event_instance_id = getattr(e, 'instance_id', getattr(e, 'joy', -1))
            device_index = self.instance_id_to_index.get(event_instance_id, -1)

            if device_index == -1: continue
            if self.target_index is not None and device_index != self.target_index: continue

            if e.type == pygame.JOYBUTTONDOWN:
                self.detected_input = {
                    "phys_device_index": device_index,
                    "phys_device_name": self.joysticks[device_index].get_name(),
                    "phys_input_type": "button",
                    "phys_input_id": e.button
                }
                self.accept()
            elif e.type == pygame.JOYAXISMOTION:
                if abs(e.value) > 0.5:
                    self.detected_input = {
                        "phys_device_index": device_index,
                        "phys_device_name": self.joysticks[device_index].get_name(),
                        "phys_input_type": "axis",
                        "phys_input_id": e.axis
                    }
                    self.accept()


class MappingDialog(QDialog):
    """
    Revised Editor with Deadzone and Clamp Sliders
    """

    def __init__(self, mapping_data=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Mapping")
        self.resize(550, 700)
        self.data = mapping_data or {}

        if not pygame.get_init(): pygame.init()
        if not pygame.joystick.get_init(): pygame.joystick.init()

        layout = QVBoxLayout(self)

        # --- DESCRIPTION FIELD ---
        grp_desc = QGroupBox("Description")
        lay_desc = QVBoxLayout()
        self.txt_desc = QLineEdit()
        self.txt_desc.setPlaceholderText("e.g. Left Gun Trigger, Eject Button")
        lay_desc.addWidget(self.txt_desc)
        grp_desc.setLayout(lay_desc)
        layout.addWidget(grp_desc)

        # --- INPUT DEFINITION ---
        grp_input = QGroupBox("Physical Input Source")
        form_input = QFormLayout()

        self.combo_device = QComboBox()
        self.combo_device.addItem("Any / Auto-Detect", -1)

        self.devices = []
        for i in range(pygame.joystick.get_count()):
            j = pygame.joystick.Joystick(i)
            if not j.get_init(): j.init()
            name = f"{i}: {j.get_name()}"
            self.devices.append(
                {"index": i, "name": j.get_name(), "buttons": j.get_numbuttons(), "axes": j.get_numaxes()})
            self.combo_device.addItem(name, i)

        self.combo_device.currentIndexChanged.connect(self.update_input_limits)
        form_input.addRow("Select Device:", self.combo_device)

        self.btn_detect = QPushButton("Auto-Detect Input")
        self.btn_detect.clicked.connect(self.run_detection)
        form_input.addRow(self.btn_detect)

        self.combo_type = QComboBox()
        self.combo_type.addItems(["button", "axis"])
        self.combo_type.currentTextChanged.connect(self.update_input_limits)

        self.spin_id = QSpinBox()
        self.lbl_id_max = QLabel("(Select a device to see limits)")

        row_id = QHBoxLayout()
        row_id.addWidget(self.spin_id)
        row_id.addWidget(self.lbl_id_max)

        form_input.addRow("Input Type:", self.combo_type)
        form_input.addRow("Input ID:", row_id)
        grp_input.setLayout(form_input)
        layout.addWidget(grp_input)

        # --- AXIS TUNING (NEW) ---
        # Only relevant for axes, but we show it always for simplicity
        grp_tune = QGroupBox("Axis Tuning")
        form_tune = QFormLayout()

        # Deadzone Slider (0 to 50%)
        self.slider_deadzone = QSlider(Qt.Horizontal)
        self.slider_deadzone.setRange(0, 50)
        self.slider_deadzone.setValue(5)  # Default 5%
        self.lbl_deadzone = QLabel("5 %")
        self.slider_deadzone.valueChanged.connect(lambda v: self.lbl_deadzone.setText(f"{v} %"))

        row_dz = QHBoxLayout()
        row_dz.addWidget(self.slider_deadzone)
        row_dz.addWidget(self.lbl_deadzone)
        form_tune.addRow("Center Deadzone:", row_dz)

        # Clamp Slider (50% to 100%)
        self.slider_clamp = QSlider(Qt.Horizontal)
        self.slider_clamp.setRange(50, 100)
        self.slider_clamp.setValue(100)  # Default 100%
        self.lbl_clamp = QLabel("100 %")
        self.slider_clamp.valueChanged.connect(lambda v: self.lbl_clamp.setText(f"{v} %"))

        row_cl = QHBoxLayout()
        row_cl.addWidget(self.slider_clamp)
        row_cl.addWidget(self.lbl_clamp)
        form_tune.addRow("Edge Clamp (Max):", row_cl)

        grp_tune.setLayout(form_tune)
        layout.addWidget(grp_tune)

        # --- ACTION TYPE ---
        self.tabs = QTabWidget()

        self.tab_direct = QWidget()
        form_direct = QFormLayout(self.tab_direct)

        self.combo_action = QComboBox()
        self.combo_action.addItems(["xbox_button", "xbox_axis", "chair_cmd"])
        self.combo_action.currentTextChanged.connect(self.refresh_targets)
        self.combo_target = QComboBox()

        self.chk_invert = QCheckBox("Invert Axis")
        self.chk_halfmast = QCheckBox("Half-Mast (Axis -> Trigger)")
        self.chk_turbo = QCheckBox("Hold Turbo (Repeat while held)")
        self.spin_turbo_rate = QSpinBox()
        self.spin_turbo_rate.setRange(10, 2000)
        self.spin_turbo_rate.setValue(100)
        self.spin_turbo_rate.setSuffix(" ms")

        form_direct.addRow("Action:", self.combo_action)
        form_direct.addRow("Target:", self.combo_target)
        form_direct.addRow(self.chk_invert)
        form_direct.addRow(self.chk_halfmast)
        form_direct.addRow(self.chk_turbo)
        form_direct.addRow("Turbo Rate:", self.spin_turbo_rate)

        self.tabs.addTab(self.tab_direct, "Direct Mapping")

        self.tab_seq = QWidget()
        form_seq = QFormLayout(self.tab_seq)

        self.lbl_seq_info = QLabel("Tap button to cycle: Target 1 -> Pause -> Target 2")
        self.seq_target_1 = QComboBox()
        self.seq_target_2 = QComboBox()
        xbox_btns = ["A", "B", "X", "Y", "START", "BACK", "LB", "RB"]
        self.seq_target_1.addItems(xbox_btns)
        self.seq_target_2.addItems(xbox_btns)

        self.spin_seq_on = QSpinBox()
        self.spin_seq_on.setValue(100)
        self.spin_seq_on.setSuffix(" ms")
        self.spin_seq_off = QSpinBox()
        self.spin_seq_off.setValue(500)
        self.spin_seq_off.setSuffix(" ms")
        self.spin_repeats = QSpinBox()
        self.spin_repeats.setRange(1, 50)
        self.spin_repeats.setValue(1)
        self.spin_repeats.setPrefix("Repeat: ")

        form_seq.addRow(self.lbl_seq_info)
        form_seq.addRow("Button 1:", self.seq_target_1)
        form_seq.addRow("Button 2:", self.seq_target_2)
        form_seq.addRow("Press Duration:", self.spin_seq_on)
        form_seq.addRow("Pause Duration:", self.spin_seq_off)
        form_seq.addRow(self.spin_repeats)

        self.tabs.addTab(self.tab_seq, "Pulse Sequence")
        layout.addWidget(self.tabs)

        btns = QHBoxLayout()
        btn_save = QPushButton("Save Mapping")
        btn_save.clicked.connect(self.save_mapping)
        btns.addWidget(btn_save)
        layout.addLayout(btns)

        self.refresh_targets()
        self.update_input_limits()
        self.load_ui_from_data()

    def update_input_limits(self):
        idx = self.combo_device.currentData()
        input_type = self.combo_type.currentText()

        if idx == -1:
            self.lbl_id_max.setText("(Generic Device)")
            self.spin_id.setMaximum(99)
            return

        dev_stats = next((d for d in self.devices if d['index'] == idx), None)
        if dev_stats:
            if input_type == "button":
                count = dev_stats['buttons']
                self.lbl_id_max.setText(f"(0 to {count - 1})")
                self.spin_id.setMaximum(max(0, count - 1))
            else:
                count = dev_stats['axes']
                self.lbl_id_max.setText(f"(0 to {count - 1})")
                self.spin_id.setMaximum(max(0, count - 1))

    def run_detection(self):
        target_idx = self.combo_device.currentData()
        if target_idx == -1: target_idx = None

        detector = InputDetector(target_device_index=target_idx, parent=self)
        if detector.exec():
            res = detector.detected_input
            combo_idx = self.combo_device.findData(res['phys_device_index'])
            if combo_idx >= 0: self.combo_device.setCurrentIndex(combo_idx)
            self.combo_type.setCurrentText(res['phys_input_type'])
            self.spin_id.setValue(res['phys_input_id'])

    def refresh_targets(self):
        action = self.combo_action.currentText()
        self.combo_target.clear()
        if action == "xbox_button":
            self.combo_target.addItems(
                ["A", "B", "X", "Y", "START", "BACK", "LEFT_SHOULDER", "RIGHT_SHOULDER", "DPAD_UP", "DPAD_DOWN"])
        elif action == "xbox_axis":
            self.combo_target.addItems(
                ["left_stick_x", "left_stick_y", "right_stick_x", "right_stick_y", "left_trigger", "right_trigger"])
        elif action == "chair_cmd":
            self.combo_target.addItems(["connect", "on", "off", "park"])

    def load_ui_from_data(self):
        if not self.data: return

        self.txt_desc.setText(self.data.get('comment', ''))

        dev_idx = self.data.get('phys_device_index', -1)
        combo_idx = self.combo_device.findData(dev_idx)
        if combo_idx >= 0:
            self.combo_device.setCurrentIndex(combo_idx)
        else:
            self.combo_device.setCurrentIndex(0)

        self.combo_type.setCurrentText(self.data.get('phys_input_type', 'button'))
        self.spin_id.setValue(self.data.get('phys_input_id', 0))

        # Load Tuning
        tune = self.data.get('tuning', {})
        self.slider_deadzone.setValue(int(tune.get('deadzone', 0.05) * 100))
        self.slider_clamp.setValue(int(tune.get('clamp', 1.0) * 100))

        if self.data.get('action_type') == 'sequence':
            self.tabs.setCurrentIndex(1)
            opts = self.data.get('options', {})
            self.seq_target_1.setCurrentText(opts.get('t1', 'A'))
            self.seq_target_2.setCurrentText(opts.get('t2', 'B'))
            self.spin_seq_on.setValue(int(opts.get('on_ms', 100)))
            self.spin_seq_off.setValue(int(opts.get('off_ms', 500)))
            self.spin_repeats.setValue(int(opts.get('repeats', 1)))
        else:
            self.tabs.setCurrentIndex(0)
            self.combo_action.setCurrentText(self.data.get('action_type', 'xbox_button'))
            self.combo_target.setCurrentText(self.data.get('target', ''))
            opts = self.data.get('options', {})
            self.chk_invert.setChecked(opts.get('invert', False))
            self.chk_halfmast.setChecked(opts.get('half_mast', False))
            self.chk_turbo.setChecked(opts.get('mode') == 'turbo')
            self.spin_turbo_rate.setValue(int(opts.get('rate', 0.1) * 1000))

    def save_mapping(self):
        idx = self.combo_device.currentData()
        if idx == -1: idx = 0

        dev_name = self.combo_device.currentText()
        if ":" in dev_name: dev_name = dev_name.split(": ", 1)[1]

        base_data = {
            "phys_device_index": idx,
            "phys_device_name": dev_name,
            "phys_input_type": self.combo_type.currentText(),
            "phys_input_id": self.spin_id.value(),
            # Save Tuning Data
            "tuning": {
                "deadzone": self.slider_deadzone.value() / 100.0,
                "clamp": self.slider_clamp.value() / 100.0
            }
        }

        desc = self.txt_desc.text().strip()
        if not desc:
            if self.tabs.currentIndex() == 0:
                desc = f"{self.combo_target.currentText()}"
            else:
                desc = "Sequence"
        base_data['comment'] = desc

        if self.tabs.currentIndex() == 0:
            base_data['action_type'] = self.combo_action.currentText()
            base_data['target'] = self.combo_target.currentText()
            opts = {}
            if self.chk_invert.isChecked(): opts['invert'] = True
            if self.chk_halfmast.isChecked(): opts['half_mast'] = True
            if self.chk_turbo.isChecked():
                opts['mode'] = 'turbo'
                opts['rate'] = self.spin_turbo_rate.value() / 1000.0
            base_data['options'] = opts
        else:
            base_data['action_type'] = 'sequence'
            base_data['target'] = 'macro'
            base_data['options'] = {
                't1': self.seq_target_1.currentText(),
                't2': self.seq_target_2.currentText(),
                'on_ms': self.spin_seq_on.value(),
                'off_ms': self.spin_seq_off.value(),
                'repeats': self.spin_repeats.value()
            }

        self.data = base_data
        self.accept()