"""Screen registry for Claude Glean TUI."""

from .home import HomeScreen
from .usage import UsageScreen
from .components import ComponentsScreen
from .xray import XrayScreen

SCREEN_NAMES = ["Home", "Usage", "Components", "X-ray"]

__all__ = [
    "HomeScreen",
    "UsageScreen",
    "ComponentsScreen",
    "XrayScreen",
    "SCREEN_NAMES",
]
