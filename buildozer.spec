[app]
title = My Voice Assistant
package.name = myassistant
package.domain = org.test
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,json,pv,ppn,onnx,env
version = 1.0
requirements = python3,kivy==2.2.1,pyjnius==1.6.1,cython==0.29.36,android,requests,numpy,sounddevice,vosk,pvporcupine,piper-tts,python-dotenv,certifi
orientation = portrait
android.wakelock = True
android.permissions = RECORD_AUDIO, INTERNET, READ_PHONE_STATE, CALL_PHONE
android.api = 30
android.minapi = 21
android.archs = arm64-v8a
android.add_assets = assets
python.version = 3.11.5
hostpython3.version = 3.11.5
p4a.branch = master
p4a.build_flags = --without-tests
    # delete the bogus test before hostpython compile
# Delete CPython stdlib tests before hostpython3 byte-compile
pre_build = rm -rf .buildozer/android/platform/build-arm64-v8a/build/bootstrap_builds/sdl2/jni/SDL2_image/external && rm -rf .buildozer/android/app/tmp/Python-*/Lib/test
p4a.num_jobs = 6

[buildozer]
warn_on_root = 1
log_level = 2
