import io


def _sign_in_admin(client):
    with client.session_transaction() as session:
        session.update({
            "user_id": 1,
            "role": "admin",
            "full_name": "Test Administrator",
            "tenant_slug": None,
        })


def test_screen_recordings_page_requires_admin(client):
    response = client.get("/screen-recordings/")

    assert response.status_code in (302, 401)


def test_admin_can_upload_screen_recording(client, app, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    _sign_in_admin(client)

    response = client.post(
        "/screen-recordings/upload",
        data={
            "title": "Sales lead workflow",
            "duration_seconds": "12",
            "recording": (io.BytesIO(b"webm-test-data"), "screen-recording.webm", "video/webm"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    assert response.json["recording_id"]
    with app.app_context():
        from onecpase.database import get_db

        recording = get_db().execute("SELECT * FROM screen_recordings").fetchone()
        assert recording["title"] == "Sales lead workflow"
        assert recording["duration_seconds"] == 12
        assert (tmp_path / "screen_recordings" / recording["stored_filename"]).exists()


def test_screen_recording_rejects_non_video_upload(client):
    _sign_in_admin(client)

    response = client.post(
        "/screen-recordings/upload",
        data={"recording": (io.BytesIO(b"not-video"), "notes.txt", "text/plain")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "Only WebM and MP4" in response.json["error"]
