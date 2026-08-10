from fastapi.testclient import TestClient

from app.core import payers as payers_module
from app.main import app


client = TestClient(app)


def test_admin_payers_can_be_updated():
    original = dict(payers_module.KNOWN_PAYERS)
    try:
        response = client.get("/admin/payers")
        assert response.status_code == 200

        response = client.post(
            "/admin/payers",
            json={
                "payers": [
                    {"name": "Example Health", "aliases": ["example", "example health"]}
                ]
            },
        )

        assert response.status_code == 200
        assert payers_module.KNOWN_PAYERS["Example Health"] == ["example", "example health"]
    finally:
        payers_module.KNOWN_PAYERS.clear()
        payers_module.KNOWN_PAYERS.update(original)
