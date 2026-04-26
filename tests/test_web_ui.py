from __future__ import annotations

from .test_api import make_client
from .helpers import make_work_dir, remove_work_dir


def test_web_pages_render_and_submit_redirects_to_detail():
    work_dir = make_work_dir("web-ui")
    try:
        client = make_client(work_dir)

        dashboard = client.get("/")
        assert dashboard.status_code == 200, dashboard.text
        assert "沙箱总览" in dashboard.text

        submit_page = client.get("/submit")
        assert submit_page.status_code == 200, submit_page.text
        assert "提交样本" in submit_page.text

        submitted = client.post(
            "/submit",
            files={"file": ("sample.exe", b"MZ" + b"\x00" * 128, "application/octet-stream")},
            data={"timeout": "90", "route": "drop", "platforms": "windows"},
            follow_redirects=False,
        )
        assert submitted.status_code == 303, submitted.text
        assert submitted.headers["location"].startswith("/analyses/")

        detail = client.get(submitted.headers["location"])
        assert detail.status_code == 200, detail.text
        assert "分析详情" in detail.text
        assert "sample.exe" in detail.text

        analyses = client.get("/analyses")
        assert analyses.status_code == 200, analyses.text
        assert "sample.exe" in analyses.text

        nodes = client.get("/nodes")
        assert nodes.status_code == 200, nodes.text
        assert "节点" in nodes.text
    finally:
        remove_work_dir(work_dir)
