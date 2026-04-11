"""Screen registry for Claude Glean TUI."""

from .home import HomeScreen
from .components import ComponentsScreen
from .xray import XrayScreen

SCREEN_NAMES = ["Home", "Components", "X-ray"]

__all__ = [
    "HomeScreen",
    "ComponentsScreen",
    "XrayScreen",
    "SCREEN_NAMES",
]
