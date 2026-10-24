import asyncio
import json
import shutil
import struct
import subprocess

import pytest

from vvlog_server.adapters import vrm
from vvlog_server.adapters.events import JsonlEventBus
from vvlog_server.adapters.fs import FsArtifactStore, FsAvatarStore, FsJobRepository, FsProjectRepository
from vvlog_server.adapters.queue import InProcessJobQueue
from vvlog_server.domain.errors import VvlogError
from vvlog_server.domain.job import Job

BONES = ["hips", "spine", "head", "leftUpperArm", "rightUpperArm", "leftUpperLeg", "rightUpperLeg"]


def make_vrm(version="1.0", bones=BONES) -> bytes:
    if version == "1.0":
        ext = {"VRMC_vrm": {"meta": {"name": "Sample", "authors": ["team"], "commercialUsage": "corporation"},
                            "humanoid": {"humanBones": {b: {"node": i} for i, b in enumerate(bones)}}}}
    else:
        ext = {"VRM": {"meta": {"title": "Old", "author": "team", "licenseName": "CC_BY"},
                       "humanoid": {"humanBones": [{"bone": b, "node": i} for i, b in enumerate(bones)]}}}
    js = json.dumps({"asset": {"version": "2.0"}, "extensions": ext}).encode()
    js += b" " * (-len(js) % 4)
    return struct.pack("<III", 0x46546C67, 2, 20 + len(js)) + struct.pack("<II", len(js), 0x4E4F534A) + js


def test_vrm_meta_both_versions():
    m1 = vrm.read_meta(make_vrm("1.0"))
    assert m1["name"] == "Sample" and m1["version"] == "1.0" and m1["license"] == {"commercial": "corporation"}
    m0 = vrm.read_meta(make_vrm("0.x"))
    assert m0["authors"] == ["team"] and m0["license"]["name"] == "CC_BY"


@pytest.mark.parametrize("data", [b"not a glb", make_vrm(bones=BONES[:3])])
def test_vrm_invalid(data):
    with pytest.raises(VvlogError) as e:
        vrm.read_meta(data)
    assert e.value.code == "AVATAR_INVALID"


def test_avatar_store_dedups(tmp_path):
    s = FsAvatarStore(tmp_path)
    a, _ = s.put(make_vrm(), "a.vrm")
    b, _ = s.put(make_vrm(), "copy.vrm")
    assert a == b and a.startswith("av_") and len(a) == 19
    assert s.get(a)[0].is_file()


def test_job_repo_and_artifacts(tmp_path):
    projects, jobs = FsProjectRepository(tmp_path), FsJobRepository(tmp_path)
    p = projects.create("demo")
    job = Job.create(p["projectId"], "analyze", {"mediaId": "med_1"})
    jobs.save(job)
    assert FsJobRepository(tmp_path).get(job.jobId).to_dict() == job.to_dict()   # read back from disk
    src = tmp_path / "meta.json"
    src.write_text("{}")
    store = FsArtifactStore(projects, jobs)
    a = store.publish(job, "meta", src)
    assert a.url == f"/v1/jobs/{job.jobId}/artifacts/meta" and a.size == 2 and a.stage == "ingest"
    path, a2 = store.open(job.jobId, "meta")
    assert path.read_text() == "{}" and a2 == a and src.exists()      # copied, not moved
    with pytest.raises(VvlogError):
        store.open(job.jobId, "persons")


async def test_event_bus_replay_and_live(tmp_path):
    bus = JsonlEventBus(tmp_path)
    bus.publish("job_1", "queued", {"position": 0})
    bus.publish("job_1", "stage_started", {"stage": "ingest"})
    got = []

    async def read():
        async for e in bus.subscribe("job_1", after_id=1):
            got.append((e["id"], e["event"]))

    t = asyncio.create_task(read())
    await asyncio.sleep(0.05)
    bus.publish("job_1", "job_finished", {"status": "succeeded"})
    await asyncio.wait_for(t, 2)
    assert got == [(2, "stage_started"), (3, "job_finished")]
    assert JsonlEventBus(tmp_path).publish("job_1", "heartbeat", {}) == 4         # ids continue after a restart


def test_queue_fifo():
    q = InProcessJobQueue()
    for j in ("a", "b", "c"):
        q.enqueue(j)
    q.enqueue("a")
    assert q.items() == ["a", "b", "c"] and q.position("c") == 2
    assert q.remove("b") and not q.remove("b") and q.items() == ["a", "c"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
def test_frames_to_h264_with_gaps_and_quote(tmp_path):
    """Real ffmpeg: no glob (absent on Windows builds), stems need not be gapless, ' in the path is escaped."""
    from PIL import Image
    from vvlog_server.adapters.pipeline_outputs import frames_to_h264

    frames = tmp_path / "it's" / "frames"
    frames.mkdir(parents=True)
    for stem in (3, 4, 7, 10, 11):
        Image.new("RGB", (63, 35), (stem * 20, 0, 0)).save(frames / f"{stem:08d}.jpg")
    out = frames_to_h264(frames, 15.0, tmp_path / "preview.mp4")
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-print_format", "json",
                                       "-show_streams", str(out)], check=True, capture_output=True, text=True).stdout)
    s = probe["streams"][0]
    assert (s["codec_name"], s["width"], s["height"], s["nb_read_frames"]) == ("h264", 64, 36, "5")
    assert s["avg_frame_rate"] == "15/1"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["it's", "preview.mp4"]  # the list file is removed


def test_frames_to_h264_without_frames(tmp_path):
    from vvlog_server.adapters.pipeline_outputs import frames_to_h264

    (tmp_path / "frames").mkdir()
    assert frames_to_h264(tmp_path / "frames", 15.0, tmp_path / "preview.mp4") is None
