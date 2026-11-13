import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402


@pytest.fixture
def fm_mock_server():
    """启动内置 MockFmServer，测试完自动 stop。"""
    from fm_client.mockserver import MockFmServer
    server = MockFmServer()
    yield server
    server.stop()


@pytest.fixture
def fm(fm_mock_server):
    """一个连着 mock 的 FmTestClient（自带 EventCollector）；测试完自动 close。

    用法：
        def test_xxx(fm):
            fm.front_login("guest1", "1111", room_id=100001, room_node_id="1")
            fm.conf_login(user_name="guest1")
            fm.mic_on()
            assert fm.wait_for_user_state(timeout=3)
    """
    from fm_client.testing import FmTestClient
    client = FmTestClient("127.0.0.1", fm_mock_server.port, request_timeout=5.0)
    yield client
    client.close()

