"""Shared beta-notice settings for the myCamino macOS applications."""

BETA_NOTICE_VERSION = 1
BETA_NOTICE_PREFERENCE_KEY = "myCaminoBetaNoticeVersion"
BUG_REPORT_URL = "https://github.com/hfalcke/myCamino-GPS-SlideShow/issues/new?template=01-bug.yml"
FEATURE_REQUEST_URL = "https://github.com/hfalcke/myCamino-GPS-SlideShow/issues/new?template=02-feature-request.yml"


def beta_notice_should_be_shown(stored_version):
    """Return whether the current beta notice has not yet been acknowledged."""
    try:
        return int(stored_version) < BETA_NOTICE_VERSION
    except (TypeError, ValueError):
        return True
