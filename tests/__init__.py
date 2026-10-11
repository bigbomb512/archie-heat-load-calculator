"""Tests run without the operators' imported AIRAH tables (design temperatures and U-values, kept in their home folder),
so results don't depend on the machine; tests that need one point ARCHIE_DESIGN_WEATHER_TABLE or ARCHIE_U_VALUE_TABLE
at their own file."""

import os

os.environ.setdefault("ARCHIE_DESIGN_WEATHER_TABLE", os.path.join(os.path.dirname(__file__), "no-design-weather-table.json"))
os.environ.setdefault("ARCHIE_U_VALUE_TABLE", os.path.join(os.path.dirname(__file__), "no-u-value-table.json"))
# No test may reach a paid AI service: the team's key, if the shell has it, is blanked (tests that need a key set
# their own fake one).
for name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
    os.environ[name] = ""
