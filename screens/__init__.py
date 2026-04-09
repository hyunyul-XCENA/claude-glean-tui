"""Screen registry for Claude Glean TUI.

Each screen is a subclass of BaseScreen that renders a full-screen view
and handles its own key events.
"""

from .home import HomeScreen
from .usage import UsageScreen
from .components import ComponentsScreen
from .xray import XrayScreen
from .vault import VaultScreen

SCREEN_NAMES = ["Home", "Usage", "Components", "X-ray", "Vault"]

__all__ = [
    "HomeScreen",
    "UsageScreen",
    "ComponentsScreen",
    "XrayScreen",
    "VaultScreen",
    "SCREEN_NAMES",
]
