import pygame
import vgamepad as vg
import socket
import time


# --- CHAIR NETWORK CLIENT ---
class ChairClient:
    def __init__(self, ip, tcp_port, udp_port):
        self.ip = ip
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.tcp_socket = None
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.CMD_ON = b'\xa1'
        self.CMD_OFF = b'\xa2'
        self.CMD_PARK = b'\xa2\x01'
        self.CMD_LIGHTS_OFF = b'\xb2\x01\x01\x00\xff\x00'

        self.connect()

    def connect(self):
        if self.tcp_socket:
            try:
                self.tcp_socket.close()
            except:
                pass
        try:
            self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_socket.settimeout(1.0)
            self.tcp_socket.connect((self.ip, self.tcp_port))
            return True
        except:
            self.tcp_socket = None
            return False

    def is_connected(self):
        return self.tcp_socket is not None

    def send_tcp(self, command_name):
        if not self.tcp_socket:
            if not self.connect(): return

        payload = None
        if command_name == "on":
            payload = self.CMD_ON
        elif command_name == "off":
            payload = self.CMD_OFF
        elif command_name == "park":
            payload = self.CMD_PARK

        if not payload: return

        try:
            self.tcp_socket.send(payload)
        except (BrokenPipeError, ConnectionResetError, OSError):
            if self.connect():
                try:
                    self.tcp_socket.send(payload)
                except:
                    pass

    def send_udp(self, command_name):
        if command_name == "lights_off":
            try:
                self.udp_socket.sendto(self.CMD_LIGHTS_OFF, (self.ip, self.udp_port))
            except:
                pass

    def close(self):
        if self.tcp_socket:
            try:
                self.tcp_socket.close()
            except:
                pass


# --- INPUT MAPPER ---
class InputMapper:
    def __init__(self, config_data, virtual_pad):
        self.config = config_data
        if not pygame.get_init(): pygame.init()
        if not pygame.joystick.get_init(): pygame.joystick.init()
        self._refresh_joysticks()
        self.virtual_pad = virtual_pad
        c_set = self.config.get('chair_settings', {})
        self.chair = ChairClient(c_set.get('ip_address', '127.0.0.1'),
                                 c_set.get('tcp_port', 50020),
                                 c_set.get('udp_port', 50010))
        self.turbo_states = {}
        self.active_sequences = []
        self.stick_state = {"lx": 0, "ly": 0, "rx": 0, "ry": 0}

    def is_chair_connected(self):
        return self.chair.is_connected()

    def _refresh_joysticks(self):
        self.joysticks = {}
        for i in range(pygame.joystick.get_count()):
            joy = pygame.joystick.Joystick(i)
            if not joy.get_init(): joy.init()
            self.joysticks[i] = joy

    def cleanup(self):
        self.chair.close()

    def process_inputs(self):
        for event in pygame.event.get():
            if event.type == pygame.JOYBUTTONDOWN:
                self._handle_button(event.joy, event.button, True)
            elif event.type == pygame.JOYBUTTONUP:
                self._handle_button(event.joy, event.button, False)
            elif event.type == pygame.JOYAXISMOTION:
                self._handle_axis(event.joy, event.axis, event.value)

        self._process_turbos()
        self._process_sequences()
        self.virtual_pad.update()

    def _get_mapping(self, joy_idx, input_type, input_id):
        for m in self.config['mappings']:
            if (m.get('phys_device_index') == joy_idx and
                    m.get('phys_input_type') == input_type and
                    m.get('phys_input_id') == input_id):
                return m
        return None

    def _handle_button(self, joy_idx, btn_id, is_pressed):
        mapping = self._get_mapping(joy_idx, "button", btn_id)
        if not mapping: return

        action_type = mapping.get('action_type')
        target = mapping.get('target')
        options = mapping.get('options', {})

        if action_type == "sequence":
            if is_pressed: self._start_sequence(options)
            return

        if action_type == "chair_cmd" and is_pressed:
            if target == "connect":
                self.chair.connect()
            else:
                self.chair.send_tcp(target)
            return

        if action_type == "xbox_button":
            xbox_btn = getattr(vg.XUSB_BUTTON, f"XUSB_GAMEPAD_{target}", None)
            if not xbox_btn: return

            if options.get("mode") == "turbo":
                uid = f"{joy_idx}_b_{btn_id}"
                if is_pressed:
                    self.turbo_states[uid] = {
                        "btn": xbox_btn, "rate": options.get("rate", 0.1),
                        "next_tick": time.time(), "state": False
                    }
                else:
                    if uid in self.turbo_states: del self.turbo_states[uid]
                    self.virtual_pad.release_button(xbox_btn)
            else:
                if is_pressed:
                    self.virtual_pad.press_button(xbox_btn)
                else:
                    self.virtual_pad.release_button(xbox_btn)

    def _handle_axis(self, joy_idx, axis_id, value):
        mapping = self._get_mapping(joy_idx, "axis", axis_id)
        if not mapping: return

        # --- TUNING LOGIC (Clamp & Deadzone) ---
        tune = mapping.get('tuning', {})
        deadzone = tune.get('deadzone', 0.05)
        clamp = tune.get('clamp', 1.0)  # Default 100%

        # 1. Apply Deadzone (Center)
        if abs(value) < deadzone:
            value = 0.0

        # 2. Apply Clamp (Edge)
        # Prevents rolling over to -1 if sensor goes beyond 1.0 logic
        if value > clamp: value = clamp
        if value < -clamp: value = -clamp

        # 3. Normalize (Optional but makes it feel better)
        # If I clamp at 90%, I still want 90% input to equal 100% output
        # But for your specific glitch, simple clamping is safer.
        # We will strictly cap it.

        target = mapping.get('target')
        options = mapping.get('options', {})
        if options.get("invert"): value = -value

        if target.endswith("_trigger"):
            raw_0_1 = (value + 1) / 2
            trigger_val = max(0, min(255, int(raw_0_1 * 255)))
            if target == "left_trigger":
                self.virtual_pad.left_trigger(trigger_val)
            elif target == "right_trigger":
                self.virtual_pad.right_trigger(trigger_val)

        elif "stick" in target:
            # Scale -1.0..1.0 to -32768..32767
            # If we clamped to 0.99, max output will be slightly less than max possible,
            # which solves the rollover bug.
            stick_val = int(value * 32767)

            if target == "left_stick_x":
                self.stick_state['lx'] = stick_val
            elif target == "left_stick_y":
                self.stick_state['ly'] = -stick_val
            elif target == "right_stick_x":
                self.stick_state['rx'] = stick_val
            elif target == "right_stick_y":
                self.stick_state['ry'] = -stick_val

            self.virtual_pad.left_joystick(self.stick_state['lx'], self.stick_state['ly'])
            self.virtual_pad.right_joystick(self.stick_state['rx'], self.stick_state['ry'])

    def _process_turbos(self):
        now = time.time()
        for key, t in self.turbo_states.items():
            if now >= t['next_tick']:
                t['state'] = not t['state']
                t['next_tick'] = now + t['rate']
                if t['state']:
                    self.virtual_pad.press_button(t['btn'])
                else:
                    self.virtual_pad.release_button(t['btn'])

    def _start_sequence(self, options):
        t1 = getattr(vg.XUSB_BUTTON, f"XUSB_GAMEPAD_{options.get('t1')}", None)
        t2 = getattr(vg.XUSB_BUTTON, f"XUSB_GAMEPAD_{options.get('t2')}", None)
        if not t1 or not t2: return

        self.active_sequences.append({
            "t1": t1, "t2": t2,
            "on_time": int(options.get('on_ms', 100)) / 1000.0,
            "off_time": int(options.get('off_ms', 500)) / 1000.0,
            "repeats_left": int(options.get('repeats', 1)),
            "state": "IDLE", "next_tick": time.time()
        })

    def _process_sequences(self):
        now = time.time()
        for i in range(len(self.active_sequences) - 1, -1, -1):
            seq = self.active_sequences[i]
            if now >= seq['next_tick']:
                if seq['state'] == "IDLE":
                    self.virtual_pad.press_button(seq['t1'])
                    seq['state'] = "PRESS_1"
                    seq['next_tick'] = now + seq['on_time']
                elif seq['state'] == "PRESS_1":
                    self.virtual_pad.release_button(seq['t1'])
                    seq['state'] = "WAIT_1"
                    seq['next_tick'] = now + seq['off_time']
                elif seq['state'] == "WAIT_1":
                    self.virtual_pad.press_button(seq['t2'])
                    seq['state'] = "PRESS_2"
                    seq['next_tick'] = now + seq['on_time']
                elif seq['state'] == "PRESS_2":
                    self.virtual_pad.release_button(seq['t2'])
                    seq['repeats_left'] -= 1
                    if seq['repeats_left'] > 0:
                        seq['state'] = "WAIT_RESTART"
                        seq['next_tick'] = now + seq['off_time']
                    else:
                        self.active_sequences.pop(i)
                elif seq['state'] == "WAIT_RESTART":
                    seq['state'] = "IDLE"