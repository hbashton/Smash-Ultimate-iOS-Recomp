# iOS build

This target compiles WindowSmash as an arm64 iOS app with SDL3 and the Metal renderer. It supports iPhone and iPad on iOS 17 or newer.

## Private build flow

The public GitHub repository contains source and build scripts only. A one-time authenticated transfer sends generated recompilation C, manifests, and relocatable module images to the macOS runner. The runner returns an unsigned app bundle directly to the owner's computer; the workflow does not create a GitHub artifact or release. The owner's prepared ROMFS and NCA files stay local. `package_ipa.py` makes a small IPA and a separate ZIP for those files; no game data is sent to GitHub or included in the IPA.

The scripts are:

- `transfer.py`: prepare and serve the private compiler input, then receive the finished IPA directly.
- `build_ios.py`: cross-compile the generated game and runtime for iOS arm64.
- `package_ipa.py`: create and verify a small IPA with explicit `Payload/` directory entries. It uses ordinary ZIP headers for files below the Zip64 size limit, and Zip64 only for larger individual files. Run with `--app` and `--out` on the runner; when packaging locally with game files, also pass `--game-data` and `--game-data-out` to create the separate game-data ZIP.

The final IPA must be signed with the owner's Apple signing certificate before device installation. Install the small IPA, transfer the game-data ZIP to the iPad, and open it in Files to extract it. Launch the app and tap **Choose dump folder**. Select the extracted top-level `dump` folder (the one containing `modules`). The app copies the required files into its Documents folder, shows copy progress, and starts the game when the import is complete. Keep the app open and leave the folder names unchanged. Keep the game-data ZIP private; it contains the user's game files and is never uploaded by the build workflow.

## Controllers

Extended and generic physical gamepad profiles are enabled. A connected iOS controller waits unassigned until its first button, stick, or trigger input, then claims the next free player slot. GameCube and recognized Mayflash W012/GameCube adapter names use the GameCube trigger and button layout. SDL 3's iOS backend reads Apple's physical input profiles, allowing a multi-port adapter that iOS exposes as separate controllers to appear as separate gamepads.
