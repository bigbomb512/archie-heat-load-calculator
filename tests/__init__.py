"""Tests run without the operators' imported AIRAH tables (design temperatures and U-values, kept in their home folder),
so results don't depend on the machine; tests that need one point ARCHIE_DESIGN_WEATHER_TABLE or ARCHIE_U_VALUE_TABLE
at their own file."""

import os

os.environ.setdefault("ARCHIE_DESIGN_WEATHER_TABLE", os.path.join(os.path.dirname(__file__), "no-design-weather-table.json"))
os.environ.setdefault("ARCHIE_U_VALUE_TABLE", os.path.join(os.path.dirname(__file__), "no-u-value-table.json"))
