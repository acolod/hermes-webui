"""Automatic isolation activation for every update-routing test module."""

import os

from downstream_test_isolation import _audit_active


def test_update_routing_test_activates_isolation_automatically():
    assert os.environ.get("HERMES_DOWNSTREAM_TEST_GUARD_ACTIVE") == "1"
    assert _audit_active() is True
