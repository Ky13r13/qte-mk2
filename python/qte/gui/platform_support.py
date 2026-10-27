"""Fail-closed host capabilities; platform selection is never browser input."""
import sys


def full_gui_supported() -> bool:
    return sys.platform.startswith("linux")


LIMITED_REASON = (
    "This host currently supports Library and offline Connections only. "
    "Artifact storage and supervised research jobs require the Linux backend; "
    "native Windows parity is still in development."
)
