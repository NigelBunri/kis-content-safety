from app.detector import EXPLICIT_LABELS, _highest_explicit_detection


def test_highest_explicit_detection_ignores_non_explicit_labels():
    detections = [
        {"class": "FACE_FEMALE", "score": 0.99},
        {"class": "ARMPITS_EXPOSED", "score": 0.95},
    ]
    label, score = _highest_explicit_detection(detections)
    assert label is None
    assert score == 0.0


def test_highest_explicit_detection_picks_highest_scoring_explicit_label():
    detections = [
        {"class": "FEMALE_BREAST_EXPOSED", "score": 0.4},
        {"class": "BUTTOCKS_EXPOSED", "score": 0.9},
        {"class": "FACE_FEMALE", "score": 0.99},
    ]
    label, score = _highest_explicit_detection(detections)
    assert label == "BUTTOCKS_EXPOSED"
    assert score == 0.9


def test_explicit_labels_is_the_same_five_labels():
    assert EXPLICIT_LABELS == {
        "FEMALE_GENITALIA_EXPOSED",
        "MALE_GENITALIA_EXPOSED",
        "FEMALE_BREAST_EXPOSED",
        "BUTTOCKS_EXPOSED",
        "ANUS_EXPOSED",
    }
