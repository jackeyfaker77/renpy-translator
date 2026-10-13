"""SDK 下载/安装：手动下载接口与启动时自动补装共用"""
import shutil
from pathlib import Path


def find_dmg_sdk_root(mount: Path) -> Path | None:
    """定位 dmg 卷内的 SDK 根（含 renpy.sh/renpy.app 的目录）。

    官方 dmg（7.4.11 / 8.5.3 实测）卷根是同名**文件夹**
    （renpy-X.Y.Z-sdk/，.app 在其内一层），并非卷根直接一个 .app；
    也兼容「卷根即 SDK」与「卷根直接是 .app」布局。找不到返回 None，
    由调用方给出明确报错。
    """
    if (mount / 'renpy.sh').exists() or (mount / 'renpy.app').exists():
        return mount
    for child in sorted(mount.iterdir()):
        if child.name.startswith(('.', '[')):  # 跳过 .HFS+ / [HFS+] 卷级隐藏项
            continue
        if child.is_dir() and ((child / 'renpy.sh').exists()
                               or (child / 'renpy.app').exists()):
            return child
    return None


def sdk_package(version: str) -> tuple:
    """按平台/架构选择 SDK 包与解压方式（官网 dl 目录格式）"""
    import platform
    import sys
    if sys.platform == 'win32':
        return f'renpy-{version}-sdk.zip', 'zip'
    if sys.platform == 'darwin':
        return f'renpy-{version}-sdk.dmg', 'dmg'
    if platform.machine().lower() in ('aarch64', 'arm64'):
        return f'renpy-{version}-sdkarm.tar.bz2', 'tarbz2'
    return f'renpy-{version}-sdk.tar.bz2', 'tarbz2'


def make_download_body(state, version: str):
    """构造 SDK 下载任务体（下载 → 解压 → 校验）。

    完成后按大版本写入对应设置槽位（sdk_path_7 / sdk_path_8），
    不影响另一个大版本的配置。
    """

    async def body(job):
        import tarfile
        import urllib.request
        import uuid
        import zipfile
        from rt_home import home
        from .jobs import JobCancelled

        def log(msg):
            job.emit_log(msg)
            state.logger.info(msg, panel='settings')

        fname, kind = sdk_package(version)
        url = f'https://www.renpy.org/dl/{version}/{fname}'
        tools_dir = home() / 'tools'
        tools_dir.mkdir(parents=True, exist_ok=True)
        # 临时文件/目录名加 uuid：并发下载同版本不会写坏同一个临时文件
        uniq = uuid.uuid4().hex[:8]
        tmp_pkg = tools_dir / f'.{fname}.{uniq}.part'
        stage_dir = tools_dir / f'.{fname}.{uniq}.tmp'
        sdk_dir = tools_dir / f'renpy-{version}-sdk'
        bak_dir = tools_dir / f'{sdk_dir.name}.{uniq}.bak'

        # ---- 下载（可取消） ----
        log(f'下载 {url}')
        try:
            def _download():
                with urllib.request.urlopen(url, timeout=60) as resp:
                    total = int(resp.headers.get('Content-Length') or 0)
                    if not total:
                        raise RuntimeError('服务器未返回文件大小（下载链接可能失效）')
                    log(f'文件大小 {total >> 20} MB，开始下载')
                    with open(tmp_pkg, 'wb') as f:
                        downloaded = 0
                        while True:
                            if job.cancel_event.is_set():
                                raise JobCancelled()
                            chunk = resp.read(1 << 20)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            job.emit_progress(
                                downloaded / total * 0.7,
                                f'正在下载 SDK... '
                                f'({downloaded >> 20}/{total >> 20} MB)')

            await state.run_sync(_download)
        except BaseException:
            tmp_pkg.unlink(missing_ok=True)
            raise
        log('下载完成')

        # ---- 解压到暂存目录（可取消，逐步进度） ----
        # 先解压+校验，全部成功后再替换旧目录，失败不会弄丢可用的旧 SDK
        def _extract_zip():
            with zipfile.ZipFile(tmp_pkg) as zf:
                infos = zf.infolist()
                total = len(infos)
                for i, info in enumerate(infos, 1):
                    if i % 25 == 0 and job.cancel_event.is_set():
                        raise JobCancelled()
                    zf.extract(info, stage_dir)
                    job.emit_progress(0.7 + (i / total) * 0.3,
                                      f'正在解压... ({i}/{total})')

        def _extract_tarbz2():
            with tarfile.open(tmp_pkg, 'r:bz2') as tf:
                members = tf.getmembers()
                total = len(members)
                for i, m in enumerate(members, 1):
                    if i % 25 == 0 and job.cancel_event.is_set():
                        raise JobCancelled()
                    tf.extract(m, stage_dir, filter='data')
                    job.emit_progress(0.7 + (i / total) * 0.3,
                                      f'正在解压... ({i}/{total})')

        def _extract_dmg():
            import subprocess
            import tempfile
            mp = Path(tempfile.mkdtemp())
            staged_sdk = stage_dir / sdk_dir.name
            log('挂载 dmg...')
            subprocess.run(
                ['hdiutil', 'attach', '-nobrowse', '-mountpoint', str(mp),
                 str(tmp_pkg)], check=True, capture_output=True)
            try:
                from sdk_manager import unmangle_mac_binaries
                sdk_root = find_dmg_sdk_root(mp)
                if sdk_root is None:
                    raise RuntimeError(
                        "dmg 中未找到 Ren'Py SDK（缺 renpy.sh/renpy.app）")
                job.emit_progress(0.8, f'正在拷贝 {sdk_root.name}...')
                staged_sdk.mkdir(parents=True, exist_ok=True)
                # 复制 SDK 根的**内容**而非目录本身：staged_sdk 自身必须
                # 是工程根（renpy.sh/lib 顶层），get_renpy_exe 才能命中；
                # 跳过卷级隐藏项（.HFS+ 私有数据、[HFS+]）
                for item in sdk_root.iterdir():
                    if item.name.startswith(('.', '[')):
                        continue
                    subprocess.run(['cp', '-a', str(item),
                                    str(staged_sdk) + '/'], check=True)
                # dmg 为规避公证把平台二进制改名 *.macho +RENPY 头，
                # 不还原则 renpy.sh exec 缺文件（返回码 126）
                unmangle_mac_binaries(staged_sdk)
                # 去掉下载隔离属性，避免 Gatekeeper 拦截
                subprocess.run(['xattr', '-dr', 'com.apple.quarantine',
                                str(staged_sdk)], capture_output=True)
            finally:
                subprocess.run(['hdiutil', 'detach', str(mp)],
                               capture_output=True)
                shutil.rmtree(mp, ignore_errors=True)

        log(f'正在解压（{kind}）...')
        try:
            await state.run_sync({'zip': _extract_zip, 'tarbz2': _extract_tarbz2,
                                  'dmg': _extract_dmg}[kind])
            tmp_pkg.unlink(missing_ok=True)
            log('解压完成')

            staged_sdk = stage_dir / sdk_dir.name
            if not state.sdk_manager.is_valid_sdk(staged_sdk):
                raise RuntimeError(
                    f'解压后未找到有效 SDK: {staged_sdk}（缺 renpy 可执行文件）')

            # ---- 校验通过，替换旧目录（先备份，失败则恢复） ----
            if sdk_dir.exists():
                sdk_dir.rename(bak_dir)
            try:
                shutil.move(str(staged_sdk), str(sdk_dir))
            except BaseException:
                if bak_dir.exists():
                    bak_dir.rename(sdk_dir)
                raise
            shutil.rmtree(bak_dir, ignore_errors=True)
        except BaseException:
            tmp_pkg.unlink(missing_ok=True)
            shutil.rmtree(stage_dir, ignore_errors=True)
            raise
        shutil.rmtree(stage_dir, ignore_errors=True)

        log(f'SDK 就绪: {sdk_dir}')
        job.emit_progress(1.0, 'SDK 就绪')
        return {'sdk_path': str(sdk_dir)}

    return body
