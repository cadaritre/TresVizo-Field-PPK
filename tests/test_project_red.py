"""Proyecto «network» (.tvppk): receptores, fijas, opciones del ajuste y decisiones de cada línea, ida y vuelta."""
from __future__ import annotations

import json

import pytest

from tresvizo_ppk import project as projects
from tresvizo_ppk.model import NetworkFixChoice, NetworkOptions, NetworkRover, Project


def test_network_project_round_trip(tmp_path):
    project = Project(kind="network", name="Red", rovers=[
        NetworkRover("R1", point_name="", pole_height_m=None, label="MeridianV 0417"),
        NetworkRover("R2", point_name="P7", pole_height_m=1.523, code="VERT", description="Mojón", label="r2.obs")],
        network_fix=[NetworkFixChoice("INEG", "check"), NetworkFixChoice("ISLP", "weighted", True, False)],
        network_options=NetworkOptions(test="w", scale_covariances="value", scale_value=2.5, minimal_datum="inner",
                                       minimal_point="UQRO", include_trivial=True, relative_pairs="all"),
        line_choices={"INEG–P1 · 15-07 06:00": "off", "P1–P2 · 15-07 06:00": "independent"})
    path = projects.save(project, tmp_path / "red.tvppk")
    back = projects.load(path)
    assert back.kind == "network"
    assert back.rovers == project.rovers and back.network_fix == project.network_fix
    assert back.network_options == project.network_options and back.line_choices == project.line_choices


def test_network_project_tolerates_hand_edits(tmp_path):
    path = projects.save(Project(kind="network", name="Red"), tmp_path / "red.tvppk")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["rovers"] = [{"key": "R1"}, {"key": "R1"}, {"point_name": "sin clave"}, {"key": "R2", "pole_height_m": "x"}]
    data["network_fix"] = [{"point_id": "INEG", "mode": "rara"}, {"mode": "fixed"}]
    data["network_options"] = {"test": "chi", "alpha": 3, "scale_value": -1, "include_trivial": 1}
    data["line_choices"] = {"a": "off", "b": "quizá"}
    path.write_text(json.dumps(data), encoding="utf-8")
    back = projects.load(path)
    assert [r.key for r in back.rovers] == ["R1"]
    assert back.network_fix == [NetworkFixChoice("INEG", "fixed")]
    o = back.network_options
    assert o.test == "tau" and o.alpha == 0.05 and o.scale_value == 1.0 and o.include_trivial is True
    assert back.line_choices == {"a": "off"}


def test_old_projects_still_open(tmp_path):
    path = projects.save(Project(kind="static", name="Vértice"), tmp_path / "v.tvppk")
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("rovers", "network_fix", "network_options", "line_choices"):
        data.pop(key)
    path.write_text(json.dumps(data), encoding="utf-8")
    back = projects.load(path)
    assert back.rovers == [] and back.network_options == NetworkOptions()
    data["kind"] = "otro"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(projects.ProjectError):
        projects.load(path)
