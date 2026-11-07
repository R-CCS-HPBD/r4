from __future__ import annotations

from unittest.mock import patch

from r4.config import Config
from r4.vision import VisionClient


def test_vision_submit_forwards_gateway_payload_for_s3_uri():
    client = VisionClient(
        Config(gateway_url="http://gateway:8080", project_id="project-a", verify_ssl=False)
    )

    with patch.object(client._http, "post", return_value={"task": {"task_id": "task-1"}}) as post:
        result = client.submit(
            "s3://umrs-files/project-a/images/cell.png",
            model="sam3",
            adapter="sam3",
            inference_config={"prompt": "cell"},
            output_bucket="vision-results",
        )

    assert result["task"]["task_id"] == "task-1"
    assert post.call_args.args[0] == "http://gateway:8080/gw/vision/infer/submit"
    assert post.call_args.kwargs["json_body"] == {
        "image_uri": "s3://umrs-files/project-a/images/cell.png",
        "inference_config": {"prompt": "cell"},
        "model": "sam3",
        "adapter": "sam3",
        "output_bucket": "vision-results",
    }


def test_vision_status_uses_gateway_query_params():
    client = VisionClient(
        Config(gateway_url="http://gateway:8080", project_id="project-a", verify_ssl=False)
    )

    with patch.object(client._http, "get", return_value={"status": "running"}) as get:
        result = client.status("task-1", endpoint_id="job-vision")

    assert result == {"status": "running"}
    assert get.call_args.args[0] == "http://gateway:8080/gw/vision/infer/status"
    assert get.call_args.kwargs["params"] == {
        "task_id": "task-1",
        "endpoint_id": "job-vision",
    }
