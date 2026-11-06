from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories import Repository
from pipeline_adapters import TestComponents
from test_pipeline import setup, wait_run


def test_review_from_pipeline_is_readable_and_survives_new_app(settings, fixture):
    repo = setup(settings, fixture, 1)
    with TestClient(create_app(settings, pipeline_adapters=TestComponents(decision="review").adapters()), base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/reviews").json() == []
        run = wait_run(repo, client.post("/api/v1/runs", json={}).json()["run_id"])
        assert run.state == "completed"
        reviews = client.get("/api/v1/reviews").json()
        assert len(reviews) == 1 and reviews[0]["status"] == "open"
        assert reviews[0]["version"] == 1 and reviews[0]["requires_recheck"] is False
        review_id = reviews[0]["review_id"]
        assert client.get("/api/v1/reviews/" + review_id).json() == reviews[0]
        assert client.get("/api/v1/reviews?status=resolved").json() == []
        assert client.get("/api/v1/reviews?status=all").json() == reviews
        for query in ("status=unknown", "kind=assignment", "status=open&status=resolved"):
            assert client.get("/api/v1/reviews?" + query).status_code == 422
        missing = client.get("/api/v1/reviews/missing")
        assert missing.status_code == 404 and missing.json()["code"] == "REVIEW_NOT_FOUND"
        assert client.post("/api/v1/reviews/" + review_id, json={}).status_code == 405
        assert repo.list_topic_ids(None) == []  # review не увеличивает предполагаемую тему
    history = Repository(repo.path).review_history(review_id)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/reviews").json() == reviews
        assert client.get("/api/v1/reviews/" + review_id).json() == reviews[0]
    assert Repository(repo.path).review_history(review_id) == history
