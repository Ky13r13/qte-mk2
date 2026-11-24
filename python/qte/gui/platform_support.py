"""Fail-closed host capabilities; platform selection is never browser input."""
import sys


def full_gui_supported() -> bool:
    return sys.platform.startswith("linux")


LIMITED_REASON = (
    "Library, offline Connections and the Gamma calculator are available. "
    "Research, Data and supervised jobs currently require the Linux backend."
)
