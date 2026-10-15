"""Real fixture loaders and HA custom integration support."""
import json
from pathlib import Path
import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


def fixture_json(name):
    return json.loads((Path(__file__).parent / "fixtures" / name).read_text())


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
