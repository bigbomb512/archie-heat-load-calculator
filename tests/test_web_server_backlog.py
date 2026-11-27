"""A page load opens about 20 connections at once (the job screens' modules, styles and font). Python's default
listen queue of 5 dropped the extras on a busy machine, and the browser waited seconds before trying again: the
Jobs heading or a room plan didn't appear within 5 s in full Playwright runs alongside the smoke gate (2026-11-27,
15 of 60 stressed runs failed; 0 of 60 with a longer queue)."""

import socket
import unittest

from backend import web_app


class ServerBacklogTests(unittest.TestCase):
    def test_the_server_queues_a_whole_page_load_of_connections(self):
        self.assertGreaterEqual(web_app.ArchieHTTPServer.request_queue_size, 64)

    def test_many_connections_waiting_at_once_are_all_queued(self):
        try:
            server = web_app.ArchieHTTPServer(("127.0.0.1", 0), web_app.Handler)
        except OSError as error:  # sandboxes without local sockets
            self.skipTest(f"local sockets unavailable: {error}")
        clients = []
        try:
            # The server is not accepting yet, so every connection waits in the listen queue, as on a busy machine.
            for _ in range(32):
                client = socket.create_connection(server.server_address, timeout=1.0)
                clients.append(client)
            self.assertEqual(len(clients), 32)
        finally:
            for client in clients:
                client.close()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
