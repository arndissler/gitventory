# Import all adapter subpackages to trigger @register_adapter decoration.
# Add new adapters here as they are implemented.
from gitventory.adapters import static_yaml  # noqa: F401

# GitHub adapter — imported only when PyGithub is available
try:
    from gitventory.adapters import github  # noqa: F401
except ImportError:
    pass
