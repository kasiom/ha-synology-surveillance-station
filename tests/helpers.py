"""Load pure integration modules without installing Home Assistant."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

PKG_DIR = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "synology_surveillance_station"
)


def _ensure_aiohttp_stub() -> None:
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        module = types.ModuleType("aiohttp")

        class ClientError(Exception):
            pass

        module.ClientError = ClientError
        module.ClientSession = object
        module.ClientResponse = object
        module.ClientTimeout = lambda **kwargs: kwargs
        module.web = types.SimpleNamespace(Request=object)
        sys.modules["aiohttp"] = module


def load_module(name: str):
    """Load one module while allowing its relative imports."""
    _ensure_aiohttp_stub()
    package_name = "synology_surveillance_station"
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(PKG_DIR)]
        sys.modules[package_name] = package
    full_name = f"{package_name}.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(full_name, PKG_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module
