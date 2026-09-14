import os

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
