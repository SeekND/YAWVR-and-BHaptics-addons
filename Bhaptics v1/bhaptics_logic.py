import asyncio
import time
import pygame
import win32api
import bhaptics_python

# --- CONSTANTS ---
KEY_MAP = {
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
    "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
    "A": 0x41, "B": 0x42, "C": 0x43, "D": 0x44, "E": 0x45,
    "F": 0x46, "G": 0x47, "H": 0x48, "I": 0x49, "J": 0x4A,
    "K": 0x4B, "L": 0x4C, "M": 0x4D, "N": 0x4E, "O": 0x4F,
    "P": 0x50, "Q": 0x51, "R": 0x52, "S": 0x53, "T": 0x54,
    "U": 0x55, "V": 0x56, "W": 0x57, "X": 0x58, "Y": 0x59,
    "Z": 0x5A,
    "L_MOUSE": 0x01, "R_MOUSE": 0x02, "M_MOUSE": 0x04,
    "SPACE": 0x20, "L_SHIFT": 0xA0, "L_CTRL": 0xA2
}


class HapticLibrary:
    def __init__(self, config_data):
        self.config = config_data
        self.duration = 100
        self.interval = 0.05
        self.TACTSUIT_POSITION = 0

    def _create_values(self, active_indices, intensity):
        # Create a 40-int array (Standard TactSuit X40)
        # 0-19: Front, 20-39: Back
        values = [0] * 40
        valid_intensity = max(0, min(100, int(intensity)))
        for idx in active_indices:
            if 0 <= idx < 40:
                values[idx] = valid_intensity
        return values

    async def play_effect(self, effect_name, intensity=100):
        # 1. Try Custom Effects first (so you can override)
        custom_fx = next((fx for fx in self.config.get("custom_effects", []) if fx["name"] == effect_name), None)

        if custom_fx:
            await self._play_custom_effect(custom_fx, intensity)
            return

        # 2. Try Hardcoded Effects
        method = getattr(self, f"effect_{effect_name}", None)
        if method:
            await method(intensity)
        else:
            print(f"Unknown effect: {effect_name}")

    async def _play_custom_effect(self, fx_data, master_intensity):
        if fx_data['type'] == 'static':
            # Single Frame
            final_intensity = int(fx_data['intensity'] * (master_intensity / 100.0))
            values = self._create_values(fx_data['motors'], final_intensity)
            await bhaptics_python.play_dot(self.TACTSUIT_POSITION, fx_data['duration'], values)

        elif fx_data['type'] == 'sequence':
            # Timeline
            for frame in fx_data['frames']:
                # Calc intensity relative to master
                frame_int = int(frame['intensity'] * (master_intensity / 100.0))
                values = self._create_values(frame['motors'], frame_int)

                await bhaptics_python.play_dot(self.TACTSUIT_POSITION, frame['duration'], values)

                if frame.get('delay', 0) > 0:
                    await asyncio.sleep(frame['delay'] / 1000.0)

    # --- EFFECT DEFINITIONS (YOUR RENAMED VERSIONS) ---
    async def effect_front_rear_center(self, intensity):
        sequence = [18, 17, 14, 13, 10, 9, 6, 5, 2, 1]
        for idx in sequence:
            values = self._create_values([idx], intensity)
            await bhaptics_python.play_dot(self.TACTSUIT_POSITION, 80, values)
            await asyncio.sleep(0.02)

    async def effect_front_outter_right_chest(self, intensity):
        values = self._create_values([2, 3], intensity)
        await bhaptics_python.play_dot(self.TACTSUIT_POSITION, self.duration, values)

    async def effect_front_rear_lower_edges(self, intensity):
        dots = [19, 18, 16, 15, 12]
        values = self._create_values(dots, intensity)
        await bhaptics_python.play_dot(self.TACTSUIT_POSITION, self.duration, values)

    async def effect_front_inner_right_chest(self, intensity):
        values = self._create_values([6, 7, 10, 11], intensity)
        await bhaptics_python.play_dot(self.TACTSUIT_POSITION, self.duration, values)


class InputMonitor:
    def __init__(self, config_data):
        self.config = config_data
        self.running = False
        # PASS CONFIG TO LIBRARY
        self.library = HapticLibrary(config_data)

        # State Management
        self.disabled_binds = set()
        self.current_key_states = {}
        self.active_turbos = set()
        self.axis_states = {}

        # NEW: Initialize Disabled Binds
        for m in self.config.get('mappings', []):
            if m.get('start_disabled', False):
                self.disabled_binds.add(m.get('name'))

        print(f"Disabled on Start: {list(self.disabled_binds)}")

        # Hardware Init
        pygame.init()
        pygame.joystick.init()
        self._refresh_joysticks()

    def _refresh_joysticks(self):
        self.joysticks = {}
        for i in range(pygame.joystick.get_count()):
            j = pygame.joystick.Joystick(i)
            j.init()
            self.joysticks[i] = j

    async def run_loop(self):
        try:
            await bhaptics_python.registry_and_initialize("UniversalBridge", "key", "UniversalBridge")
        except:
            pass

        self.running = True
        print("Engine Started.")

        while self.running:
            await self._process_inputs()
            await self._update_continuous_haptics()
            await asyncio.sleep(0.02)

        await bhaptics_python.stop_all()

    async def _process_inputs(self):
        for event in pygame.event.get():
            if event.type == pygame.JOYBUTTONDOWN:
                await self._handle_input_event("joy_button", event.joy, event.button, True)
            elif event.type == pygame.JOYBUTTONUP:
                await self._handle_input_event("joy_button", event.joy, event.button, False)
            elif event.type == pygame.JOYAXISMOTION:
                self.axis_states[(event.joy, event.axis)] = event.value

        for m in self.config.get('mappings', []):
            itype = m.get('input_type')
            if itype not in ['keyboard', 'mouse']: continue

            key_name = m.get('input_id')
            vk_code = KEY_MAP.get(key_name)

            if vk_code:
                is_pressed = (win32api.GetAsyncKeyState(vk_code) & 0x8000) != 0
                uid = f"{itype}_{key_name}"
                was_pressed = self.current_key_states.get(uid, False)

                if is_pressed != was_pressed:
                    self.current_key_states[uid] = is_pressed
                    await self._handle_input_event(itype, 0, key_name, is_pressed)

    async def _update_continuous_haptics(self):
        for m in self.config.get('mappings', []):
            if m.get('input_type') == 'joy_axis':
                dev_idx = m.get('device_index')
                axis_id = m.get('input_id')
                current_val = self.axis_states.get((dev_idx, axis_id), 0.0)

                if m.get('name') in self.disabled_binds: continue

                await self._handle_axis_effect(m, current_val)

    async def _handle_input_event(self, input_type, device_idx, input_id, is_pressed):
        for m in self.config.get('mappings', []):
            if m.get('input_type') != input_type: continue
            if m.get('input_id') != input_id: continue
            if input_type == "joy_button" and m.get('device_index') != device_idx: continue

            if m.get('name') in self.disabled_binds: continue

            if is_pressed:
                hold_time = m.get('hold_time', 0)
                if hold_time > 0:
                    asyncio.create_task(self._run_hold_timer(m, input_type, device_idx, input_id))
                else:
                    if m.get('turbo_mode'):
                        asyncio.create_task(self._run_turbo(m, input_type, device_idx, input_id))
                    else:
                        await self._fire_action(m)

    async def _run_hold_timer(self, mapping, i_type, d_idx, i_id):
        required_time = mapping.get('hold_time', 0) / 1000.0
        start_time = time.time()

        while time.time() - start_time < required_time:
            if not self._is_still_pressed(i_type, d_idx, i_id):
                return
            await asyncio.sleep(0.05)

        if mapping.get('turbo_mode'):
            await self._run_turbo(mapping, i_type, d_idx, i_id)
        else:
            await self._fire_action(mapping)

    async def _run_turbo(self, mapping, i_type, d_idx, i_id):
        uid = f"{mapping['name']}_{i_type}_{i_id}"
        if uid in self.active_turbos: return
        self.active_turbos.add(uid)

        rate = mapping.get('turbo_rate', 100) / 1000.0

        while self._is_still_pressed(i_type, d_idx, i_id):
            if mapping.get('name') not in self.disabled_binds:
                await self._fire_action(mapping)
            await asyncio.sleep(rate)

        self.active_turbos.remove(uid)

    def _is_still_pressed(self, i_type, d_idx, i_id):
        if i_type == "joy_button":
            if d_idx in self.joysticks:
                try:
                    return self.joysticks[d_idx].get_button(i_id)
                except:
                    return False
        elif i_type in ["keyboard", "mouse"]:
            key_name = i_id
            vk_code = KEY_MAP.get(key_name)
            return (win32api.GetAsyncKeyState(vk_code) & 0x8000) != 0
        return False

    async def _fire_action(self, mapping):
        to_disable = mapping.get('disable_others', [])
        to_enable = mapping.get('enable_others', [])

        for name in to_disable:
            self.disabled_binds.add(name)
        for name in to_enable:
            if name in self.disabled_binds:
                self.disabled_binds.remove(name)

        if to_disable or to_enable:
            print(f"[{mapping.get('name')}] Triggered. Disabled: {list(self.disabled_binds)}")

        await self.library.play_effect(mapping.get('effect_name'))

    async def _handle_axis_effect(self, mapping, value):
        effect_name = mapping.get('effect_name')

        INPUT_CEILING = 0.9
        if value > INPUT_CEILING: value = INPUT_CEILING
        if value < -INPUT_CEILING: value = -INPUT_CEILING

        direction_mode = mapping.get('axis_direction', 'both')
        calc_val = 0.0

        if direction_mode == 'positive':
            if value > 0: calc_val = value
        elif direction_mode == 'negative':
            if value < 0: calc_val = abs(value)
        else:
            calc_val = abs(value)

        sat_limit = mapping.get('saturation', 100) / 100.0
        if sat_limit < 0.01: sat_limit = 0.01

        saturated_val = calc_val / sat_limit
        if saturated_val > 1.0: saturated_val = 1.0

        max_cap = mapping.get('max_intensity', 100) / 100.0
        final_val = saturated_val * max_cap

        if final_val < 0.05: return

        intensity = int(final_val * 100)
        await self.library.play_effect(effect_name, intensity)

    def stop(self):
        self.running = False