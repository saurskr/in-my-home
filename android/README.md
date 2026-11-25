# Android app (Trusted Web Activity)

The Android app is the web app wrapped in a thin native shell, so there is one code base. Its application id is **`app.saurskr.inmyhome`** (the package name; once published on Google Play it can never change).

`twa-manifest.json` here is the configuration for [Bubblewrap](https://github.com/GoogleChromeLabs/bubblewrap). Nothing in this repository builds the Android package for you yet; this folder only fixes the name and id.

## Build it (on a computer with Node 18+ and Java 17)
```bash
npm i -g @bubblewrap/cli
cd android
bubblewrap init --manifest https://saurskr.github.io/in-my-home/manifest.webmanifest   # or reuse twa-manifest.json
bubblewrap build        # makes app-release-signed.apk and app-release-bundle.aab
```
Keep the generated `android.keystore` and its password safe and **out of git**: losing it means you cannot update the app.

## Digital Asset Links (needed for the full-screen, no-address-bar look)
The app only hides the browser bar if the web site vouches for it. Publish this file at
`https://saurskr.github.io/.well-known/assetlinks.json` (the root of the *user* site, so it needs a repository named `saurskr.github.io`; a project site like `/in-my-home/` cannot serve it):
```json
[{"relation": ["delegate_permission/common.handle_all_urls"],
  "target": {"namespace": "android_app", "package_name": "app.saurskr.inmyhome",
             "sha256_cert_fingerprints": ["<SHA-256 of your signing key>"]}}]
```
`bubblewrap fingerprint` prints the value. Without the file the app still works but shows a browser address bar. A custom domain avoids that GitHub Pages limitation.

## Good to know
- The app keeps its data in the web view's storage on the phone (see "Your data" in Settings: export a backup now and then).
- Updating the web app (pushing to `main`) updates the Android app too; you only rebuild the package to change its name, icon or id.
