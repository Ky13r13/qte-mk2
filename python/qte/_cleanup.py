"""Report secondary cleanup failures without disclosing paths or broker messages."""
import logging
import os


def report_os_error(operation: str, error: OSError) -> None:
    logging.getLogger(__name__).warning("%s failed (errno=%s)", operation, error.errno)


def close_fd(fd: int) -> None:
    """Release an owned descriptor once; cleanup must continue for other descriptors."""
    if fd < 0:
        return
    try:
        os.close(fd)
    except OSError as error:
        # Retrying close can affect another owner's newly allocated descriptor.
        report_os_error("Descriptor close", error)
