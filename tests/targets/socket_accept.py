"""Target process with a thread blocked in ``socket.accept()``.

Run standalone with ``python tests/targets/socket_accept.py``. Prints
``READY`` to stdout once the listening socket is bound and the acceptor
thread is running.

The acceptor thread blocks inside ``accept()`` on a loopback socket that
nobody connects to, while the main thread loops on short sleeps so
``sys.remote_exec`` still finds a safe execution point. Intended for
verifying that pidprobe reports both a thread parked in a socket syscall and
the listening descriptor itself.
"""

from __future__ import annotations

import socket
import threading
import time

HOST = "127.0.0.1"
EPHEMERAL_PORT = 0
BACKLOG = 1


def _accept_forever(server: socket.socket) -> None:
    while True:
        connection, _ = server.accept()
        connection.close()


def _run() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((HOST, EPHEMERAL_PORT))
    server.listen(BACKLOG)

    acceptor = threading.Thread(target=_accept_forever, args=(server,), daemon=True)
    acceptor.start()

    print("READY", flush=True)
    while True:
        time.sleep(0.05)


if __name__ == "__main__":
    _run()
