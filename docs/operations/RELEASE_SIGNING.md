# Android release signing

Debug and public release builds use separate identities. Keep the existing debug
key for development updates; a release APK signed by a different key cannot
replace an installed debug APK. Do not uninstall an existing app merely to test
release signing: that may erase its local data and pairing.

Release packaging requires three environment variables:

- `IGOREK_ANDROID_KEYSTORE`: an existing private PKCS12/JKS keystore outside Git.
- `IGOREK_ANDROID_STORE_PASSWORD_FILE`: a private UTF-8 password file outside Git.
- `IGOREK_ANDROID_KEY_ALIAS`: the existing signing alias.

Run `./android/gradlew -p android assembleRelease` with those variables set.
Packaging fails if signing configuration is missing or stored inside the source
repository. Passwords are read from files and must not appear in commands,
reports or CI logs. Verify the final APK with `apksigner verify --verbose` and
record its public certificate SHA-256 fingerprint alongside the artifact hash.

Retain an independently recoverable encrypted backup of the keystore and its
password. A second copy on the same machine is only a local backup; external
custody/recovery remains the owner's responsibility. Do not upload signing keys
as source files or ordinary Actions artifacts. CI release signing requires a
separately protected environment and approved secret provisioning.

Signing proves the publishing identity. It does not establish physical-device,
background, voice or update acceptance. Source publication and stable binary
release acceptance remain separate gates.
