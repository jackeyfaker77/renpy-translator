"""SDK 子进程 stdout 管道死锁回归测试

_run_once 此前"先等子进程退出、退出后才读 stdout"：子进程输出一旦
超过管道缓冲（Windows 匿名管道默认 4KB），子进程阻塞在写、父进程阻塞
在等退出 → 互锁，直到 3600s deadline 才报「执行超时」。触发场景：
工程根错误时 renpy 输出海量 duplicate label 错误（实测 10KB+）。

契约：无论子进程输出多大，_run_once 都必须在子进程自然退出后
及时返回，输出完整可读（边读边等）。
"""
import sys
import threading

from sdk_manager import SDKManager


def test_large_output_no_deadlock(tmp_path):
    """子进程输出远超管道缓冲时不得死锁

    用 cancel_event 做 15s 兜底：旧实现在死锁下会挂到兜底触发、
    返回 cancelled（测试失败）；新实现 ~1s 内自然完成。
    """
    cmd = [sys.executable, '-c', 'print("x" * 300000)']
    cancel = threading.Event()
    safety = threading.Timer(15.0, cancel.set)
    safety.daemon = True
    safety.start()
    try:
        result = SDKManager(str(tmp_path))._run_once(cmd, 'chinese', cancel)
    finally:
        safety.cancel()

    assert not result.get('cancelled'), (
        '子进程输出超过管道缓冲后与轮询互锁（先等退出后读 stdout），'
        '只能靠取消兜底脱身')
    assert result['returncode'] == 0, result
    assert len(result['output']) >= 300_000, (
        f'输出未完整读回: {len(result["output"])}B')


def test_small_output_roundtrip(tmp_path):
    """常规小输出：行为与输出内容不变"""
    cmd = [sys.executable, '-c', 'print("hello sdk")']

    result = SDKManager(str(tmp_path))._run_once(cmd, 'chinese')

    assert result['returncode'] == 0, result
    assert 'hello sdk' in result['output']
