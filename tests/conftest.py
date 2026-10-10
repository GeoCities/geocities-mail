import pytest
from geomail.backends.demo import DemoBackend


@pytest.fixture
def demo(tmp_path):
    return DemoBackend(state_path=str(tmp_path / "demo.json"))
