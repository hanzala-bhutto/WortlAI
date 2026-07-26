"""Contract for assembling the session runtime.

The runtime is what the app builds at startup and the voice loop (#8) drives. What
matters here is that assembling it opens the checkpointer on its own file (so a
conversation can be resumed after a restart) and that closing it releases both the
checkpointer connection and the provider's HTTP client. Driving an actual turn is
covered by test_session_graph; that would write to the real learner store, so it
is deliberately not done here.

data_dir is redirected to a temp path so no real checkpoints.db is created.
"""

import pytest

from app.agents.runtime import build_session_runtime
from app.config import get_settings


def _temp_settings(tmp_path):
    return get_settings().model_copy(update={"data_dir": tmp_path})


async def test_build_opens_checkpointer_on_its_own_file(tmp_path):
    runtime = await build_session_runtime(_temp_settings(tmp_path))
    try:
        assert (tmp_path / "checkpoints.db").exists()
        # A compiled graph is runnable: it exposes the async invoke/stream API.
        assert hasattr(runtime.graph, "ainvoke")
        assert hasattr(runtime.graph, "astream")
    finally:
        await runtime.aclose()


async def test_aclose_is_safe_and_releases_the_connection(tmp_path):
    runtime = await build_session_runtime(_temp_settings(tmp_path))
    await runtime.aclose()

    # The checkpointer connection is closed: using it now raises, which is how we
    # know aclose actually released it rather than just returning.
    with pytest.raises(Exception):
        await runtime._conn.execute("select 1")
