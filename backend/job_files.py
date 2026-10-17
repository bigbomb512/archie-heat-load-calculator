"""A cheap signature of a job's saved files, for caching values worked out from them."""

from pathlib import Path


def signature(root, extra_files=()):
    """Size, modification time and inode of every JSON file in the job folder, plus any extra files.

    Saves go through an atomic replace, so any saved change alters the signature. Statting a 38-page job's
    ~300 JSON files takes about 15 ms, against seconds to re-read and fingerprint them.
    """
    files = sorted(Path(root).rglob("*.json")) + [Path(path) for path in extra_files if path]
    result = []
    for path in files:
        try:
            stat = path.stat()
            result.append((str(path), stat.st_mtime_ns, stat.st_size, getattr(stat, "st_ino", 0)))
        except OSError:
            result.append((str(path), None))
    return tuple(result)
