# SPDX-License-Identifier: GPL-3.0-or-later
"""Native first-use map-provider setup shared by myCamino applications."""

from __future__ import annotations

import objc
from AppKit import (
    NSAlert,
    NSButton,
    NSButtonTypeRadio,
    NSControlStateValueOn,
    NSFont,
    NSMakeRect,
    NSSecureTextField,
    NSTextField,
    NSView,
    NSWorkspace,
)
from Foundation import NSObject, NSURL

from map_provider_setup import (
    PROVIDER_DEFINITIONS,
    PROVIDERS_BY_ID,
    known_provider_credentials,
    save_map_provider_preference,
    validate_provider_credential,
)
from map_provider_utils import read_provider_credential, store_provider_credential


class ProviderChoiceController(NSObject):
    """Keep radio-style provider buttons mutually exclusive."""

    def init(self):
        self = objc.super(ProviderChoiceController, self).init()
        if self is not None:
            self.buttons = []
        return self

    def chooseProvider_(self, sender):
        for button in self.buttons:
            button.setState_(NSControlStateValueOn if button is sender else 0)


def _show_message(title: str, detail: str = "") -> None:
    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    if detail:
        alert.setInformativeText_(detail)
    alert.runModal()


def _open_url(url: str) -> None:
    if url:
        NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url))


def _choose_provider(preferred_provider: str, credential_id: str):
    known = known_provider_credentials(credential_id)
    preferred = str(preferred_provider or "").strip().lower()
    if preferred not in PROVIDERS_BY_ID:
        preferred = "geoapify"

    accessory = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 500.0, 326.0))
    controller = ProviderChoiceController.alloc().init()
    y = 294.0
    for index, definition in enumerate(PROVIDER_DEFINITIONS):
        suffix = " - API key stored" if definition.provider_id in known else ""
        radio = NSButton.alloc().initWithFrame_(NSMakeRect(0.0, y, 490.0, 22.0))
        radio.setButtonType_(NSButtonTypeRadio)
        radio.setTitle_(definition.display_name + suffix)
        radio.setTarget_(controller)
        radio.setAction_("chooseProvider:")
        radio.setState_(NSControlStateValueOn if definition.provider_id == preferred else 0)
        radio.setTag_(index)
        controller.buttons.append(radio)
        accessory.addSubview_(radio)
        description = NSTextField.labelWithString_(definition.description)
        description.setFont_(NSFont.systemFontOfSize_(11.0))
        description.setFrame_(NSMakeRect(24.0, y - 18.0, 466.0, 18.0))
        accessory.addSubview_(description)
        y -= 50.0

    alert = NSAlert.alloc().init()
    alert.setMessageText_("Choose a map provider")
    alert.setInformativeText_(
        "Automatic project Map Generation needs a provider that permits generated maps. "
        "Hosted services use your own account and quota; API keys are stored only in macOS Keychain."
    )
    alert.setAccessoryView_(accessory)
    alert.addButtonWithTitle_("Continue")
    alert.addButtonWithTitle_("Not Now - Use Limited OSM")
    alert.addButtonWithTitle_("Cancel")
    response = int(alert.runModal())
    if response == 1001:
        return "osm"
    if response != 1000:
        return None
    selected = next(
        (int(button.tag()) for button in controller.buttons if int(button.state()) == NSControlStateValueOn),
        -1,
    )
    return PROVIDER_DEFINITIONS[selected].provider_id if 0 <= selected < len(PROVIDER_DEFINITIONS) else preferred


def configure_hosted_provider(
    provider: str,
    credential_id: str,
    timeout_seconds: float = 12.0,
    *,
    initial_key: str = "",
):
    definition = PROVIDERS_BY_ID[provider]
    entered_key = str(initial_key or "")
    while True:
        accessory = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 430.0, 54.0))
        label = NSTextField.labelWithString_("API key")
        label.setFrame_(NSMakeRect(0.0, 31.0, 430.0, 18.0))
        field = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 430.0, 26.0))
        field.setPlaceholderString_("Paste the API key from the provider dashboard")
        field.setStringValue_(entered_key)
        accessory.addSubview_(label)
        accessory.addSubview_(field)

        alert = NSAlert.alloc().init()
        alert.setMessageText_(f"Set up {definition.display_name.replace(' (recommended)', '')}")
        alert.setInformativeText_(
            "Create or open your provider account, copy its API key, and paste it below. "
            "Free allowances and usage conditions are controlled by the provider and may change."
        )
        alert.setAccessoryView_(accessory)
        alert.addButtonWithTitle_("Validate and Store")
        alert.addButtonWithTitle_("Open Account Page")
        alert.addButtonWithTitle_("Back")
        response = int(alert.runModal())
        entered_key = str(field.stringValue()).strip()
        if response == 1001:
            _open_url(definition.signup_url)
            continue
        if response != 1000:
            return None
        validation = validate_provider_credential(
            provider,
            entered_key,
            timeout_seconds=timeout_seconds,
        )
        if validation.valid:
            try:
                store_provider_credential(provider, entered_key, credential_id)
                save_map_provider_preference(provider, credential_id, credential_verified=True)
            except (OSError, ValueError) as exc:
                _show_message("Could not store the API key", str(exc))
                continue
            return {"action": "configured", "provider": provider, "verified": True}
        if validation.network_error:
            offline = NSAlert.alloc().init()
            offline.setMessageText_("The provider could not be reached")
            offline.setInformativeText_(
                validation.message
                + "\n\nThe key can be stored unverified, but it will be tested again before maps are downloaded."
            )
            offline.addButtonWithTitle_("Store Unverified")
            offline.addButtonWithTitle_("Try Again")
            offline.addButtonWithTitle_("Back")
            offline_response = int(offline.runModal())
            if offline_response == 1000:
                try:
                    store_provider_credential(provider, entered_key, credential_id)
                    save_map_provider_preference(provider, credential_id, credential_verified=False)
                except (OSError, ValueError) as exc:
                    _show_message("Could not store the API key", str(exc))
                    continue
                return {"action": "unverified", "provider": provider, "verified": False}
            if offline_response == 1001:
                continue
            return None
        _show_message("API key was not accepted", validation.message)


def run_map_provider_setup(
    *,
    preferred_provider: str = "geoapify",
    credential_id: str = "default",
    timeout_seconds: float = 12.0,
):
    """Run first-use setup and return the requested caller action."""
    while True:
        provider = _choose_provider(preferred_provider, credential_id)
        if provider is None:
            return {"action": "cancel"}
        definition = PROVIDERS_BY_ID[provider]
        if definition.settings_only:
            return {"action": "settings", "provider": provider}
        if provider == "osm":
            try:
                save_map_provider_preference("osm", credential_id, credential_verified=True)
            except OSError as exc:
                _show_message("Could not remember the map-provider preference", str(exc))
            return {"action": "limited", "provider": "osm", "verified": True}
        result = configure_hosted_provider(
            provider,
            credential_id,
            timeout_seconds,
            initial_key=read_provider_credential(provider, credential_id),
        )
        if result is not None:
            return result
        preferred_provider = provider
