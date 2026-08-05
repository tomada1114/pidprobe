"""Open file descriptors and sockets of the target process."""

from __future__ import annotations

from ._base import Collector

# The fd table is read through the process' own view of it (/proc/self/fd on
# Linux, /dev/fd elsewhere) because the collector runs inside the target: no
# ptrace, no psutil, and no permission problem.
_SOURCE = '''
import os
import stat
import sys

LINUX_FD_DIR = "/proc/self/fd"
BSD_FD_DIR = "/dev/fd"

KIND_BY_FORMAT = {
    stat.S_IFSOCK: "socket",
    stat.S_IFIFO: "fifo",
    stat.S_IFREG: "file",
    stat.S_IFDIR: "directory",
    stat.S_IFCHR: "char",
    stat.S_IFBLK: "block",
    stat.S_IFLNK: "symlink",
}


def fd_directory():
    if sys.platform.startswith("linux") and os.path.isdir(LINUX_FD_DIR):
        return LINUX_FD_DIR
    if os.path.isdir(BSD_FD_DIR):
        return BSD_FD_DIR
    return None


def address(sock, method_name):
    try:
        value = getattr(sock, method_name)()
    except OSError:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, tuple):
        return [
            item.decode("utf-8", "replace") if isinstance(item, bytes) else item
            for item in value
        ]
    return value


def socket_details(fd):
    """Describe a socket fd, or None when it cannot be done safely.

    The address family is auto-detected from SO_DOMAIN, which only Linux
    provides; guessing it elsewhere would decode addresses as the wrong
    family. Wrapping the fd is also skipped when a default socket timeout is
    set, because constructing the socket object would then switch the shared
    open file description to non-blocking and change the target's behaviour.
    """
    import socket

    if not hasattr(socket, "SO_DOMAIN") or socket.getdefaulttimeout() is not None:
        return None
    try:
        duplicate = os.dup(fd)
    except OSError:
        return None
    try:
        sock = socket.socket(fileno=duplicate)
    except Exception:
        # Enrichment is a bonus; losing it must never cost the fd entry.
        os.close(duplicate)
        return None
    try:
        return {
            "family": getattr(sock.family, "name", None) or str(sock.family),
            "type": getattr(sock.type, "name", None) or str(sock.type),
            "laddr": address(sock, "getsockname"),
            "raddr": address(sock, "getpeername"),
        }
    except Exception:
        return None
    finally:
        # Closes the duplicate only; the target keeps its own descriptor.
        sock.close()


def describe(directory, name):
    fd = int(name)
    # The descriptor os.listdir() used is already closed by now, so fstat
    # fails for it and it drops out of the listing.
    info = os.fstat(fd)
    kind = KIND_BY_FORMAT.get(stat.S_IFMT(info.st_mode), "unknown")
    try:
        target = os.readlink(directory + "/" + name)
    except OSError:
        target = None
    entry = {
        "fd": fd,
        "kind": kind,
        "target": target,
        "inode": info.st_ino,
        "is_tty": os.isatty(fd),
    }
    if kind == "file":
        entry["size"] = info.st_size
    if kind == "socket":
        entry["socket"] = socket_details(fd)
    return entry


directory = fd_directory()
if directory is None:
    data = {"supported": False, "source": None, "count": 0, "descriptors": []}
else:
    descriptors = []
    for entry_name in os.listdir(directory):
        try:
            descriptors.append(describe(directory, entry_name))
        except (ValueError, OSError):
            continue
    descriptors.sort(key=lambda entry: entry["fd"])
    data = {
        "supported": True,
        "source": directory,
        "count": len(descriptors),
        "descriptors": descriptors,
    }
'''

FDS_COLLECTOR = Collector(
    name="fds",
    source=_SOURCE,
    description="Open file descriptors and sockets, with kind and target",
)
