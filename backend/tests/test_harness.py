import os


def test_harness_uses_safe_test_environment():
    assert os.environ["APP_ENV"] == "test"
    assert os.environ["PUBLIC_WRITES_ENABLED"] == "false"
    assert os.environ["RELATIONSHIP_WRITES_ENABLED"] == "false"
    assert os.environ["NORMALIZED_READS_ENABLED"] == "false"
