from delivery.cddvd_channel import CDDVDChannel
from delivery.local_folder import LocalFolderChannel
from delivery.registry import active_channels, build_channels
from delivery.sftp_channel import SFTPChannel


def test_local_delivery_copies_and_verifies(tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF- fake content")
    dest_root = tmp_path / "delivered"
    ch = LocalFolderChannel({"enabled": True, "path": str(dest_root)})
    assert ch.is_available()
    result = ch.deliver(str(src), {})
    assert result.success and result.checksum
    assert (dest_root / "doc.pdf").read_bytes() == src.read_bytes()


def test_local_delivery_respects_output_subfolder(tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"x")
    ch = LocalFolderChannel({"enabled": True, "path": str(tmp_path / "out")})
    result = ch.deliver(str(src), {"output_folder": "Exceptions/Missing"})
    assert result.success
    assert (tmp_path / "out" / "Exceptions" / "Missing" / "doc.pdf").exists()


def test_local_channel_unavailable_without_path():
    assert not LocalFolderChannel({"enabled": True}).is_available()
    assert not LocalFolderChannel({"path": "C:/x"}).is_available()


def test_unconfigured_channels_fail_safely(tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"x")
    for cls in (SFTPChannel, CDDVDChannel):
        ch = cls({"enabled": True})  # enabled but not configured
        assert not ch.is_available()
        result = ch.deliver(str(src), {})
        assert not result.success
        assert "error" in result.detail


def test_registry_builds_all_three():
    channels = build_channels({"local_folder": {"enabled": True, "path": "C:/x"},
                               "sftp": {"enabled": False},
                               "cd_dvd": {"enabled": False}})
    assert [c.name for c in channels] == ["local_folder", "sftp", "cd_dvd"]


def test_active_channels_honours_enabled_and_available(tmp_path):
    cfg = {"local_folder": {"enabled": True, "path": str(tmp_path)},
           "sftp": {"enabled": True},      # enabled but stub → unavailable
           "cd_dvd": {"enabled": False}}
    active = active_channels(cfg)
    assert [c.name for c in active] == ["local_folder"]


def test_status_values():
    assert LocalFolderChannel({}).status_value == "delivered_local"
    assert SFTPChannel({}).status_value == "delivered_sftp"
    assert CDDVDChannel({}).status_value == "burned_to_disc"
