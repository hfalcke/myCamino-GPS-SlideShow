# myCamino Mac App Store Release Plan

Status: Planning
Target: One integrated myCamino app for Apple Silicon, macOS 14 or later
Distribution: Free Mac App Store app plus the existing direct GPL distribution

## Agreed Decisions

- [x] Publish one integrated app containing Adventure Map, GPX Editor, Control File Editor, and Slide Show.
- [x] Target Apple Silicon and macOS 14 or later for the first App Store release.
- [x] Keep the source repository and direct DMG distribution under GPL-3.0-or-later.
- [x] Prepare a narrowly scoped App Store distribution exception and custom EULA, subject to legal review.
- [x] Keep the first App Store release free, without advertising, donations, in-app purchases, or account requirements.

## Current Blocking Issues

- [ ] Replace the current ad-hoc signature with Apple Distribution signing and a Mac App Store provisioning profile.
- [ ] Enable App Sandbox. The current build has no entitlements.
- [ ] Correct the generated bundle identifier. The inspected build contains `myCamino GPS Track Show` instead of the intended `org.mycamino`.
- [ ] Replace persistent raw-path access with security-scoped bookmarks for user-selected Adventure directories.
- [ ] Make the bundled Slide Show process and FFmpeg valid sandbox-inheriting helper tools.
- [ ] Remove runtime dependence on optional or separately installed tools such as ExifTool.
- [ ] Add `PrivacyInfo.xcprivacy` and complete the App Store privacy declarations.
- [ ] Disable direct-update behavior in the App Store build.
- [ ] Complete the App Store metadata, legal review, TestFlight testing, and submission workflow.

## 1. Developer Account and App Record

- [ ] Confirm active Apple Developer Program membership and accept the current agreements.
- [ ] Register `org.mycamino` as the permanent App ID, if available.
- [ ] If `org.mycamino` is unavailable, choose one permanent reverse-DNS identifier before any build is uploaded.
- [ ] Create the macOS app record in App Store Connect.
- [ ] Create Apple Development, Apple Distribution, and Mac Installer certificates as required.
- [ ] Create development and Mac App Store provisioning profiles with App Sandbox enabled.
- [ ] Set the app name to `myCamino` and reserve the corresponding SKU.

## 2. Sandbox and File Access

- [ ] Add a main-app entitlements file containing:
  - `com.apple.security.app-sandbox`
  - `com.apple.security.files.user-selected.read-write`
  - `com.apple.security.network.client`
- [ ] Route initial project access through `NSOpenPanel`, document opening, or drag and drop.
- [ ] Create security-scoped bookmarks for Adventure directories the user wants to reopen later.
- [ ] Resolve bookmarks on startup and balance every `startAccessingSecurityScopedResource` call with a corresponding stop call.
- [ ] Detect stale bookmarks and ask the user to locate the directory again without losing Adventure history.
- [ ] Centralize authorized project-directory access for media, GPX, sidecars, maps, control files, playlists, logs, normalization, and file watching.
- [ ] Verify that child processes inherit access to the active Adventure directory.
- [ ] Store caches and application state using the sandbox container's Caches and Application Support directories.
- [ ] Store temporary recovery data under the sandbox-safe temporary directory.
- [ ] Ensure project-local `.mycamino` files are written only while their directory authorization is active.
- [ ] Avoid temporary-exception entitlements.

References:

- <https://developer.apple.com/documentation/security/app-sandbox>
- <https://developer.apple.com/documentation/security/accessing-files-from-the-macos-app-sandbox>

## 3. Metadata and Native System APIs

- [ ] Replace runtime `mdls` subprocess calls with ImageIO, AVFoundation, or other public framework APIs.
- [ ] Replace runtime `sips` subprocess calls with ImageIO/CoreGraphics conversion.
- [ ] Remove the optional external ExifTool dependency from the App Store feature path.
- [ ] Define and test the supported EXIF, HEIC, JPEG, QuickTime, and common video metadata fields.
- [ ] Report unsupported metadata clearly instead of asking users to install another program.
- [ ] Keep build-time development scripts separate from the shipped runtime.
- [ ] Verify that no feature downloads or executes Python code, plug-ins, binaries, or scripts after installation.

## 4. Slide Show and FFmpeg Helpers

- [ ] Keep Slide Show playback in its separate process unless sandbox testing demonstrates a safe in-process alternative.
- [ ] Place the Slide Show helper in the app's `Contents/MacOS` directory.
- [ ] Give the helper a stable signing identifier below the main bundle identifier.
- [ ] Sign the helper with only App Sandbox and `com.apple.security.inherit` entitlements.
- [ ] Package FFmpeg as an embedded helper/runtime component and sign every executable architecture slice.
- [ ] Verify Slide Show access to project media, maps, playlists, state files, and normalized video output under App Sandbox.
- [ ] Verify that termination, cancellation, and error reporting work without invoking a shell.
- [ ] Retain FFmpeg LGPL license, exact source information, checksum, and build configuration.

Reference:

- <https://developer.apple.com/documentation/xcode/embedding-a-helper-tool-in-a-sandboxed-app>

## 5. Bundle Metadata and Documents

- [ ] Set `CFBundleIdentifier` to the registered App ID.
- [ ] Set `CFBundleDisplayName` and `CFBundleName` to `myCamino`.
- [ ] Set `CFBundleShortVersionString` from the public release version.
- [ ] Set a monotonically increasing `CFBundleVersion`.
- [ ] Set `LSMinimumSystemVersion` to `14.0`.
- [ ] Set the macOS category to Photo & Video and use Travel as the secondary App Store category.
- [ ] Add `NSHumanReadableCopyright` for Heino Falcke.
- [ ] Declare `.adv` and `.gpx` document types and their imported type identifiers.
- [ ] Test Finder opening for Adventure and GPX files.
- [ ] Verify the complete icon set at all required macOS sizes.
- [ ] Confirm that the clean release bundle identifies itself as `org.mycamino`, not by its executable name.

## 6. Privacy and Network Services

- [ ] Add `PrivacyInfo.xcprivacy` to `Contents/Resources`.
- [ ] Declare tracking as disabled.
- [ ] Declare file timestamp and metadata access for explicitly user-selected files using reason `3B52.1`.
- [ ] Declare app-owned User Defaults access using reason `CA92.1` where required.
- [ ] Scan the frozen Python runtime and all bundled libraries for additional required-reason APIs.
- [ ] Document that photos, videos, GPX tracks, playlists, and control files are processed locally.
- [ ] Document optional transmission of coordinates and timestamps to map, geocoding, and weather providers.
- [ ] Document API-key storage in macOS Keychain.
- [ ] Ensure application-news requests use no persistent installation identifier.
- [ ] Confirm what each external provider retains and complete App Store privacy labels conservatively.
- [ ] Update the public privacy policy for the App Store version and provide its stable URL.
- [ ] Keep all optional weather and provider consent flows explicit.
- [ ] Complete Apple's export-compliance questionnaire for HTTPS/OpenSSL usage.
- [ ] Set `ITSAppUsesNonExemptEncryption` according to the resulting export-compliance determination.

References:

- <https://developer.apple.com/documentation/bundleresources/adding-a-privacy-manifest-to-your-app-or-third-party-sdk>
- <https://developer.apple.com/help/app-store-connect/manage-app-information/manage-app-privacy/>
- <https://developer.apple.com/help/app-store-connect/manage-app-information/overview-of-export-compliance>

## 7. Licensing and Content Rights

- [ ] Inventory all copyright holders and confirm that Heino Falcke can grant the App Store distribution exception for all first-party content.
- [x] Draft a narrow GPL-3.0-or-later App Store exception that permits Apple's distribution conditions without relicensing third-party code.
- [x] Add a Contributor Agreement covering GPL publication, App Store distribution, and future dual licensing.
- [x] Make the GPL, exception, EULA draft, contributor terms, and third-party notices visible in the applications and distribution bundle.
- [ ] Complete the support telephone number in the custom EULA.
- [ ] Have the exception and custom EULA reviewed by a lawyer familiar with GPL and App Store distribution.
- [ ] Install the approved custom EULA in App Store Connect for every distribution region.
- [ ] Keep the GPL license, source location, copyright information, and third-party notices accessible inside the app.
- [ ] Verify licenses for every bundled Python package, native library, map asset, font, icon, screenshot, and demo asset.
- [ ] Confirm rights and privacy clearance for all demo photographs and remove unnecessary personal metadata.
- [ ] Keep future donation functionality outside version 1; use StoreKit if an in-app tip feature is added later.

Reference:

- <https://developer.apple.com/help/app-store-connect/manage-app-information/provide-a-custom-license-agreement/>

## 8. App Store Build Pipeline

- [ ] Add a separate reproducible `build_app_store.sh`; do not replace `build_dmg.sh`.
- [ ] Build in a clean arm64 environment against the macOS 14 deployment target.
- [ ] Freeze the main app and helper without machine-specific absolute dependencies.
- [ ] Embed the provisioning profile and finalized Info.plist.
- [ ] Sign nested dynamic libraries, Python extensions, FFmpeg, and helper executables from the inside out.
- [ ] Sign the main bundle with Apple Distribution and the finalized entitlements.
- [ ] Build the upload package using the Mac Installer distribution identity.
- [ ] Add automated checks for signatures, Team ID, identifiers, architectures, deployment targets, entitlements, and privacy resources.
- [ ] Fail the build for unsigned Mach-O files, missing notices, external runtime dependencies, or development paths.
- [ ] Keep direct-DMG and App-Store feature flags explicit and covered by tests.
- [ ] In the App Store build, keep the news feature but remove direct download and self-update actions.
- [ ] Validate the package with Apple's tooling before upload.

## 9. App Store Product Page

- [ ] Prepare German and English app names, subtitles, descriptions, and keywords.
- [ ] Provide a support URL with working contact information.
- [ ] Provide the privacy-policy URL and optional privacy-choices URL.
- [ ] Provide at least five representative Mac screenshots without transparency.
- [ ] Show Adventure Map, GPX editing, media/control workflow, map generation, and Slide Show in the screenshots.
- [ ] Complete the age-rating questionnaire accurately; do not classify local user media as publicly distributed user-generated content.
- [ ] Confirm content rights for map tiles and attribution shown in screenshots.
- [ ] Set the price to Free.
- [ ] Do not include beta, unsigned-build, direct-download, or donation messaging in Store metadata.

References:

- <https://developer.apple.com/help/app-store-connect/reference/app-information/app-information/>
- <https://developer.apple.com/help/app-store-connect/manage-app-information/upload-app-previews-and-screenshots>
- <https://developer.apple.com/app-store/categories/>

## 10. Testing and App Review

- [ ] Run the complete unit and regression suite against the sandboxed Store build.
- [ ] Test a new Adventure, copied Adventure, large existing Adventure, and media-only Adventure.
- [ ] Test folder bookmarks across quit, restart, folder move, permission loss, and bookmark renewal.
- [ ] Test metadata extraction for supported image and video formats without ExifTool, `mdls`, or `sips` subprocesses.
- [ ] Test GPX editing, map providers, weather, geocoding, media watching, control-file editing, audio, narration, FFmpeg normalization, and Slide Show playback.
- [ ] Test offline startup and provider failures without delay or crash.
- [ ] Test single- and dual-screen playback on notebook and external displays.
- [ ] Test clean installation on a Mac that has no Homebrew, Python, ExifTool, or development checkout.
- [ ] Test VoiceOver labels, keyboard operation, focus order, reduced motion, window resizing, and readable contrast.
- [ ] Verify that all promised functionality is complete and that no placeholder or beta-only workflow remains.
- [ ] Upload the first build to App Store Connect and resolve all automated validation findings.
- [ ] Run internal TestFlight testing, followed by external TestFlight testing.
- [ ] Supply Review Notes describing the included demo, optional network services, file-access workflow, GPX Editor, Slide Show, and dual-screen behavior.
- [ ] Ensure App Review can exercise core functionality without buying a map-provider account or supplying an API key.
- [ ] Submit only after the sandbox test matrix and TestFlight acceptance criteria pass.

References:

- <https://developer.apple.com/app-store/review/guidelines/>
- <https://developer.apple.com/help/app-store-connect/manage-builds/upload-builds>
- <https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview>

## Release Acceptance Criteria

- [ ] The installed app launches on a clean Apple Silicon Mac running macOS 14 or later.
- [ ] It requires no separately installed runtime, command-line utility, or user account.
- [ ] Every executable and library has a valid Apple Distribution signature and expected entitlement set.
- [ ] App Sandbox permits all documented workflows and blocks unintended filesystem access.
- [ ] Reopened Adventures work through valid security-scoped bookmarks.
- [ ] App Store validation reports no blocking errors.
- [ ] Privacy labels, privacy policy, manifest, in-app explanations, and observed network behavior agree.
- [ ] GPL exception, custom EULA, bundled notices, and source availability have completed legal review.
- [ ] TestFlight testers can complete the demo and primary workflows without developer assistance.
- [ ] Direct GPL distribution remains buildable and functionally independent of the App Store package.
