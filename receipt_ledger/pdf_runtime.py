"""PDFium calls share a process-wide mutex (PDFium itself is not thread safe)."""
from functools import wraps
import threading

PDF_LOCK = threading.RLock()


def serialized(function):
    @wraps(function)
    def protected(*args, **kwargs):
        with PDF_LOCK:
            return function(*args, **kwargs)
    return protected
