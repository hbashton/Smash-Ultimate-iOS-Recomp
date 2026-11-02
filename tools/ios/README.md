# iOS build

This target compiles WindowSmash as an arm64 iOS app with SDL3 and the Metal renderer. It supports iPhone and iPad on iOS 17 or newer.

## Private build flow

The public GitHub repository contains source and build scripts only. A one-time authenticated transfer sends generated recompilation C, manifests, and relocatable module images to the macOS runner. The runner returns an unsigned app bundle directly to the owner's computer; the workflow does not create a GitHub artifact or release. The owner's prepared ROMFS and NCA files are read locally by `package_ipa.py` and streamed into the final IPA. Original NSPs and keys are not used by this packaging step.

The scripts are:

- `transfer.py`: prepare and serve the private compiler input, then receive the app bundle.
- `build_ios.py`: cross-compile the generated game and runtime for iOS arm64.
- `package_ipa.py`: create and verify a Zip64 IPA from the app bundle and local `dump/` folder.

The final app bundle must be signed with the owner's Apple signing certificate before device installation. The IPA includes the prepared game data when `package_ipa.py` receives the local `dump/` folder. The uncompressed assets are about 18.4 GB for the current 13.0.5 preparation, so this is intended for private sideloading and needs installation validation on the target devices.

## Controllers

Extended and generic physical gamepad profiles are enabled. A connected iOS controller waits unassigned until its first button, stick, or trigger input, then claims the next free player slot. GameCube and recognized Mayflash W012/GameCube adapter names use the GameCube trigger and button layout. SDL 3's iOS backend reads Apple's physical input profiles, allowing a multi-port adapter that iOS exposes as separate controllers to appear as separate gamepads.
