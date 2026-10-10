"""Every request handler a route dispatches to exists (a missing one crashed saving a site's location)."""

from pathlib import Path
import re
import unittest

from backend import web_app


class RouteHandlerTests(unittest.TestCase):
    def test_every_handler_a_route_calls_exists(self):
        source = Path(web_app.__file__).read_text(encoding="utf-8")
        called = set(re.findall(r"return self\.([a-z_]+)\(\)", source))
        self.assertIn("save_site_location_resolution", called)
        missing = sorted(name for name in called if not hasattr(web_app.Handler, name))
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
