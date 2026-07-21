"""Shared beta-notice settings for the myCamino macOS applications."""

BETA_NOTICE_VERSION = 1
BETA_NOTICE_PREFERENCE_KEY = "myCaminoBetaNoticeVersion"
BUG_REPORT_URL = "https://mycamino.heinofalcke.de/contact/"


def beta_notice_should_be_shown(stored_version):
    """Return whether the current beta notice has not yet been acknowledged."""
    try:
        return int(stored_version) < BETA_NOTICE_VERSION
    except (TypeError, ValueError):
        return True
