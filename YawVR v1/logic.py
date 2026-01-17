import pygame
import vgamepad as vg
import socket
import time
import math


class ChairClient:
    def __init__(self, ip, tcp_port, udp_port):
        self.ip = ip
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.tcp_socket = None
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.CMD_ON, self.CMD_OFF, self.CMD_PARK = b'\xa1', b'\xa2', b'\xa2\x01'
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
        except:
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
        self.active_rumbles = []
        self.pending_actions = []  # New Delayed Action Queue
        self.axis_state = {
            "left_stick_x": 0.0, "left_stick_y": 0.0,
            "right_stick_x": 0.0, "right_stick_y": 0.0,
            "left_trigger": -1.0, "right_trigger": -1.0
        }

    def is_chair_connected(self):
        return self.chair.is_connected()

    def cleanup(self):
        self.chair.close()

    def _refresh_joysticks(self):
        self.joysticks = {}
        for i in range(pygame.joystick.get_count()):
            joy = pygame.joystick.Joystick(i)
            if not joy.get_init(): joy.init()
            self.joysticks[i] = joy

    def process_inputs(self):
        # 1. READ PHYSICAL INPUTS
        for event in pygame.event.get():
            if event.type == pygame.JOYBUTTONDOWN:
                self._handle_button(event.joy, event.button, True)
            elif event.type == pygame.JOYBUTTONUP:
                self._handle_button(event.joy, event.button, False)
            elif event.type == pygame.JOYAXISMOTION:
                self._handle_axis(event.joy, event.axis, event.value)

        # 2. PROCESS DELAYED QUEUE
        self._process_pending_actions()

        # 3. RUN LOGIC
        self._process_turbos()
        self._process_sequences()

        # 4. APPLY RUMBLE & SEND
        self._update_vpad_with_rumble()

    def _get_mappings(self, joy_idx, input_type, input_id):
        matches = []
        for m in self.config['mappings']:
            if (m.get('phys_device_index') == joy_idx and
                    m.get('phys_input_type') == input_type and
                    m.get('phys_input_id') == input_id):
                matches.append(m)

        # SORT BY DELAY: Ensures actions execute in the order you visualized
        matches.sort(key=lambda x: int(x.get('start_delay', 0)))
        return matches

    def _handle_button(self, joy_idx, btn_id, is_pressed):
        mappings = self._get_mappings(joy_idx, "button", btn_id)
        if not mappings: return

        for mapping in mappings:
            delay = mapping.get('start_delay', 0)

            # If there is a delay and we are pressing, schedule it.
            # (Note: Releases are usually immediate, or we handle them specially)
            if is_pressed and delay > 0:
                self.pending_actions.append({
                    "time": time.time() + (delay / 1000.0),
                    "mapping": mapping,
                    "is_pressed": True,
                    "joy_idx": joy_idx,
                    "btn_id": btn_id
                })
            else:
                # Immediate execution (Delay 0 OR Button Release)
                self._execute_mapping(mapping, joy_idx, btn_id, is_pressed)

    def _handle_axis(self, joy_idx, axis_id, value):
        # Axis inputs are continuous, so delays are rare/weird here.
        # We usually execute immediately.
        mappings = self._get_mappings(joy_idx, "axis", axis_id)
        for mapping in mappings:
            self._execute_mapping(mapping, joy_idx, axis_id, value, is_axis=True)

    def _process_pending_actions(self):
        now = time.time()
        # Filter list: keep only future events, execute expired ones
        remaining = []
        for item in self.pending_actions:
            if now >= item['time']:
                self._execute_mapping(item['mapping'], item['joy_idx'], item['btn_id'], item['is_pressed'])
            else:
                remaining.append(item)
        self.pending_actions = remaining

    def _execute_mapping(self, mapping, joy_idx, input_id, value_or_pressed, is_axis=False):
        action = mapping.get('action_type')
        target = mapping.get('target')
        opts = mapping.get('options', {})

        # --- AXIS HANDLING ---
        if is_axis:
            value = value_or_pressed
            tune = mapping.get('tuning', {})
            deadzone = tune.get('deadzone', 0.05)
            clamp = tune.get('clamp', 1.0)

            if abs(value) < deadzone: value = 0.0
            if value > clamp: value = clamp
            if value < -clamp: value = -clamp
            if opts.get("invert"): value = -value

            if target in self.axis_state:
                self.axis_state[target] = value
            return

        # --- BUTTON HANDLING ---
        is_pressed = value_or_pressed

        if action == "rumble":
            if is_pressed:
                self._start_rumble(target, opts)
            elif opts.get('duration', 0) == 0:
                self._stop_rumble(target)
            return

        if action == "sequence":
            if is_pressed: self._start_sequence(opts)
            return

        if action == "chair_cmd" and is_pressed:
            if target == "connect":
                self.chair.connect()
            else:
                self.chair.send_tcp(target)
            return

        if action == "xbox_button":
            xbox_btn = getattr(vg.XUSB_BUTTON, f"XUSB_GAMEPAD_{target}", None)
            if not xbox_btn: return

            if opts.get("mode") == "turbo":
                uid = f"{joy_idx}_b_{input_id}"
                if is_pressed:
                    self.turbo_states[uid] = {"btn": xbox_btn, "rate": opts.get("rate", 0.1), "next_tick": time.time(),
                                              "state": False}
                else:
                    if uid in self.turbo_states: del self.turbo_states[uid]
                    self.virtual_pad.release_button(xbox_btn)
            else:
                if is_pressed:
                    self.virtual_pad.press_button(xbox_btn)
                else:
                    self.virtual_pad.release_button(xbox_btn)

    def _start_rumble(self, target, opts):
        duration = opts.get('duration', 0)
        end_time = time.time() + (duration / 1000.0) if duration > 0 else None
        rumble = {
            "target": target,
            "intensity": opts.get('intensity', 0.1),
            "speed": opts.get('speed', 10),
            "start_time": time.time(),
            "end_time": end_time
        }
        self.active_rumbles.append(rumble)

    def _stop_rumble(self, target):
        self.active_rumbles = [r for r in self.active_rumbles if not (r['target'] == target and r['end_time'] is None)]

    def _update_vpad_with_rumble(self):
        now = time.time()
        final_state = self.axis_state.copy()

        for i in range(len(self.active_rumbles) - 1, -1, -1):
            r = self.active_rumbles[i]
            if r['end_time'] and now > r['end_time']:
                self.active_rumbles.pop(i)
                continue
            elapsed = now - r['start_time']
            wave = r['intensity'] * math.sin(elapsed * r['speed'] * 2 * math.pi)
            if r['target'] in final_state:
                final_state[r['target']] += wave

        def process_stick(val):
            return int(max(-1.0, min(1.0, val)) * 32767)

        def process_trigger(val):
            return int(max(0.0, min(1.0, (val + 1) / 2)) * 255)

        self.virtual_pad.left_joystick(process_stick(final_state['left_stick_x']),
                                       process_stick(final_state['left_stick_y']))
        self.virtual_pad.right_joystick(process_stick(final_state['right_stick_x']),
                                        process_stick(final_state['right_stick_y']))
        self.virtual_pad.left_trigger(process_trigger(final_state['left_trigger']))
        self.virtual_pad.right_trigger(process_trigger(final_state['right_trigger']))
        self.virtual_pad.update()

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