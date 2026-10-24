import pytest

from vvlog_server.domain import media as M
from vvlog_server.domain.errors import VvlogError
from vvlog_server.domain.job import Job
from vvlog_server.domain.upload import Upload


def test_job_state_machine():
    j = Job.create("prj_1", "analyze", {"mediaId": "med_1"})
    assert [s.name for s in j.stages] == ["ingest", "track", "mesh", "occlusion"]
    j.start()
    j.stage_started("ingest")
    j.stage_progress("ingest", 0.5)
    j.stage_progress("ingest", 0.2)          # never goes back
    assert j.stage("ingest").progress == 0.5
    j.stage_finished("ingest")
    j.succeed()
    assert j.finished and j.finishedAt
    with pytest.raises(ValueError):
        j.start()
    assert Job.from_dict(j.to_dict()).to_dict() == j.to_dict()


def test_job_dependency_required():
    with pytest.raises(VvlogError) as e:
        Job.create("prj_1", "render", {})
    assert e.value.code == "VALIDATION_FAILED"
    assert Job.create("prj_1", "export", {"renderJobId": "job_x"}).dependsOn == "job_x"


def test_cancel_marks_running_stage_failed():
    j = Job.create("prj_1", "render", {"analyzeJobId": "job_a"})
    j.start()
    j.stage_started("removal")
    j.cancel()
    assert j.status == "canceled" and j.stage("removal").status == "failed" and j.error["code"] == "CANCELED"


@pytest.mark.parametrize("w,h,scale,proxy", [
    (1080, 1920, 2, (540, 960)),        # ADR-0011's example
    (1920, 1080, 2, (960, 540)),
    (720, 1280, 2, (360, 640)),
    (480, 854, 1, (480, 854)),
    (1440, 1920, 3, (480, 640)),
])
def test_proxy_scale(w, h, scale, proxy):
    s = M.proxy_scale(w, h)
    assert s == scale and (w // s, h // s) == proxy


def _probe(**kw):
    d = dict(format_name="mov,mp4,m4a,3gp,3g2,mj2", codec="hevc", width=1920, height=1080, rotation=90, fps=59.94,
             duration_sec=9.8, has_audio=True)
    d.update(kw)
    return M.Probe(**d)


def test_plan_meta_applies_rotation_and_cfr():
    m = M.plan_meta(_probe())
    assert m.full == {"width": 1080, "height": 1920} and m.proxy == {"width": 540, "height": 960}
    assert m.fps == pytest.approx(29.97) and m.frameCount == round(9.8 * 29.97) and m.proxyScale == 2


@pytest.mark.parametrize("kw,code", [
    ({"duration_sec": 42.1}, "MEDIA_TOO_LONG"),
    ({"width": 3840, "height": 2160}, "MEDIA_TOO_LARGE"),
    ({"codec": "vp9"}, "MEDIA_UNSUPPORTED"),
    ({"format_name": "matroska,webm"}, "MEDIA_UNSUPPORTED"),
])
def test_validate(kw, code):
    with pytest.raises(VvlogError) as e:
        M.validate(_probe(**kw))
    assert e.value.code == code


def test_upload_chunks():
    u = Upload.create("prj_1", "source", "a.mov", 8 * 1024 * 1024 * 2 + 10, "0" * 64)
    assert u.chunk_count == 3 and u.expected_length(2) == 10
    u.receive(2, 10)
    u.receive(0, u.chunkSize)
    assert u.missing() == [1]
    with pytest.raises(VvlogError):
        u.receive(1, 5)
    with pytest.raises(VvlogError):
        u.expected_length(3)
