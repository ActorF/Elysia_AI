"""Exercise the reviewed Elysia Live2D source-layer alignment gate."""

from pathlib import Path

from scripts import check_live2d_face_alignment


def test_reviewed_live2d_face_layers_remain_aligned() -> None:
    """Reject any source-layer change that moves a reviewed facial landmark."""

    assert check_live2d_face_alignment.audit_face_alignment() == ()


def test_alignment_gate_reports_missing_layer_directory(tmp_path: Path) -> None:
    """Report the complete required layer set when the authoring source is absent."""

    problems = check_live2d_face_alignment.audit_face_alignment(tmp_path)

    assert problems
    assert problems[0].startswith("missing source layers:")
