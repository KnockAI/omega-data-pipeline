"""Per-source/geography process locks; stale locks release with process exit."""
import fcntl, os
from contextlib import contextmanager

@contextmanager
def source_lock(source: str, geography: str, root: str = "./data/locks"):
    os.makedirs(root, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in f"{source}-{geography}")
    with open(os.path.join(root, safe + ".lock"), "w") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try: yield
        finally: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
