import sqlite3

import pytest

from onecpase import create_app


def test_startup_releases_database_connections(monkeypatch):
    connections = []
    original = sqlite3.connect
    def capture(*args, **kwargs):
        connection = original(*args, **kwargs)
        connections.append(connection)
        return connection
    monkeypatch.setattr(sqlite3, 'connect', capture)
    create_app({'TESTING': True, 'TURNSTILE_ENABLED': False, 'SCHEDULER_ENABLED': False})
    assert connections
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            connection.execute('SELECT 1')
