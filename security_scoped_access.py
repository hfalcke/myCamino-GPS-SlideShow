"""Persistent Adventure-directory access for sandboxed Store builds.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from contextlib import contextmanager

from runtime_distribution import is_app_store_build


BOOKMARK_DEFAULTS_KEY = "myCaminoSecurityScopedAdventureBookmarksV1"


class SecurityScopedAccessManager:
    """Own one balanced security scope for the active Adventure directory."""

    def __init__(self, defaults=None):
        if defaults is None:
            from Foundation import NSUserDefaults

            defaults = NSUserDefaults.standardUserDefaults()
        self.defaults = defaults
        self.active_url = None
        self.active_path: Path | None = None

    def _bookmarks(self) -> dict[str, str]:
        value = self.defaults.dictionaryForKey_(BOOKMARK_DEFAULTS_KEY)
        return {str(key): str(data) for key, data in dict(value or {}).items()}

    def _save_bookmark(self, path: Path, data) -> None:
        bookmarks = self._bookmarks()
        bookmarks[str(path)] = base64.b64encode(bytes(data)).decode("ascii")
        self.defaults.setObject_forKey_(bookmarks, BOOKMARK_DEFAULTS_KEY)

    def encoded_bookmark(self, path: str | Path) -> str:
        path = Path(path).expanduser().resolve(strict=False)
        return self._bookmarks().get(str(path), "")

    def stop(self) -> None:
        if self.active_url is not None:
            try:
                self.active_url.stopAccessingSecurityScopedResource()
            except Exception:
                pass
        self.active_url = None
        self.active_path = None

    def _start(self, url, path: Path) -> bool:
        self.stop()
        try:
            if not bool(url.startAccessingSecurityScopedResource()):
                return False
        except Exception:
            return False
        self.active_url = url
        self.active_path = path
        return True

    def restore(self, path: str | Path) -> bool:
        path = Path(path).expanduser().resolve(strict=False)
        if not is_app_store_build():
            self.active_path = path
            return True
        encoded = self._bookmarks().get(str(path))
        if not encoded:
            return False
        from Foundation import NSData, NSURL, NSURLBookmarkResolutionWithSecurityScope

        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            return False
        data = NSData.dataWithBytes_length_(raw, len(raw))
        try:
            result = NSURL.alloc().initByResolvingBookmarkData_options_relativeToURL_bookmarkDataIsStale_error_(
                data, NSURLBookmarkResolutionWithSecurityScope, None, None, None
            )
            if isinstance(result, tuple):
                url = result[0]
                stale = bool(result[1]) if len(result) > 1 else False
            else:
                url, stale = result, False
        except Exception:
            return False
        if url is None or not self._start(url, Path(str(url.path())).resolve(strict=False)):
            return False
        if stale:
            self.remember_url(url)
        return True

    def remember_url(self, url) -> bool:
        path = Path(str(url.path())).expanduser().resolve(strict=False)
        if not is_app_store_build():
            self.active_path = path
            return True
        from Foundation import NSURLBookmarkCreationWithSecurityScope

        try:
            result = url.bookmarkDataWithOptions_includingResourceValuesForKeys_relativeToURL_error_(
                NSURLBookmarkCreationWithSecurityScope, None, None, None
            )
            data = result[0] if isinstance(result, tuple) else result
        except Exception:
            return False
        if data is None:
            return False
        self._save_bookmark(path, data)
        return self._start(url, path)

    def activate_directory(self, path: str | Path) -> bool:
        path = Path(path).expanduser().resolve(strict=False)
        if self.active_path == path:
            return True
        if self.restore(path):
            return True
        from Foundation import NSURL

        return self.remember_url(NSURL.fileURLWithPath_(str(path)))


@contextmanager
def temporary_directory_access(path, defaults=None):
    """Inspect a destination without releasing the active project's scope."""
    manager = SecurityScopedAccessManager(defaults)
    try:
        yield manager.activate_directory(path)
    finally:
        manager.stop()


_helper_access_manager = None


def activate_helper_project_access(environment=None) -> bool:
    """Resolve a bookmark passed by the main application and retain its scope."""
    import os

    global _helper_access_manager
    source = os.environ if environment is None else environment
    encoded = str(source.get("MYCAMINO_PROJECT_BOOKMARK", "")).strip()
    if not encoded:
        return not is_app_store_build(source)
    from Foundation import NSData, NSURL, NSURLBookmarkResolutionWithSecurityScope

    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return False
    data = NSData.dataWithBytes_length_(raw, len(raw))
    try:
        result = NSURL.alloc().initByResolvingBookmarkData_options_relativeToURL_bookmarkDataIsStale_error_(
            data, NSURLBookmarkResolutionWithSecurityScope, None, None, None
        )
        url = result[0] if isinstance(result, tuple) else result
    except Exception:
        return False
    if url is None:
        return False
    manager = SecurityScopedAccessManager()
    if not manager._start(url, Path(str(url.path())).resolve(strict=False)):
        return False
    _helper_access_manager = manager
    return True
