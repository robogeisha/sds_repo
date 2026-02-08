# push_to_talk_voice_bot.py

import os
import signal
import subprocess
import time
import threading
import requests
from pynput import keyboard

RASA_URL = "http://localhost:5005/webhooks/rest/webhook"


SENDER = "voice_user"

WAV_PATH = "/tmp/ptt_input.wav"
WHISPER_MODEL = "base"
LANG = "en"
VOICE = "en-US-JennyNeural"

MIN_RECORD_SECONDS = 0.4
MIN_WAV_BYTES = 8000

_arecord_proc = None
_is_recording = False
_record_start_time = 0.0
_busy = False
_lock = threading.Lock()

# client-side states
_waiting_for_platform = False
_waiting_yesno = False  # guard for "did that fix it?"


def speak(text: str):
    if not text:
        return
    out_mp3 = "/tmp/tts.mp3"
    subprocess.run(
        ["edge-tts", "--text", text, "--voice", VOICE, "--write-media", out_mp3],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["ffplay", "-nodisp", "-autoexit", out_mp3],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def start_recording():
    global _arecord_proc, _is_recording, _record_start_time
    with _lock:
        if _busy or _is_recording:
            return
        _record_start_time = time.time()
        _arecord_proc = subprocess.Popen(
            ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", WAV_PATH]
        )
        _is_recording = True
    print("\n🎙️ Recording… (release SPACE to send)")


def stop_recording() -> bool:
    global _arecord_proc, _is_recording, _record_start_time
    with _lock:
        if not _is_recording:
            return False

    elapsed = time.time() - _record_start_time
    if elapsed < MIN_RECORD_SECONDS:
        time.sleep(MIN_RECORD_SECONDS - elapsed)

    try:
        _arecord_proc.send_signal(signal.SIGINT)
        _arecord_proc.wait(timeout=2)
    except Exception:
        try:
            _arecord_proc.kill()
        except Exception:
            pass

    with _lock:
        _is_recording = False

    try:
        return os.path.exists(WAV_PATH) and os.path.getsize(WAV_PATH) >= MIN_WAV_BYTES
    except Exception:
        return False


def transcribe_whisper(wav_path: str) -> str:
    out_dir = "/tmp"
    base = os.path.splitext(os.path.basename(wav_path))[0]
    txt_path = os.path.join(out_dir, f"{base}.txt")
    try:
        os.remove(txt_path)
    except FileNotFoundError:
        pass

    subprocess.run(
        [
            "whisper", wav_path,
            "--model", WHISPER_MODEL,
            "--language", LANG,
            "--fp16", "False",
            "--output_format", "txt",
            "--output_dir", out_dir,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not os.path.exists(txt_path):
        return ""
    with open(txt_path, "r", encoding="utf-8") as f:
        return f.read().strip()


def ask_rasa(text: str) -> str:
    r = requests.post(RASA_URL, json={"sender": SENDER, "message": text}, timeout=30)
    r.raise_for_status()
    msgs = r.json()
    return " ".join(m.get("text", "") for m in msgs if m.get("text")).strip()


def send_intent(intent_name: str) -> str:
    """
    Send a slash intent via the normal webhook pipeline.
    This is more reliable than trigger_intent + empty message.
    """
    return ask_rasa(f"/{intent_name}")


def _classify_yesno(text: str):
    """
    Returns "yes", "no", or None.
    """
    t = text.strip().lower()

    yes_words = [
        "yes", "yeah", "yep", "yup", "sure", "correct", "fixed", "works", "working",
        "it works", "it worked", "now works", "now it works"
    ]
    no_words = [
        "no", "nope", "nah", "not", "still", "still broken", "doesn't", "doesnt",
        "not working", "no change", "didn't", "didnt"
    ]


    if t in ["yes", "yeah", "yep", "yup"]:
        return "yes"
    if t in ["no", "nope", "nah"]:
        return "no"

    if any(w in t for w in yes_words):
        return "yes"
    if any(w in t for w in no_words):
        return "no"
    return None


def _process_turn():
    global _busy, _waiting_for_platform, _waiting_yesno
    print("⏳ Processing…")

    try:
        user_text = transcribe_whisper(WAV_PATH)
    except subprocess.CalledProcessError:
        speak("Sorry, I didn't catch that.")
        with _lock:
            _busy = False
        return

    if not user_text:
        speak("Sorry—try again.")
        with _lock:
            _busy = False
        return

    print(f"You: {user_text}")

    try:
        # 1) YES/NO GUARD (highest priority)
        if _waiting_yesno:
            yn = _classify_yesno(user_text)
            if yn == "yes":
                bot_text = send_intent("affirm")
                _waiting_yesno = False
            elif yn == "no":
                bot_text = send_intent("deny")
                _waiting_yesno = False
            else:
                bot_text = "Just say yes or no."
                # keep waiting

        # 2) PLATFORM GUARD
        elif _waiting_for_platform:
            t = user_text.strip().lower()

            if any(k in t for k in ["linux", "ubuntu", "debian", "arch", "fedora", "mint", "kali"]):
                bot_text = send_intent("platform_linux")
                _waiting_for_platform = False
            elif any(k in t for k in ["windows", "win", "win10", "win11", "windows 10", "windows 11"]):
                bot_text = send_intent("platform_windows")
                _waiting_for_platform = False
            elif any(k in t for k in ["mac", "macos", "osx", "macbook", "apple"]):
                bot_text = send_intent("platform_macos")
                _waiting_for_platform = False
            else:
                bot_text = "Just say Windows, macOS, or Linux."


        # 3) NORMAL FLOW
        else:
            bot_text = ask_rasa(user_text)

    except Exception as e:
        print("Rasa connection failed:", e)
        speak("I can't reach the server right now.")
        with _lock:
            _busy = False
        return

    if not bot_text:
        bot_text = "Say that again but, like, clearer."

    # Detect modes from bot text
    low = bot_text.lower()
    if "which platform are you on" in low:
        _waiting_for_platform = True
    if "did that fix it" in low or "did that help" in low:
        _waiting_yesno = True

    print(f"Bot: {bot_text}")
    speak(bot_text)

    print("\nHold SPACE to talk. ESC to quit.")
    with _lock:
        _busy = False


def on_press(key):
    if key == keyboard.Key.space:
        start_recording()


def on_release(key):
    global _busy
    if key == keyboard.Key.esc:
        print("\nBye.")
        return False

    if key == keyboard.Key.space:
        ok = stop_recording()
        if not ok:
            speak("I heard nothing. Try again.")
            print("\nHold SPACE to talk. ESC to quit.")
            return

        with _lock:
            if _busy:
                return
            _busy = True

        threading.Thread(target=_process_turn, daemon=True).start()


def main():
    print("✅ Wi-Fi voice assistant (push-to-talk)")
    print("Hold SPACE to talk, release to send. Press ESC to quit.\n")

    intro = "What's up? What's wrong?"
    print(f"Bot: {intro}")
    speak(intro)

    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


if __name__ == "__main__":
    main()

