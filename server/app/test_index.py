from app import create_app


def test_index_renders_hello_world() -> None:
    client = create_app().test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert "hello world" in response.text
