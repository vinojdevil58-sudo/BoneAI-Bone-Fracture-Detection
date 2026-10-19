BONEAI WINDOWS SETUP

A ready-made .venv is intentionally not bundled because virtual environments are machine-, path-, operating-system-, and Python-version-specific.

1. Extract the ZIP.
2. Double-click SETUP_VENV.bat. It detects Python 3.13, 3.12, or 3.11 (or the default Python launcher) and installs requirements.
3. Double-click CREATE_DOCTOR.bat to create your own doctor login. There is no demo account.
4. Double-click RUN_BONEAI.bat.
5. Visit http://127.0.0.1:5000.

If package installation fails on Python 3.13 because a PyTorch/dependency wheel is unavailable, install Python 3.12 (64-bit), delete the incomplete .venv folder, and rerun SETUP_VENV.bat.
