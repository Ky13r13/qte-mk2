"""Fingerprint the actual loaded extension and Python source, including edits."""
import hashlib
from pathlib import Path
from qte import _core

def source_identity() -> str:
    digest = hashlib.sha256()
    root = Path(__file__).parent
    for path in sorted(root.rglob('*.py')):
        digest.update(path.relative_to(root).as_posix().encode() + b'\0')
        digest.update(path.read_bytes())
    digest.update(Path(_core.__file__).read_bytes())
    return 'sha256:' + digest.hexdigest()
