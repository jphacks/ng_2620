"""The committed packages/contracts/openapi.yaml matches what the server serves (contracts §8)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import export_openapi  # noqa: E402

CONTRACTS = Path(__file__).resolve().parents[3] / "packages" / "contracts"


def test_openapi_up_to_date():
    assert (CONTRACTS / "openapi.yaml").read_text(encoding="utf-8") == export_openapi.build(), \
        "run: python scripts/export_openapi.py"


@pytest.mark.parametrize("name", ["project.schema.json", "overrides.schema.json"])
def test_json_schemas_parse(name):
    js = json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
    assert js["$schema"].startswith("https://json-schema.org/")


def test_every_contract_path_is_served():
    import yaml
    spec = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))
    want = {("get", "/v1/health"), ("get", "/v1/capabilities"), ("post", "/v1/projects"),
            ("get", "/v1/projects/{projectId}"), ("delete", "/v1/projects/{projectId}"), ("post", "/v1/uploads"),
            ("put", "/v1/uploads/{uploadId}/chunks/{index}"), ("get", "/v1/uploads/{uploadId}"),
            ("post", "/v1/uploads/{uploadId}/complete"), ("post", "/v1/avatars"), ("get", "/v1/avatars/{avatarId}"),
            ("post", "/v1/projects/{projectId}/jobs"), ("get", "/v1/jobs/{jobId}"),
            ("get", "/v1/projects/{projectId}/jobs"), ("get", "/v1/queue"), ("post", "/v1/jobs/{jobId}/cancel"),
            ("get", "/v1/jobs/{jobId}/events"), ("get", "/v1/jobs/{jobId}/artifacts"),
            ("get", "/v1/jobs/{jobId}/artifacts/{name}"), ("get", "/v1/jobs/{jobId}/logs"),
            ("post", "/v1/projects/{projectId}/units:prompt"), ("get", "/v1/me")}
    got = {(m, p) for p, ops in spec["paths"].items() for m in ops}
    assert want <= got, want - got
    assert set(spec["x-sse-events"]) == {"snapshot", "queued", "stage_started", "progress", "artifact_ready",
                                         "stage_finished", "job_finished", "job_failed"}
