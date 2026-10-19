# BoneAI Android / Google Play starter

This folder is a **starter Android WebView wrapper**, not a finished APK/AAB and not yet ready to submit. The Flask website must first be deployed to a public HTTPS domain. The app cannot reach a Flask server running only at `127.0.0.1` on your PC.

## Build steps
1. Install Android Studio and Android SDK Platform 36; use a supported JDK (Android Studio's bundled JDK is recommended).
2. Deploy the Flask BoneAI website to a secure public HTTPS host. Configure production settings, a strong secret key, secure cookies, database persistence, authentication, upload limits, and backups. Do not expose Flask's development server or run `debug=True` publicly.
3. Open this `android-play-store-starter` folder in Android Studio.
4. Edit `app/src/main/java/com/boneai/app/MainActivity.kt` and replace `websiteUrl` with the real HTTPS URL.
5. Sync Gradle, run on a test device, and verify login, registration, image upload, history, privacy information, account deletion, and all links.
6. In Android Studio choose **Build > Generate Signed Bundle / APK > Android App Bundle**. Create and securely back up your own signing key. Never share the key or passwords.
7. Upload the signed `.aab` to Google Play Console and complete every policy and listing form. Google Play requirements can change; as of August 31, 2026, new apps and updates must target Android 16 / API 36 or higher.

## Google Play health-app requirements to address
- Provide an active, public HTTPS privacy policy URL and make the privacy policy accessible inside the app.
- Complete the Health apps declaration and accurate Data safety form in Play Console.
- Clearly state the app is an educational prototype, **not a medical device and does not diagnose, treat, cure, or prevent a medical condition**; advise users to consult a qualified healthcare professional.
- Implement a clear account/data deletion process and describe retention and sharing of sensitive health data accurately.
- Do not submit until you have verified the model, security, data handling, privacy disclosures, app content, and all applicable local laws.

Official policy links:
- Target API requirements: https://support.google.com/googleplay/android-developer/answer/11926878
- Health apps policy: https://support.google.com/googleplay/android-developer/answer/16679553

The website's current local database and Flask setup are suitable for development testing only. A production deployment and security review are required before public release.
