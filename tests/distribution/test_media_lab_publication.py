from pathlib import Path

from watcherobot.distribution.check import check_application
from watcherobot.distribution.submit import _validate_submission_metadata


def test_bundled_ui_example_is_ready_for_catalog_submission():
    root = Path(__file__).resolve().parents[2] / "examples" / "sdk_media_lab"
    application = check_application(root)
    _validate_submission_metadata(application)
    assert application.supported_host_platforms == ("windows",)
