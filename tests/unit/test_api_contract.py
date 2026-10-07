"""Contract tests for the REST layer.

The API had no tests at all, which left the widest gap in the suite: eleven
endpoints, and the one place where a failure inside the graph becomes — or fails
to become — something the caller can see.

The case that matters most is `POST /investigations`. It used to wrap the graph
run in `except Exception: pass`, commented "Expected pause at human checkpoint".
The comment was wrong: `interrupt_before` makes LangGraph *return* at the
breakpoint, it does not raise. So the handler caught nothing but genuine
failures — an unreachable provider, a defect in a node, a dropped connection —
and answered `201 Created` with an empty verdict while the record sat RUNNING
for ever. A caller could not tell that from a real pause. That is the silent
degradation the whole system is built to refuse, sitting in its front door.

No database and no graph: the session dependency is overridden and the compiled
graph is replaced per test. `TestClient` is used without its context manager on
purpose — entering it would run the lifespan hook, and that opens Postgres.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from sentinel.api import main as api
from sentinel.database.db import get_db


class FakeRecord:
    def __init__(self, subject_name="Wolfspeed", ticker="WOLF"):
        self.id = uuid.uuid4()
        self.subject_name = subject_name
        self.ticker = ticker
        self.status = "RUNNING"
        self.created_at = datetime.now(tz=timezone.utc)
        self.risk_level = "UNKNOWN"
        self.human_approved = None
        self.supervisor_reasoning = ""
        self.final_report = ""


class FakeState:
    def __init__(self, values):
        self.values = values


class FakeGraph:
    """Stands in for the compiled LangGraph app."""

    def __init__(self, *, raises=None, values=None):
        self._raises = raises
        self._values = values or {}
        self.invoked = False

    def invoke(self, _input, _config):
        self.invoked = True
        if self._raises is not None:
            raise self._raises
        return self._values

    def get_state(self, _config):
        return FakeState(self._values)


@pytest.fixture
def client():
    api.app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(api.app, raise_server_exceptions=False)
    api.app.dependency_overrides.clear()


@pytest.fixture
def graph(monkeypatch):
    def install(**kwargs):
        fake = FakeGraph(**kwargs)
        monkeypatch.setattr(api, "graph_app", fake)
        monkeypatch.setattr(api, "create_investigation", lambda **_: FakeRecord())
        return fake

    return install


class TestGraphFailureIsVisible:
    """The regression: a failed run must not be answered as a created one."""

    def test_graph_failure_is_not_reported_as_created(self, client, graph):
        graph(raises=RuntimeError("provider unreachable"))
        response = client.post("/investigations", json={"subject_name": "Wolfspeed", "ticker": "WOLF"})
        assert response.status_code != 201

    def test_graph_failure_returns_a_gateway_error(self, client, graph):
        graph(raises=RuntimeError("provider unreachable"))
        response = client.post("/investigations", json={"subject_name": "Wolfspeed", "ticker": "WOLF"})
        assert response.status_code == 502

    def test_the_failure_reason_reaches_the_caller(self, client, graph):
        graph(raises=RuntimeError("provider unreachable"))
        response = client.post("/investigations", json={"subject_name": "Wolfspeed", "ticker": "WOLF"})
        assert "provider unreachable" in response.json()["detail"]

    def test_the_exception_type_is_named(self, client, graph):
        """"Something went wrong" is not a diagnosis an operator can act on."""
        graph(raises=ValueError("bad state"))
        response = client.post("/investigations", json={"subject_name": "Wolfspeed", "ticker": "WOLF"})
        assert "ValueError" in response.json()["detail"]

    def test_a_paused_run_is_still_a_success(self, client, graph):
        """The interrupt returns; only real faults raise."""
        graph(values={"plan": {"decisions": []}, "risk_level": "ELEVATED"})
        response = client.post("/investigations", json={"subject_name": "Wolfspeed", "ticker": "WOLF"})
        assert response.status_code == 201

    def test_the_paused_verdict_is_returned(self, client, graph):
        graph(values={"risk_level": "ELEVATED", "confidence": 0.74})
        response = client.post("/investigations", json={"subject_name": "Wolfspeed", "ticker": "WOLF"})
        body = response.json()
        assert body["risk_level"] == "ELEVATED"
        assert body["confidence"] == 0.74

    def test_the_graph_actually_ran(self, client, graph):
        fake = graph(values={})
        client.post("/investigations", json={"subject_name": "Wolfspeed", "ticker": "WOLF"})
        assert fake.invoked


class TestRequestValidation:
    def test_a_missing_subject_is_rejected(self, client, graph):
        graph(values={})
        assert client.post("/investigations", json={}).status_code == 422

    def test_a_ticker_is_optional(self, client, graph):
        graph(values={})
        response = client.post("/investigations", json={"subject_name": "Acme Trading Limited"})
        assert response.status_code == 201


class TestToolCatalogue:
    def test_every_tool_declares_its_availability(self, client):
        for tool in client.get("/tools").json():
            assert isinstance(tool["available"], bool)

    def test_an_unavailable_tool_says_why(self, client):
        """`available: false` with no reason tells an operator nothing."""
        for tool in client.get("/tools").json():
            if not tool["available"]:
                assert tool["unavailable_reason"].strip()

    def test_the_catalogue_names_a_data_source_for_each_tool(self, client):
        for tool in client.get("/tools").json():
            assert tool["data_source"].strip()

    def test_the_catalogue_is_not_empty(self, client):
        assert len(client.get("/tools").json()) >= 5


class TestSystemStatus:
    def test_durability_is_reported_as_a_boolean(self, client):
        assert isinstance(client.get("/system").json()["checkpointing"]["durable"], bool)

    def test_the_backend_is_named(self, client):
        assert client.get("/system").json()["checkpointing"]["backend"]

    def test_each_provider_reports_a_role_and_availability(self, client):
        for provider in client.get("/system").json()["llm"]["providers"]:
            assert provider["role"] in {"primary", "fallback"}
            assert isinstance(provider["available"], bool)

    def test_usage_is_attributed_by_provider(self, client):
        """Which provider *served* traffic, not which was configured."""
        assert "by_provider" in client.get("/system").json()["llm"]["usage_since_startup"]


class TestAssetDownloads:
    def test_an_unknown_category_is_rejected(self, client):
        assert client.get("/assets/nonsense/file.pdf").status_code == 404

    @pytest.mark.parametrize("attempt", [
        "../../../../etc/passwd",
        "..%2f..%2f.env",
        "....//....//.env",
    ])
    def test_traversal_cannot_escape_the_asset_directory(self, client, attempt):
        response = client.get(f"/assets/dataset/{attempt}")
        assert response.status_code == 404
        assert "sentinel_user" not in response.text

    def test_a_missing_file_is_a_404_not_a_crash(self, client):
        assert client.get("/assets/dataset/no-such-file.csv").status_code == 404

    def test_the_listing_describes_each_file(self, client):
        for item in client.get("/assets/files").json():
            assert item["filename"] and item["type"] and item["path"].startswith("/assets/")


class TestMalformedIdentifiers:
    """`uuid.UUID` raises on anything malformed; unhandled, that is a 500.

    A 500 tells the caller the server broke. The truth is that they asked for
    something that cannot exist, and the difference matters to anyone reading an
    error rate.
    """

    MALFORMED = ["not-a-uuid", "123", "' OR 1=1--", "%00", "null"]

    @pytest.mark.parametrize("bad_id", MALFORMED)
    def test_fetching_a_malformed_id_is_not_a_server_error(self, client, graph, bad_id):
        graph(values={})
        assert client.get(f"/investigations/{bad_id}").status_code < 500

    @pytest.mark.parametrize("bad_id", MALFORMED)
    def test_approving_a_malformed_id_is_not_a_server_error(self, client, graph, bad_id):
        graph(values={})
        response = client.post(f"/investigations/{bad_id}/approve", json={"approved": True})
        assert response.status_code < 500

    def test_a_malformed_id_never_reaches_the_graph(self, client, graph):
        """An approval must not be written into a thread that cannot be finalised."""
        fake = graph(values={})
        fake.update_state = lambda *a, **k: pytest.fail("graph was touched with an unusable id")
        client.post("/investigations/not-a-uuid/approve", json={"approved": True})

    def test_the_report_endpoint_rejects_a_malformed_id(self, client, graph):
        graph(values={})
        assert client.get("/investigations/not-a-uuid/report").status_code < 500
