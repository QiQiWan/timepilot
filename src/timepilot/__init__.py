"""TimePilot public API; load the full SDK only when a client is requested.

Building the offline Perspective frontend must not need forecasting dependencies.
"""
__version__ = "0.40.6"
__all__ = ["TimePilot", "__version__"]


def __getattr__(name: str):
    if name == 'TimePilot':
        from timepilot.sdk import TimePilot
        return TimePilot
    raise AttributeError(f'module timepilot has no attribute {name!r}')
