<<<<<<< HEAD
# BoneAI — simple homepage, no demo login, Windows setup and Android starter

This ZIP contains the full Flask website project, YOLOv8 model files, patient/doctor pages, X-ray upload and prediction workflow, setup scripts, and a simplified responsive homepage. The homepage shows a single wrist X-ray image and has no analysis-result panel. It tries the online Wikimedia Commons X-ray first and falls back to the included local image if the online image cannot load.

## Windows setup
1. Extract the ZIP.
2. Open `BoneAI_Full_Website`.
3. Double-click `SETUP_VENV.bat` to create `.venv` locally and install requirements.
4. Double-click `CREATE_DOCTOR.bat` once to create your own doctor account. There is no demo login or default admin account.
5. Double-click `RUN_BONEAI.bat`.
6. Open `http://127.0.0.1:5000`.

A prebuilt `.venv` is not included because it is machine- and Python-version-specific. Setup downloads packages and needs internet access. If PyTorch/Ultralytics install fails with your installed Python, install a compatible 64-bit Python version and rerun setup.

## Google Play / Android
The `android-play-store-starter` folder contains a Kotlin Android WebView starter targeting API 36. It is **not a ready-to-upload Play Store app**: you must first host the Flask website on a public HTTPS domain, set that URL in `MainActivity.kt`, test the app, create a signed Android App Bundle (`.aab`), and complete Google Play's privacy, health-app, data-safety, listing, and testing requirements. Read `android-play-store-starter/README_PLAY_STORE.md`.

Google Play health-app policy requires an active public privacy policy URL, a Health apps declaration, accurate Data safety disclosures, and a clear medical disclaimer. The current prototype must not be marketed as a clinically validated diagnostic tool.

## Important security notes
The included Flask website is a local development prototype. Do not expose it directly to the internet as-is. Production hosting requires secure deployment configuration, a strong secret key, HTTPS, secure session cookies, persistent storage, appropriate access controls, privacy/retention controls, and a security review. The model's accuracy has not been clinically validated.
=======
# BoneAI-Bone-Fracture-Detection
>>>>>>> 2268874d40584c77b91bcae4194ef13aa91a4b08
