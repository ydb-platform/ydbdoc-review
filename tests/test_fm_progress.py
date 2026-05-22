from ydbdoc_review.fm_progress import fm_call_span, fm_http_timeout_sec, fm_progress_enabled


def test_fm_http_timeout_default():
    assert fm_http_timeout_sec() >= 30.0


def test_fm_call_span_logs(monkeypatch, capsys):
    monkeypatch.setenv("YDBDOC_FM_PROGRESS_LOG", "1")
    assert fm_progress_enabled()
    with fm_call_span(operation="test_op", model="m1", detail="x.md"):
        pass
    err = capsys.readouterr().err
    assert "test_op" in err
    assert "m1" in err
    assert "x.md" in err
