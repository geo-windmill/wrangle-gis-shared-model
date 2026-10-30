"""Settings from the environment and .env reach every entry point."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from wrangle import cli
from wrangle.cache import Mode

AI_PY = Path(__file__).resolve().parents[1] / "src" / "wrangle" / "ai.py"


def test_env_file_paths_apply_before_any_module_reads_them(tmp_path):
    # A fresh interpreter, with load_env pointed at a scratch .env: importing the
    # package must read it before mappings and convert work out their paths.
    env = tmp_path / ".env"
    env.write_text(
        f"WRANGLE_DB={tmp_path / 'from-file.db'}\nWRANGLE_OUT_DIR={tmp_path / 'out-from-file'}\n"
    )
    code = textwrap.dedent(f"""
        import functools, importlib.util, sys
        spec = importlib.util.spec_from_file_location("wrangle.ai", {str(AI_PY)!r})
        ai = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ai)
        ai.load_env = functools.partial(ai.load_env, __import__("pathlib").Path({str(env)!r}))
        sys.modules["wrangle.ai"] = ai
        from wrangle import convert, mappings
        print(mappings.DB_PATH.name, convert.OUT_DIR.name)
    """)
    clean = {k: v for k, v in os.environ.items() if k not in ("WRANGLE_DB", "WRANGLE_OUT_DIR")}
    out = subprocess.run(
        [sys.executable, "-c", code], env=clean, capture_output=True, text=True, check=True
    )
    assert out.stdout.split() == ["from-file.db", "out-from-file"]


def test_cli_cache_mode_comes_from_the_environment_unless_a_flag_says_otherwise(monkeypatch):
    monkeypatch.setenv("WRANGLE_CACHE_MODE", "replay")
    assert cli._cache(live=False).mode is Mode.REPLAY
    assert cli._cache(live=True).mode is Mode.LIVE
    assert cli._cache(live=False, refresh=True).mode is Mode.REFRESH
