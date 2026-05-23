"""
Single-instance enforcement using QLocalServer / QLocalSocket.

The first instance starts a named local server. Subsequent instances try to
connect to that server, which is enough to signal "I'm trying to start".
The existing instance, on receiving the connection, raises and focuses its
window. The duplicate instance then exits.

Cross-machine safe: each Windows user gets their own server name.
"""
import getpass
from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtNetwork import QLocalServer, QLocalSocket


def _server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "default"
    return f"PyMail-singleinstance-{user}"


class SingleInstance(QObject):
    """Manages the named server for the running instance."""
    another_instance_started = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._server: QLocalServer | None = None

    def acquire(self) -> bool:
        """Try to become the primary instance.

        Returns True if successful (we're the only/first instance).
        Returns False if another instance is running (and notifies it).
        """
        name = _server_name()

        # Probe: try to connect to existing server. The mere connection is
        # enough — we don't need to send any payload.
        sock = QLocalSocket()
        sock.connectToServer(name)
        if sock.waitForConnected(500):
            # Give the server a moment to register the connection before
            # we close it. Without this, the server may receive the
            # connect+disconnect too fast on Windows.
            sock.waitForBytesWritten(50)
            sock.flush()
            # Give the server side ~200ms to fire newConnection on its
            # event loop before we close
            sock.waitForDisconnected(200)
            sock.abort()
            return False

        # No server reachable. Cleanup any stale lock and start fresh.
        QLocalServer.removeServer(name)

        self._server = QLocalServer(self)
        # On Windows, allow access from other processes of same user
        try:
            self._server.setSocketOptions(QLocalServer.UserAccessOption)
        except Exception:
            pass
        if not self._server.listen(name):
            QLocalServer.removeServer(name)
            if not self._server.listen(name):
                self._server = None
                return True

        self._server.newConnection.connect(self._on_new_connection)
        return True

    def _on_new_connection(self):
        if not self._server:
            return
        sock = self._server.nextPendingConnection()
        if sock is not None:
            try:
                sock.disconnectFromServer()
            except Exception:
                pass
        # The fact that someone tried to connect IS the signal
        self.another_instance_started.emit()

    def release(self):
        if self._server is not None:
            try:
                self._server.close()
            except Exception:
                pass
            self._server = None
