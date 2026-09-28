"""Basic sanity test for package scaffolding."""

import agentkit


def test_package_scaffold() -> None:
    """Verify that agentkit package is discoverable and importable."""
    assert agentkit is not None
