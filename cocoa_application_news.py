"""Native macOS presentation for the shared myCamino news channel.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import json
import threading
import time

import objc
from AppKit import (
    NSAlert,
    NSApp,
    NSBackingStoreBuffered,
    NSColor,
    NSFont,
    NSMakeRect,
    NSMakeSize,
    NSMenuItem,
    NSOffState,
    NSOnState,
    NSScrollView,
    NSTextField,
    NSTextView,
    NSViewMaxYMargin,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
    NSWorkspace,
)
from Foundation import NSObject, NSUserDefaults, NSURL

from application_news import (
    NEWS_CHECK_INTERVAL_SECONDS,
    NEWS_WEBSITE_URL,
    parse_news_feed,
    retrieve_news_feed,
    unread_news,
)
from cocoa_native_menus import menu_item


AUTO_CHECK_KEY = "org.mycamino.news.auto-check.v1"
LAST_CHECK_KEY = "org.mycamino.news.last-check.v1"
LAST_ATTEMPT_KEY = "org.mycamino.news.last-attempt.v1"
KNOWN_IDS_KEY = "org.mycamino.news.known-ids.v1"
READ_IDS_KEY = "org.mycamino.news.read-ids.v1"
CACHED_FEED_KEY = "org.mycamino.news.cached-feed.v1"


class ApplicationNewsController(NSObject):
    def init(self):
        self = objc.super(ApplicationNewsController, self).init()
        if self is not None:
            self.menu_items = []
            self.toggle_items = []
            self.window = None
            self.text_view = None
            self.status_field = None
            self.checking = False
            self.last_error = ""
            defaults = NSUserDefaults.standardUserDefaults()
            cached_feed = defaults.stringForKey_(CACHED_FEED_KEY)
            try:
                self.items = parse_news_feed(str(cached_feed)) if cached_feed else ()
            except (TypeError, ValueError):
                self.items = ()
            if defaults.objectForKey_(AUTO_CHECK_KEY) is None:
                defaults.setBool_forKey_(True, AUTO_CHECK_KEY)
        return self

    @objc.python_method
    def attach_to_menu(self, application_menu):
        application_menu.addItem_(NSMenuItem.separatorItem())
        news_item = menu_item("News and Updates", "showNews:", self)
        check_item = menu_item("Check for News and Updates…", "checkNow:", self)
        toggle_item = menu_item("Automatically Check for News", "toggleAutomaticChecks:", self)
        application_menu.addItem_(news_item)
        application_menu.addItem_(check_item)
        application_menu.addItem_(toggle_item)
        self.menu_items.append(news_item)
        self.toggle_items.append(toggle_item)
        self._update_menu_items()
        self.check_if_due()

    @objc.python_method
    def _stored_ids(self, key):
        raw = NSUserDefaults.standardUserDefaults().stringForKey_(key)
        if not raw:
            return set()
        try:
            values = json.loads(str(raw))
        except (TypeError, ValueError):
            return set()
        return {str(value) for value in values if isinstance(value, str)}

    @objc.python_method
    def _store_ids(self, key, values):
        bounded = sorted({str(value) for value in values})[-500:]
        NSUserDefaults.standardUserDefaults().setObject_forKey_(json.dumps(bounded), key)

    @objc.python_method
    def _automatic_enabled(self):
        return bool(NSUserDefaults.standardUserDefaults().boolForKey_(AUTO_CHECK_KEY))

    @objc.python_method
    def _update_menu_items(self):
        count = len(unread_news(self.items, self._stored_ids(READ_IDS_KEY)))
        title = f"News and Updates ({count} unread)" if count else "News and Updates"
        for item in self.menu_items:
            item.setTitle_(title)
        state = NSOnState if self._automatic_enabled() else NSOffState
        for item in self.toggle_items:
            item.setState_(state)
        try:
            NSApp().dockTile().setBadgeLabel_(str(count) if count else None)
        except Exception:
            pass

    @objc.python_method
    def check_if_due(self):
        defaults = NSUserDefaults.standardUserDefaults()
        if not self._automatic_enabled():
            return
        last_attempt = float(defaults.doubleForKey_(LAST_ATTEMPT_KEY))
        if not last_attempt:
            last_attempt = float(defaults.doubleForKey_(LAST_CHECK_KEY))
        if time.time() - last_attempt >= NEWS_CHECK_INTERVAL_SECONDS:
            self._start_check(False)

    @objc.python_method
    def _start_check(self, manual):
        if self.checking:
            return
        self.checking = True
        NSUserDefaults.standardUserDefaults().setDouble_forKey_(
            time.time(), LAST_ATTEMPT_KEY
        )
        self._refresh_window_content()

        def worker():
            try:
                result = (retrieve_news_feed(), "", bool(manual))
            except Exception as exc:
                result = ((), str(exc), bool(manual))
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "applyNewsResult:", result, False
            )

        threading.Thread(target=worker, name="mycamino-news", daemon=True).start()

    @objc.IBAction
    def checkNow_(self, _sender):
        self._start_check(True)

    @objc.IBAction
    def toggleAutomaticChecks_(self, _sender):
        defaults = NSUserDefaults.standardUserDefaults()
        defaults.setBool_forKey_(not self._automatic_enabled(), AUTO_CHECK_KEY)
        self._update_menu_items()
        self.check_if_due()

    def applyNewsResult_(self, result):
        items, error, manual = result
        self.checking = False
        if error:
            self.last_error = str(error)
            self._refresh_window_content()
            if manual:
                self._show_window()
            return
        defaults = NSUserDefaults.standardUserDefaults()
        self.last_error = ""
        defaults.setDouble_forKey_(time.time(), LAST_CHECK_KEY)
        defaults.setObject_forKey_(
            json.dumps(
                {
                    "format_version": 1,
                    "items": [
                        {
                            "id": item.identifier,
                            "title": item.title,
                            "summary": item.summary,
                            "published_at": item.published_at,
                            "url": item.url,
                            "kind": item.kind,
                            "app_version": item.app_version,
                        }
                        for item in items
                    ],
                }
            ),
            CACHED_FEED_KEY,
        )
        known = self._stored_ids(KNOWN_IDS_KEY)
        newly_arrived = [item for item in items if item.identifier not in known]
        self.items = tuple(items)
        self._store_ids(KNOWN_IDS_KEY, known | {item.identifier for item in items})
        self._update_menu_items()
        self._refresh_window_content()
        if newly_arrived:
            latest = newly_arrived[0]
            alert = NSAlert.alloc().init()
            alert.setMessageText_(latest.title)
            alert.setInformativeText_(latest.summary)
            alert.addButtonWithTitle_("Read News")
            alert.addButtonWithTitle_("Later")
            if int(alert.runModal()) == 1000:
                self.showNews_(None)
        elif manual:
            self._show_window()

    @objc.python_method
    def _connection_status(self):
        timestamp = float(
            NSUserDefaults.standardUserDefaults().doubleForKey_(LAST_CHECK_KEY)
        )
        if timestamp:
            connected = time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))
            suffix = f"Last successful connection: {connected}"
        else:
            suffix = "No successful connection recorded yet"
        if self.checking:
            return f"Checking quietly in the background · {suffix}"
        if self.last_error:
            return f"Website currently unavailable · {suffix}"
        return suffix

    @objc.python_method
    def _news_text(self):
        text = []
        for item in self.items:
            label = "UPDATE" if item.kind == "update" else "NEWS"
            version = f" · myCamino {item.app_version}" if item.app_version else ""
            text.append(
                f"{label}{version}\n{item.title}\n{item.published_at[:10]}\n\n"
                f"{item.summary}\n\n{item.url}\n"
            )
        return "\n\n".join(text) if text else "No published news or updates are available."

    @objc.python_method
    def _refresh_window_content(self):
        if self.text_view is not None:
            self.text_view.setString_(self._news_text())
        if self.status_field is not None:
            self.status_field.setStringValue_(self._connection_status())

    @objc.python_method
    def _show_window(self):
        if self.window is None:
            self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(220, 160, 700, 560),
                NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable,
                NSBackingStoreBuffered,
                False,
            )
            self.window.setReleasedWhenClosed_(False)
            self.window.setTitle_("myCamino News and Updates")
            content = self.window.contentView()
            bounds = content.bounds()
            scroll = NSScrollView.alloc().initWithFrame_(
                NSMakeRect(0, 28, bounds.size.width, max(0, bounds.size.height - 28))
            )
            scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            scroll.setHasVerticalScroller_(True)
            self.text_view = NSTextView.alloc().initWithFrame_(scroll.contentView().bounds())
            self.text_view.setEditable_(False)
            self.text_view.setSelectable_(True)
            self.text_view.setFont_(NSFont.systemFontOfSize_(14.0))
            if self.text_view.respondsToSelector_("setAutomaticLinkDetectionEnabled:"):
                self.text_view.setAutomaticLinkDetectionEnabled_(True)
            self.text_view.setVerticallyResizable_(True)
            self.text_view.setHorizontallyResizable_(False)
            self.text_view.setAutoresizingMask_(NSViewWidthSizable)
            self.text_view.setMinSize_(NSMakeSize(0, 0))
            self.text_view.setMaxSize_(NSMakeSize(1.0e7, 1.0e7))
            self.text_view.textContainer().setWidthTracksTextView_(True)
            scroll.setDocumentView_(self.text_view)
            content.addSubview_(scroll)
            self.status_field = NSTextField.alloc().initWithFrame_(
                NSMakeRect(12, 5, max(0, bounds.size.width - 24), 17)
            )
            self.status_field.setEditable_(False)
            self.status_field.setSelectable_(False)
            self.status_field.setBordered_(False)
            self.status_field.setDrawsBackground_(False)
            self.status_field.setFont_(NSFont.systemFontOfSize_(11.0))
            self.status_field.setTextColor_(NSColor.secondaryLabelColor())
            self.status_field.setAutoresizingMask_(NSViewWidthSizable | NSViewMaxYMargin)
            content.addSubview_(self.status_field)
        self._refresh_window_content()
        self.window.makeKeyAndOrderFront_(None)
        self.window.orderFrontRegardless()

    @objc.IBAction
    def showNews_(self, _sender):
        self._show_window()
        read = self._stored_ids(READ_IDS_KEY)
        self._store_ids(READ_IDS_KEY, read | {item.identifier for item in self.items})
        self._update_menu_items()
        if not self.items:
            self._start_check(True)


_shared_controller = None


def install_application_news(application_menu):
    """Attach the process-wide news controller to a native application menu."""
    global _shared_controller
    if _shared_controller is None:
        _shared_controller = ApplicationNewsController.alloc().init()
    _shared_controller.attach_to_menu(application_menu)
    return _shared_controller
