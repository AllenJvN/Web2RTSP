from web2rtsp.runtime import X264_PRESET, X264_THREADS, mediamtx_config

from .test_config import sample_config


def test_mediamtx_is_tcp_only_and_requires_viewer_credentials():
    result = mediamtx_config(sample_config())
    assert result["rtspTransports"] == ["tcp"]
    assert result["rtmp"] is False
    assert result["webrtc"] is False
    assert result["authInternalUsers"][0]["ips"] == ["127.0.0.1", "::1"]
    assert result["authInternalUsers"][0]["permissions"] == [{"action": "publish"}]
    assert result["authInternalUsers"][1]["user"] == "viewer"
    assert result["authInternalUsers"][1]["pass"] == "correct-horse"
    assert result["authInternalUsers"][1]["permissions"] == [{"action": "read"}]


def test_mediamtx_ports_come_from_config():
    config = sample_config()
    config["rtsp"]["port"] = 9554
    config["rtsp"]["hls_port"] = 9888
    result = mediamtx_config(config)
    assert result["rtspAddress"] == ":9554"
    assert result["hlsAddress"] == ":9888"


def test_encoder_defaults_match_the_verified_720p_profile():
    assert X264_PRESET == "superfast"
    assert X264_THREADS == "1"
