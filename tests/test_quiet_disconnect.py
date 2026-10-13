"""Windows Proactor 客户端断开噪声过滤：SSE/长连接被 RST 强断时
_call_connection_lost 的 ConnectionResetError 不再刷屏，其余异常照常上报。"""
from server.app import _quiet_disconnect_errors


class _Loop:
    def __init__(self):
        self.default_calls = []

    def default_exception_handler(self, context):
        self.default_calls.append(context)


def test_reset_errors_are_silenced():
    loop = _Loop()
    for exc in (ConnectionResetError(10054, '远程主机强迫关闭了一个现有的连接'),
                ConnectionAbortedError(10053, ''),
                BrokenPipeError(32, '')):
        _quiet_disconnect_errors(loop, {'exception': exc, 'message': 'x'})
    assert loop.default_calls == []


def test_other_exceptions_fall_through():
    loop = _Loop()
    _quiet_disconnect_errors(loop, {'exception': ValueError('boom')})
    _quiet_disconnect_errors(loop, {'message': 'no exception key'})
    assert len(loop.default_calls) == 2
    assert isinstance(loop.default_calls[0]['exception'], ValueError)
