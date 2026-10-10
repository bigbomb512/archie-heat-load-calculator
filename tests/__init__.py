"""Tests run without the operators' imported design-temperature table (kept in their home folder), so results don't
depend on the machine; tests that need one point ARCHIE_DESIGN_WEATHER_TABLE at their own file."""

import os

os.environ.setdefault("ARCHIE_DESIGN_WEATHER_TABLE", os.path.join(os.path.dirname(__file__), "no-design-weather-table.json"))
