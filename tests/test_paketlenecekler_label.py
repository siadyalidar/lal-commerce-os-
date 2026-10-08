import hb_write_client as hbw


def test_label_path():
    assert hbw.label_path("abc-123", "5526075248") == \
        "/packages/merchantid/abc-123/packagenumber/5526075248/labels"
