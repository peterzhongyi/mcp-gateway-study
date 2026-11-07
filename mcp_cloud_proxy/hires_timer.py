"""
Windows timer-resolution control.

The default Windows scheduler tick is ~15.6 ms, so `asyncio.sleep(0.002)` actually sleeps
~15 ms. Every process in the testbed (load generator/gateway, upstream HTTP server, stdio
server) must request 1 ms resolution, because on Windows 10 2004+ the setting is per-process.
"""

import sys

_enabled = False


def enable_high_res_timer() -> bool:
    global _enabled
    if _enabled or sys.platform != "win32":
        return _enabled
    import ctypes

    _enabled = ctypes.WinDLL("winmm").timeBeginPeriod(1) == 0
    return _enabled
