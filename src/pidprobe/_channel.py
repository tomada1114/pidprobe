"""Return channel that carries results from the target back to pidprobe.

The prober owns the channel and starts listening *before* the injected script
exists, so the target always has somewhere to write to. Two one-shot
transports are available: :class:`UnixSocketChannel` (default), an ``AF_UNIX``
stream socket placed in a directory short enough for the 104-byte
``sun_path`` limit macOS enforces, and :class:`TempFileChannel`, a
write-then-rename file the prober polls for when no socket can be bound
(sandbox, no ``AF_UNIX``, path too long).

Nothing here may block indefinitely: every step consults the shared
:class:`~pidprobe._deadline.Deadline`.
"""

from __future__ import annotations

import errno
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ._deadline import Deadline
from ._envelope import parse_envelope
from ._errors import AttachError, ChannelError
from ._inject import ChannelKind, ChannelSpec, write_injection_script

if TYPE_CHECKING:
    from ._envelope import Envelope

DEFAULT_TIMEOUT_SECONDS = 5.0
"""Hard budget for one probe: attach, target execution and read-back."""

# macOS caps sockaddr_un.sun_path at 104 bytes (including the terminator);
# Linux allows 108. Use the stricter limit so one code path fits both.
_MAX_SUN_PATH_BYTES = 104
_TEMPDIR_PREFIX = "pidprobe-"
# mkdtemp() appends an 8-character random suffix to the prefix.
_MKDTEMP_SUFFIX_BYTES = 8
_SOCKET_FILENAME = "s.sock"
_RESULT_FILENAME = "result.json"
_RECV_CHUNK_BYTES = 65536
_POLL_INTERVAL_SECONDS = 0.005
_LISTEN_BACKLOG = 1


class ReturnChannel(Protocol):
    """One-shot path from the injected script back to the prober."""

    @property
    def kind(self) -> ChannelKind:
        """Transport the injected script must use."""
        ...

    @property
    def path(self) -> str:
        """Filesystem path the injected script writes to."""
        ...

    def receive(self, deadline: Deadline) -> str:
        """Wait for the envelope and return it as raw JSON text."""
        ...

    def close(self) -> None:
        """Release the socket or file and remove the temporary directory."""
        ...


def _short_tempdir() -> Path:
    """Create a temporary directory short enough to hold an ``AF_UNIX`` path.

    Returns:
        The created directory.

    Raises:
        OSError: If no candidate base yields a path within ``sun_path``.
    """
    seen: set[str] = set()
    for base in (tempfile.gettempdir(), "/tmp"):  # noqa: S108 -- length fallback only
        if base in seen or not Path(base).is_dir():
            continue
        seen.add(base)
        suffix = "x" * _MKDTEMP_SUFFIX_BYTES
        projected = Path(base) / f"{_TEMPDIR_PREFIX}{suffix}" / _SOCKET_FILENAME
        if len(str(projected).encode()) < _MAX_SUN_PATH_BYTES:
            return Path(tempfile.mkdtemp(prefix=_TEMPDIR_PREFIX, dir=base))
    message = "no temporary directory yields a short enough AF_UNIX path"
    raise OSError(errno.ENAMETOOLONG, message)


class UnixSocketChannel:
    """Return channel backed by a listening ``AF_UNIX`` stream socket."""

    def __init__(self) -> None:
        """Bind and listen so the target can connect as soon as it runs.

        Raises:
            OSError: If ``AF_UNIX`` is unavailable, no short enough path
                exists, or the socket cannot be bound; callers fall back to
                :class:`TempFileChannel`.
        """
        if not hasattr(socket, "AF_UNIX"):
            message = "AF_UNIX sockets are not supported on this platform"
            raise OSError(errno.EAFNOSUPPORT, message)
        self._directory = _short_tempdir()
        self._path = self._directory / _SOCKET_FILENAME
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._server.bind(str(self._path))
            self._server.listen(_LISTEN_BACKLOG)
        except OSError:
            self.close()
            raise

    @property
    def kind(self) -> ChannelKind:
        """Transport the injected script must use."""
        return ChannelKind.UNIX_SOCKET

    @property
    def path(self) -> str:
        """Path of the listening socket."""
        return str(self._path)

    def receive(self, deadline: Deadline) -> str:
        """Accept one connection and read the envelope it sends.

        Args:
            deadline: Shared budget for accepting and reading.

        Returns:
            The raw JSON text the target sent.

        Raises:
            ProbeTimeoutError: If the target does not connect, or stops
                sending, before the deadline expires.
            ChannelError: If the connection closes without any data.
        """
        conn = self._accept(deadline)
        chunks: list[bytes] = []
        with conn:
            while True:
                if deadline.is_expired:
                    raise deadline.timeout_error()
                conn.settimeout(deadline.remaining)
                try:
                    data = conn.recv(_RECV_CHUNK_BYTES)
                except TimeoutError as exc:
                    raise deadline.timeout_error() from exc
                if not data:
                    break
                chunks.append(data)
        if not chunks:
            message = "target connected to the return channel but sent nothing"
            raise ChannelError(message)
        return b"".join(chunks).decode("utf-8")

    def _accept(self, deadline: Deadline) -> socket.socket:
        """Wait for the target to connect within *deadline*."""
        if deadline.is_expired:
            raise deadline.timeout_error()
        self._server.settimeout(deadline.remaining)
        try:
            conn, _ = self._server.accept()
        except TimeoutError as exc:
            raise deadline.timeout_error() from exc
        return conn

    def close(self) -> None:
        """Close the listening socket and remove its directory."""
        server = getattr(self, "_server", None)
        if server is not None:
            server.close()
        shutil.rmtree(self._directory, ignore_errors=True)


class TempFileChannel:
    """Return channel backed by a result file the prober polls for.

    The injected script writes to ``<path>.tmp`` and renames it, so the
    prober either sees nothing or a complete envelope.
    """

    def __init__(self) -> None:
        """Create the directory the target will drop its result into."""
        self._directory = Path(tempfile.mkdtemp(prefix=_TEMPDIR_PREFIX))
        self._path = self._directory / _RESULT_FILENAME

    @property
    def kind(self) -> ChannelKind:
        """Transport the injected script must use."""
        return ChannelKind.TEMPFILE

    @property
    def path(self) -> str:
        """Path of the result file the target renames into place."""
        return str(self._path)

    def receive(self, deadline: Deadline) -> str:
        """Poll for the result file until it appears or the budget is spent.

        Args:
            deadline: Shared budget for the whole wait.

        Returns:
            The raw JSON text the target wrote.

        Raises:
            ProbeTimeoutError: If no complete result appears in time.
        """
        while True:
            try:
                content = self._path.read_text(encoding="utf-8")
            except FileNotFoundError:
                content = ""
            if content:
                return content
            if deadline.is_expired:
                raise deadline.timeout_error()
            time.sleep(min(_POLL_INTERVAL_SECONDS, max(deadline.remaining, 0.0)))

    def close(self) -> None:
        """Remove the result file and its directory."""
        shutil.rmtree(self._directory, ignore_errors=True)


def open_channel(*, allow_socket: bool = True) -> ReturnChannel:
    """Open the best available return channel.

    Args:
        allow_socket: Set to ``False`` to skip ``AF_UNIX`` entirely, which is
            mainly useful for exercising the fallback.

    Returns:
        A listening :class:`UnixSocketChannel`, or a :class:`TempFileChannel`
        when no socket could be bound.
    """
    if allow_socket:
        try:
            return UnixSocketChannel()
        except OSError:
            # Sandboxes, exotic filesystems and very deep temp directories
            # all surface as OSError here; the file fallback works anywhere.
            pass
    return TempFileChannel()


def _remote_exec(pid: int, script_path: Path) -> None:
    """Run *script_path* inside the target, translating attach failures.

    Args:
        pid: Target process id.
        script_path: Script generated by :mod:`pidprobe._inject`.

    Raises:
        AttachError: If the interpreter refuses the injection.
    """
    try:
        sys.remote_exec(pid, str(script_path))
    except (OSError, RuntimeError, ValueError) as exc:
        raise AttachError.from_cause(pid, exc) from exc


def execute_in_target(
    pid: int,
    collector_source: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    allow_socket: bool = True,
) -> Envelope:
    """Run collector source inside a live process and return its envelope.

    Opens the return channel first, then generates and injects the script, so
    the target can never answer before the prober is listening.

    Args:
        pid: Target process id.
        collector_source: Python source executed inside the target; see
            :func:`pidprobe._inject.build_injection_script` for the contract.
        timeout_seconds: Hard budget covering injection and read-back.
        allow_socket: Set to ``False`` to force the tempfile fallback.

    Returns:
        The validated envelope; ``status`` is ``"error"`` when the collector
        raised inside the target.

    Raises:
        AttachError: If the target refuses the injection.
        ProbeTimeoutError: If the target never answers in time.
        ChannelError: If the answer does not match the envelope contract.
    """
    deadline = Deadline.start(timeout_seconds, pid)
    channel = open_channel(allow_socket=allow_socket)
    try:
        spec = ChannelSpec(
            kind=channel.kind,
            path=channel.path,
            timeout_seconds=timeout_seconds,
        )
        script_dir = Path(tempfile.mkdtemp(prefix=_TEMPDIR_PREFIX))
        try:
            # The target reads the script itself, so it has to survive until
            # the answer arrives -- or until the budget is spent, after which
            # a target that wakes up late simply finds nothing to run.
            script_path = write_injection_script(collector_source, spec, script_dir)
            _remote_exec(pid, script_path)
            raw = channel.receive(deadline)
        finally:
            shutil.rmtree(script_dir, ignore_errors=True)
    finally:
        channel.close()
    return parse_envelope(raw)
