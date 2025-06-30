# main.py

import os
import time
import json
import uuid
import requests
import numpy as np
from threading import Thread, Lock
from functools import partial

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.scrollview import ScrollView
from kivy.clock import Clock
from kivy.utils import platform
from kivy.logger import Logger
from dotenv import dotenv_values
from kivy.uix.settings import SettingsWithSidebar
from kivy.uix.modalview import ModalView
from kivy.uix.recycleview import RecycleView
from kivy.uix.recycleview.views import RecycleDataViewBehavior
from kivy.properties import BooleanProperty
from kivy.uix.recycleboxlayout import RecycleBoxLayout
from kivy.uix.behaviors import FocusBehavior
from kivy.uix.recycleview.layout import LayoutSelectionBehavior

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
dotenv_path = os.path.join(APP_ROOT, '.env')
config = dotenv_values(dotenv_path)
PICOVOICE_ACCESS_KEY = config.get("PICOVOICE_ACCESS_KEY")

try: import pvporcupine
except ImportError: pvporcupine = None
try:
    from vosk import Model, KaldiRecognizer, SetLogLevel
    SetLogLevel(-1)
except ImportError: Model, KaldiRecognizer = None, None
try: import piper
except ImportError: piper = None
try: import sounddevice as sd
except Exception: sd = None

if platform == 'android':
    try:
        from android.permissions import request_permissions, check_permission, Permission
        from jnius import autoclass
    except ImportError: autoclass, Permission = None, None

APP_ROOT = os.path.dirname(os.path.abspath(__file__)); ASSETS_DIR = os.path.join(APP_ROOT, 'assets')
VoskModelEN, VoskModelHI, PiperVoiceEN, PiperVoiceHI = None, None, None, None
PORCUPINE_MODEL_PATH = os.path.join(ASSETS_DIR, 'porcupine', 'porcupine_params.pv')
PORCUPINE_KEYWORD_PATHS = [os.path.join(ASSETS_DIR, 'porcupine', 'hey-bro_en_android_v3_0_0.ppn')]
VOSK_MODEL_PATH_EN = os.path.join(ASSETS_DIR, 'vosk', 'vosk-model-small-en-us-0.15'); VOSK_MODEL_PATH_HI = os.path.join(ASSETS_DIR, 'vosk', 'vosk-model-small-hi-0.22')
PIPER_VOICE_EN_ONNX = os.path.join(ASSETS_DIR, 'piper', 'en_US-lessac-medium.onnx'); PIPER_VOICE_EN_JSON = os.path.join(ASSETS_DIR, 'piper', 'en_US-lessac-medium.onnx.json')
PIPER_VOICE_HI_ONNX = os.path.join(ASSETS_DIR, 'piper', 'hi_IN-cmu-medium.onnx'); PIPER_VOICE_HI_JSON = os.path.join(ASSETS_DIR, 'piper', 'hi_IN-cmu-medium.onnx.json')
SAMPLE_RATE = 16000; audio_lock = Lock()


class SelectableLabel(RecycleDataViewBehavior, Label):
    index, selected, selectable = None, BooleanProperty(False), BooleanProperty(True)
    def refresh_view_attrs(self, rv, index, data): self.index = index; return super().refresh_view_attrs(rv, index, data)
    def on_touch_down(self, touch):
        if super().on_touch_down(touch): return True
        if self.collide_point(*touch.pos) and self.selectable: return self.parent.select_with_touch(self.index, touch)
    def apply_selection(self, rv, index, is_selected):
        self.selected = is_selected
        if is_selected:
            rv.selected_app_data = rv.data[index]
            self.canvas.before.clear()
            with self.canvas.before: from kivy.graphics import Color, Rectangle; Color(0.0, 0.6, 0.8, 0.4); Rectangle(pos=self.pos, size=self.size)
        else: self.canvas.before.clear()
class SelectableRecycleBoxLayout(FocusBehavior, LayoutSelectionBehavior, RecycleBoxLayout): selected_app_data = None
class AppListView(ModalView):
    def __init__(self, app_list, callback, **kwargs):
        super().__init__(size_hint=(0.9, 0.9), **kwargs); self.callback = callback
        layout = BoxLayout(orientation='vertical', padding=10); title = Label(text="Select the correct app", size_hint_y=None, height=44, font_size='18sp')
        self.rv = RecycleView(key_viewclass='viewclass', key_size='height'); self.rv.data = [{'text': f"{app['name']} ({app['package']})", 'app_data': app, 'height': 44} for app in app_list]
        rv_layout = SelectableRecycleBoxLayout(default_size=(None, 44), default_size_hint=(1, None), size_hint_y=None, orientation='vertical', multiselect=False, touch_multiselect=False)
        rv_layout.bind(minimum_height=rv_layout.setter('height')); self.rv.add_widget(rv_layout); self.rv.viewclass = SelectableLabel
        confirm_button = Button(text="Confirm Selection", size_hint_y=None, height=50); confirm_button.bind(on_press=self.confirm)
        layout.add_widget(title); layout.add_widget(self.rv); layout.add_widget(confirm_button)
        self.add_widget(layout)
    def confirm(self, instance):
        if self.rv.layout_manager.selected_app_data: self.dismiss(); self.callback(self.rv.layout_manager.selected_app_data['app_data'])
class WakeWordListener(Thread):
    def __init__(self, access_key, keyword_paths, model_path, callback, sensitivity=0.5):
        super().__init__(daemon=True); self.callback = callback; self._running = False; self.porcupine = None; self.stream = None
        if not all([pvporcupine, sd, access_key, keyword_paths, model_path]): return
        try:
            self.porcupine = pvporcupine.create(access_key=access_key, keyword_paths=keyword_paths, model_path=model_path, sensitivities=[sensitivity] * len(keyword_paths))
            self.sample_rate = self.porcupine.sample_rate; self.frame_length = self.porcupine.frame_length
        except Exception as e: Logger.error(f"WakeWordListener: Failed to create Porcupine instance: {e}")
    def run(self):
        if not self.porcupine: return
        self._running = True
        try:
            with audio_lock: self.stream = sd.InputStream(samplerate=self.sample_rate, channels=1, dtype='int16', blocksize=self.frame_length); self.stream.start()
            while self._running:
                pcm = self.stream.read(self.frame_length)[0]
                if not self._running: break
                if self.porcupine.process(pcm[:, 0]) >= 0: Clock.schedule_once(lambda dt: self.callback())
        except Exception as e: Logger.error(f"WakeWordListener: Audio stream error: {e}")
        finally:
            with audio_lock:
                if self.stream: self.stream.stop(); self.stream.close()
            self._running = False
    def stop(self): self._running = False;
    if self.porcupine: self.porcupine.delete()
class NetworkNLUProcessor:
    def __init__(self, get_url_callback): self.get_url = get_url_callback; self.user_id = str(uuid.uuid4())
    def get_endpoints(self):
        base_url = self.get_url()
        if not base_url or not base_url.startswith('http'): return None, None
        return f"{base_url}/chat", f"{base_url}/reset"
    def process_text(self, text, callback):
        chat_endpoint, _ = self.get_endpoints()
        if not chat_endpoint: Clock.schedule_once(lambda dt: callback({"action": "chat", "spoken_response": "NLU server not configured."})); return
        def _send_request():
            payload, headers = {"message": text, "user_id": self.user_id}, {'Content-Type': 'application/json', 'Accept': 'application/json'}
            response_json = {"action": "chat", "spoken_response": "Error connecting to NLU server."}
            try:
                response = requests.post(chat_endpoint, headers=headers, json=payload, timeout=60); response.raise_for_status()
                response_json = response.json()
            except Exception as e: Logger.error(f"NLU Client: Request to {chat_endpoint} failed: {e}"); response_json["spoken_response"] = "Failed to connect. Check server status and settings."
            finally: Clock.schedule_once(lambda dt: callback(response_json))
        Thread(target=_send_request, daemon=True).start()
    def reset_history(self):
        _, reset_endpoint = self.get_endpoints()
        if not reset_endpoint: return
        def _send_reset():
            try: requests.post(reset_endpoint, headers={'Content-Type': 'application/json'}, json={"user_id": self.user_id}, timeout=10)
            except Exception as e: Logger.error(f"NLU Client: Failed to send reset request: {e}")
        Thread(target=_send_reset, daemon=True).start()
class CommandProcessor:
    def __init__(self, nlu_processor, app_instance):
        self.nlu_processor, self.app = nlu_processor, app_instance; self.active, self.stream, self._current_callback = False, None, None
        self.current_lang_code, self.stt_recognizer, self.tts_voice = None, None, None
        self.action_handlers = {"open_app": self.handle_open_app, "web_search": self.handle_web_search, "play_media": self.handle_play_media, "control_vpn": self.handle_control_vpn, "make_call": self.handle_make_call, "check_phone_status": self.handle_check_phone_status, "learn_app_intent": self.handle_learn_app_intent, "enable_accessibility": self.handle_enable_accessibility, "chat": self.handle_chat}
    def _load_model(self, model_type, lang_code):
        global VoskModelEN, VoskModelHI, PiperVoiceEN, PiperVoiceHI
        if model_type == 'stt':
            if lang_code == 'en' and VoskModelEN is None and os.path.exists(VOSK_MODEL_PATH_EN): globals()['VoskModelEN'] = Model(VOSK_MODEL_PATH_EN)
            elif lang_code == 'hi' and VoskModelHI is None and os.path.exists(VOSK_MODEL_PATH_HI): globals()['VoskModelHI'] = Model(VOSK_MODEL_PATH_HI)
        elif model_type == 'tts':
            if lang_code == 'en' and PiperVoiceEN is None and os.path.exists(PIPER_VOICE_EN_ONNX): globals()['PiperVoiceEN'] = piper.PiperVoice.load(PIPER_VOICE_EN_ONNX, config_path=PIPER_VOICE_EN_JSON)
            elif lang_code == 'hi' and PiperVoiceHI is None and os.path.exists(PIPER_VOICE_HI_ONNX): globals()['PiperVoiceHI'] = piper.PiperVoice.load(PIPER_VOICE_HI_ONNX, config_path=PIPER_VOICE_HI_JSON)
    def set_language(self, lang_code):
        self.current_lang_code = lang_code; self._load_model('stt', lang_code); self._load_model('tts', lang_code)
        stt_model, self.tts_voice = (VoskModelEN, PiperVoiceEN) if lang_code == 'en' else (VoskModelHI, PiperVoiceHI)
        self.stt_recognizer = KaldiRecognizer(stt_model, SAMPLE_RATE) if stt_model else None
    def start_listening(self, callback_on_result):
        if not self.stt_recognizer or not sd: return
        self.active, self._current_callback = True, callback_on_result
        def audio_callback(indata, f, t, s):
            if self.active and self.stt_recognizer.AcceptWaveform(bytes(indata)): result = self.stt_recognizer.Result(); Clock.schedule_once(lambda dt: self.stop_listening()); Clock.schedule_once(lambda dt: self.process_stt_result(result))
        with audio_lock: self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='int16', blocksize=int(SAMPLE_RATE * 0.2), callback=audio_callback); self.stream.start()
    def stop_listening(self):
         if not self.active: return
         self.active = False
         with audio_lock:
             if self.stream: self.stream.stop(); self.stream.close(); self.stream = None
    def process_stt_result(self, vosk_result_json):
        if self.active: self.stop_listening()
        transcript = ""; nlu_json = {"transcript": transcript}
        try: transcript = json.loads(vosk_result_json).get('text', ''); nlu_json["transcript"] = transcript
        except Exception: pass
        if transcript: self.nlu_processor.process_text(transcript, lambda res: self.handle_nlu_response({**res, **nlu_json}))
        elif self._current_callback: Clock.schedule_once(lambda dt: self._current_callback(""))
    def handle_nlu_response(self, nlu_json):
        action, params, spoken_response = nlu_json.get("action", "chat"), nlu_json.get("parameters", {}), nlu_json.get("spoken_response", "I'm not sure.")
        self.app.add_log(f"[b]You:[/b] {nlu_json.get('transcript', '')}"); self.app.add_log(f"[i]NLU -> {action}, Params: {params}[/i]")
        self.action_handlers.get(action, self.handle_chat)(params, spoken_response)
    def handle_chat(self, params, spoken_response): self.run_tts(spoken_response)
    def handle_open_app(self, params, spoken_response):
        if platform == 'android' and autoclass and params.get("package_name"):
            try: PythonActivity = autoclass('org.kivy.android.PythonActivity'); intent = PythonActivity.mActivity.getPackageManager().getLaunchIntentForPackage(params.get("package_name")); PythonActivity.mActivity.startActivity(intent)
            except Exception: spoken_response = f"I couldn't open that app."
        self.run_tts(spoken_response)
    def handle_web_search(self, params, spoken_response):
        if platform == 'android' and autoclass and params.get("query"):
            try:
                Intent, Uri, PythonActivity = autoclass('android.content.Intent'), autoclass('android.net.Uri'), autoclass('org.kivy.android.PythonActivity')
                intent = Intent(Intent.ACTION_VIEW, Uri.parse(f"https://www.google.com/search?q={requests.utils.quote(params.get('query'))}")); PythonActivity.mActivity.startActivity(intent)
            except Exception: spoken_response = "I couldn't start a web search."
        self.run_tts(spoken_response)
    def handle_play_media(self, params, spoken_response):
        if platform == 'android' and autoclass and params.get("query"):
            try:
                Intent, Uri, PythonActivity = autoclass('android.content.Intent'), autoclass('android.net.Uri'), autoclass('org.kivy.android.PythonActivity')
                intent = Intent(Intent.ACTION_VIEW, Uri.parse(f"vnd.youtube:{requests.utils.quote(params.get('query'))}"))
                if PythonActivity.mActivity.getPackageManager().queryIntentActivities(intent, 0).isEmpty(): intent = Intent(Intent.ACTION_VIEW, Uri.parse(f"https://www.youtube.com/results?search_query={requests.utils.quote(params.get('query'))}"))
                PythonActivity.mActivity.startActivity(intent)
            except Exception: spoken_response = f"I had trouble trying to play '{params.get('query')}'."
        self.run_tts(spoken_response)
    def handle_control_vpn(self, params, spoken_response):
        state, VPN_PACKAGE_NAME = params.get("state"), "com.your.vpn.app.package"
        CONNECT_ACTION, DISCONNECT_ACTION = "com.your.vpn.app.action.CONNECT", "com.your.vpn.app.action.DISCONNECT"
        if platform == 'android' and autoclass and state:
            try:
                Intent, PythonActivity = autoclass('android.content.Intent'), autoclass('org.kivy.android.PythonActivity')
                intent = Intent(CONNECT_ACTION if state == "on" else DISCONNECT_ACTION); intent.setPackage(VPN_PACKAGE_NAME); PythonActivity.mActivity.startActivity(intent)
            except Exception: spoken_response = "I couldn't interact with the VPN app."
        self.run_tts(spoken_response)
    def handle_make_call(self, params, spoken_response):
        number = params.get("number")
        if platform == 'android' and autoclass and number:
            try:
                if not check_permission(Permission.CALL_PHONE): raise PermissionError("CALL_PHONE denied.")
                Intent, Uri, PythonActivity = autoclass('android.content.Intent'), autoclass('android.net.Uri'), autoclass('org.kivy.android.PythonActivity')
                intent = Intent(Intent.ACTION_CALL, Uri.parse(f"tel:{number}")); PythonActivity.mActivity.startActivity(intent)
            except Exception: spoken_response = "I couldn't make the call. Do I have permission?"
        elif params.get("contact_name"): spoken_response = "I can't access contacts yet, please provide a number."
        self.run_tts(spoken_response)
    def handle_check_phone_status(self, params, spoken_response):
        if platform == 'android' and autoclass:
            try:
                if not check_permission(Permission.READ_PHONE_STATE): raise PermissionError("READ_PHONE_STATE denied.")
                TelephonyManager, Context, PythonActivity = autoclass('android.telephony.TelephonyManager'), autoclass('android.content.Context'), autoclass('org.kivy.android.PythonActivity')
                call_state = PythonActivity.mActivity.getSystemService(Context.TELEPHONY_SERVICE).getCallState()
                if call_state == TelephonyManager.CALL_STATE_RINGING: spoken_response = "Your phone is currently ringing."
                elif call_state == TelephonyManager.CALL_STATE_OFFHOOK: spoken_response = "You are currently in a call."
                else: spoken_response = "Your phone is not in a call."
            except Exception: spoken_response = "I couldn't check the phone status. Do I have permission?"
        self.run_tts(spoken_response)
    def handle_learn_app_intent(self, params, spoken_response): self.run_tts(spoken_response, on_finish_callback=lambda: self.app.launch_app_picker(params.get("app_name", "the app")))
    def handle_enable_accessibility(self, params, spoken_response): self.run_tts(spoken_response, on_finish_callback=self.app.open_accessibility_settings)
    def run_tts(self, spoken_response, on_finish_callback=None):
        final_callback = on_finish_callback if on_finish_callback else self._current_callback
        def _synthesize_and_play():
            try:
                audio_data = b"".join(list(self.tts_voice.synthesize(spoken_response)))
                if audio_data and sd:
                     with audio_lock: sd.play(np.frombuffer(audio_data, dtype=np.int16), SAMPLE_RATE); sd.wait()
            except Exception as e: Logger.error(f"TTS failed: {e}")
            finally:
                 if final_callback: Clock.schedule_once(lambda dt: final_callback(spoken_response if on_finish_callback is None else None))
        Thread(target=_synthesize_and_play, daemon=True).start()
class VoiceAssistantApp(App):
    use_kivy_settings, REQUIRED_PERMISSIONS = True, ["android.permission.RECORD_AUDIO", "android.permission.INTERNET", "android.permission.READ_PHONE_STATE", "android.permission.CALL_PHONE"]
    def get_current_nlu_url(self):
        try: return self.config.get('nlu_server', f"{self.config.get('nlu_server', 'active_backend').lower()}_url")
        except Exception: return None
    def build_config(self, config): config.setdefaults('nlu_server', {'active_backend': 'Local', 'local_url': 'http://192.168.1.100:5000', 'ec2_url': 'http://YOUR_EC2_PUBLIC_IP:5000', 'lightning_url': 'https://YOUR_LIGHTNING_URL.litng.ai'})
    def build_settings(self, settings): settings.add_json_panel('NLU Server', self.config, 'settings.json')
    def on_config_change(self, config, section, key, value):
        if section == 'nlu_server': self.add_log(f"[i]Active NLU Server URL is now: {self.get_current_nlu_url()}[/i]")
    def build(self):
        self.settings_cls = SettingsWithSidebar; self.state, self.current_lang, self.wake_word_listener = "INITIALIZING", "en", None
        self.nlu_processor = NetworkNLUProcessor(self.get_current_nlu_url); self.command_processor = CommandProcessor(self.nlu_processor, self)
        self.CUSTOM_ACTIONS_FILE = os.path.join(self.user_data_dir, 'custom_actions.json'); self.custom_actions = self.load_custom_actions()
        self.root_layout = BoxLayout(orientation='vertical', padding=10, spacing=10)
        self.permission_label = Label(text="Permissions pending...", color=(1, 0.6, 0, 1), size_hint_y=None, height=40)
        self.status_label = Label(text="Status: INITIALIZING", size_hint_y=None, height=40)
        self.log_view = ScrollView(); self.log_label = Label(text="", size_hint_y=None, halign='left', valign='top', markup=True)
        self.log_label.bind(texture_size=self.log_label.setter('size')); self.log_view.add_widget(self.log_label); self.log_messages = []
        button_layout = BoxLayout(size_hint_y=None, height=50, spacing=10)
        self.start_button = Button(text="Start Listener", on_press=self.toggle_ww_listener); self.lang_button = Button(text="Switch to Hindi", on_press=self.toggle_language)
        self.reset_button = Button(text="Reset Chat", on_press=self.reset_chat); self.settings_button = Button(text="Settings", on_press=self.open_settings)
        for btn in [self.start_button, self.lang_button, self.reset_button, self.settings_button]: button_layout.add_widget(btn)
        for widget in [self.permission_label, self.status_label, self.log_view, button_layout]: self.root_layout.add_widget(widget)
        return self.root_layout
    def on_start(self):
        if platform == 'android' and Permission: self.request_all_permissions()
        else: self.permissions_granted = True; self.on_permissions_granted()
    def request_all_permissions(self):
        missing = [p for p in self.REQUIRED_PERMISSIONS if not check_permission(p)]
        if not missing: self.on_permissions_granted(); return
        def callback(permissions, grants):
            if all(g == 0 for g in grants): self.on_permissions_granted()
            else:
                denied = [p.split('.')[-1] for p, g in zip(permissions, grants) if g != 0]
                self.permission_label.text = f"Permissions Denied: {', '.join(denied)}."; self.initialize_components()
        request_permissions(missing, callback)
    def on_permissions_granted(self):
        self.permissions_granted = True; self.permission_label.text = "All essential permissions granted."; self.permission_label.color = (0, 1, 0.3, 1); self.initialize_components()
    def initialize_components(self):
        self.command_processor.set_language(self.current_lang)
        if sd and self.command_processor.stt_recognizer and self.command_processor.tts_voice: self.update_status("IDLE"); self.add_log("[color=00ff00]Components Initialized.[/color]")
        else: self.update_status("ERROR: Components Failed"); self.add_log("[color=ff0000]Error: A core component failed to load.[/color]")
    def toggle_ww_listener(self, instance):
        if self.wake_word_listener and self.wake_word_listener.is_alive(): self.stop_ww_listener()
        else: self.start_ww_listener()
    def start_ww_listener(self):
        self.wake_word_listener = WakeWordListener(PICOVOICE_ACCESS_KEY, PORCUPINE_KEYWORD_PATHS, PORCUPINE_MODEL_PATH, self.on_wake_word_detected)
        if self.wake_word_listener and self.wake_word_listener.porcupine:
            self.wake_word_listener.start(); self.update_status("LISTENING_WW"); self.start_button.text = "Stop Listener"
            self.lang_button.disabled = self.reset_button.disabled = self.settings_button.disabled = True
    def stop_ww_listener(self):
        if self.wake_word_listener: self.wake_word_listener.stop()
        self.wake_word_listener = None; self.start_button.text = "Start Listener"
        self.lang_button.disabled = self.reset_button.disabled = self.settings_button.disabled = False
        if self.state in ["LISTENING_WW", "STARTING_WW"]: self.update_status("IDLE")
    def on_wake_word_detected(self):
        if self.state == "LISTENING_WW": self.add_log("[color=00ffff]Wake Word Detected![/color]"); self.update_status("LISTENING_CMD"); self.command_processor.start_listening(self.on_command_result)
    def on_command_result(self, final_spoken_response):
        if final_spoken_response: self.add_log(f"[b]Bot:[/b] {final_spoken_response}")
        self.add_log("-" * 20)
        if self.start_button.text == "Stop Listener": self.update_status("LISTENING_WW")
        else: self.update_status("IDLE")
    def toggle_language(self, instance):
        self.current_lang = "hi" if self.current_lang == "en" else "en"; self.lang_button.text = f"Switch to {'English' if self.current_lang == 'hi' else 'Hindi'}"
        self.add_log(f"[i]Switching language to {self.current_lang}...[/i]"); self.command_processor.set_language(self.current_lang)
    def reset_chat(self, instance): self.add_log("[i]Resetting chat history on server...[/i]"); self.nlu_processor.reset_history()
    def update_status(self, new_state): self.state = new_state; self.status_label.text = f"Status: {self.state}"
    def add_log(self, message):
        self.log_messages.append(message)
        if len(self.log_messages) > 100: self.log_messages.pop(0)
        self.log_label.text = "\n".join(self.log_messages); Clock.schedule_once(lambda dt: setattr(self.log_view, 'scroll_y', 0), 0.1)
    def load_custom_actions(self):
        try:
            if os.path.exists(self.CUSTOM_ACTIONS_FILE):
                with open(self.CUSTOM_ACTIONS_FILE, 'r') as f: return json.load(f)
        except Exception: return {}
        return {}
    def save_custom_actions(self):
        try:
            with open(self.CUSTOM_ACTIONS_FILE, 'w') as f: json.dump(self.custom_actions, f, indent=4)
        except Exception as e: Logger.error(f"Failed to save custom actions: {e}")
    def get_installed_apps(self):
        app_list = []
        if platform == 'android' and autoclass:
            try:
                PythonActivity = autoclass('org.kivy.android.PythonActivity'); Intent = autoclass('android.content.Intent'); intent = Intent(Intent.ACTION_MAIN, None); intent.addCategory(Intent.CATEGORY_LAUNCHER)
                apps = PythonActivity.mActivity.getPackageManager().queryIntentActivities(intent, 0)
                for app_info in apps: app_list.append({"name": app_info.loadLabel(PythonActivity.mActivity.getPackageManager()).toString(), "package": app_info.activityInfo.packageName})
                app_list.sort(key=lambda x: x['name'].lower())
            except Exception as e: Logger.error(f"Failed to get installed apps: {e}")
        return app_list
    def launch_app_picker(self, app_name_guess):
        installed_apps = self.get_installed_apps()
        if not installed_apps: return
        def on_app_selected(selected_app):
            app_name, package_name = selected_app['name'], selected_app['package']
            self.add_log(f"[color=00ff00]Learned: '{app_name}' is '{package_name}'[/color]")
            self.custom_actions[app_name.lower()] = self.custom_actions[app_name_guess.lower()] = package_name
            self.save_custom_actions()
            learning_text = f"Learning complete: The app '{app_name}' has package name '{package_name}'. Remember this."
            self.nlu_processor.process_text(learning_text, lambda res: self.add_log(f"[i]NLU confirmation: {res.get('spoken_response')}[/i]"))
        app_picker = AppListView(app_list=installed_apps, callback=on_app_selected); app_picker.open()
    def open_accessibility_settings(self):
        if platform == 'android' and autoclass:
            try:
                Settings, Intent, PythonActivity = autoclass('android.provider.Settings'), autoclass('android.content.Intent'), autoclass('org.kivy.android.PythonActivity')
                intent = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS); PythonActivity.mActivity.startActivity(intent)
                self.add_log("Please find this app in the list and enable its service.")
            except Exception as e: self.add_log(f"[color=ff0000]Could not open accessibility settings: {e}[/color]")
    def on_stop(self): self.stop_ww_listener(); self.command_processor.stop_listening()

if __name__ == '__main__':
    if not PICOVOICE_ACCESS_KEY: print("FATAL ERROR: PICOVOICE_ACCESS_KEY not found in .env file."); exit()
    VoiceAssistantApp().run()
