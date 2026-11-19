"""Opt-in real storage smoke tests; profiles and credentials stay outside Git."""

import json
import os
import uuid
from pathlib import Path

import pytest

from config import ConnectionProfile, RunSpec
from conftest import Context
from file_adapter import handle, test_connection as check_connection
from file_connections import Remote


@pytest.mark.skipif(
    not os.environ.get("FILE_TEST_PROFILES_FILE"),
    reason="External file destinations opt-in",
)
def test_real_smb_sftp_ftp_destinations():
    for values in json.loads(Path(os.environ["FILE_TEST_PROFILES_FILE"]).read_text()):
        p = ConnectionProfile.model_validate(values)
        assert check_connection(p)["status"] == "connected"
        spec = RunSpec(
            rows=3, target_rate=0, run_key="acceptance-" + uuid.uuid4().hex[:12]
        )
        spec.files.image.width = 32
        spec.files.image.height = 32
        ctx = Context()
        handle(spec, ctx, p, None)
        assert ctx.stats["processed"] == 3
        remote = Remote(p)
        try:
            image = next(v["path"] for v in ctx.outputs if v["path"].endswith(".png"))
            assert remote.read(image).startswith(b"\x89PNG")
        finally:
            remote.close()
