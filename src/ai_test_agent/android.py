"""Fail-closed Android commands; every invocation uses an explicit device."""

import hashlib
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .contracts import evidence_for_png, make_id


@dataclass
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    duration_ms: int = 0


class CommandExecutor:
    def run(self, argv, timeout=30):
        if not isinstance(argv, (list, tuple)) or not argv or any(not isinstance(arg, str) or not arg or '\x00' in arg for arg in argv):
            raise ValueError('argv must be a nonempty sequence of literal string arguments')
        start = time.monotonic()
        try:
            completed = subprocess.run(list(argv), shell=False, capture_output=True, timeout=timeout, check=False)
            result = CommandResult(list(argv), completed.returncode, completed.stdout, completed.stderr)
        except subprocess.TimeoutExpired as error:
            result = CommandResult(list(argv), -1, error.stdout or b'', error.stderr or b'', timed_out=True)
        except OSError as error:
            result = CommandResult(list(argv), 127, b'', str(error).encode('utf-8'))
        result.duration_ms = round((time.monotonic() - start) * 1000)
        return result


class AndroidToolError(RuntimeError):
    def __init__(self, message, result):
        super().__init__(message)
        self.result = result


class AndroidDevice:
    def __init__(self, adb_path, serial, executor=None):
        if not isinstance(serial, str) or not re.fullmatch(r'[A-Za-z0-9._:-]+', serial):
            raise ValueError('an explicit device serial is required')
        self.adb_path = str(adb_path)
        self.serial = serial
        self.executor = executor if executor is not None else CommandExecutor()
        self.command_results = []

    def _run(self, args, timeout=30):
        result = self.executor.run([self.adb_path, '-s', self.serial, *args], timeout=timeout)
        self.command_results.append(result)
        return self._checked(result)

    @staticmethod
    def _checked(result):
        if result.returncode != 0 or result.timed_out:
            raise AndroidToolError('Android command failed or timed out', result)
        return result

    @staticmethod
    def _package(package):
        if not isinstance(package, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+', package):
            raise ValueError('invalid Android package name')
        return package

    def package_info(self, package):
        result = self._run(['shell', 'dumpsys', 'package', self._package(package)])
        text = result.stdout.decode('utf-8', errors='replace')
        values = {}
        for output_name, pattern in (('version_code', r'\bversionCode=(\d+)'), ('version_name', r'\bversionName=([^\s]+)'),
                                     ('min_sdk', r'\bminSdk=(\d+)'), ('target_sdk', r'\btargetSdk=(\d+)')):
            match = re.search(pattern, text)
            if not match:
                raise AndroidToolError(f'package metadata missing {output_name}', result)
            values[output_name] = match[1] if output_name == 'version_name' else int(match[1])
        return values

    def device_info(self):
        model = self._run(['shell', 'getprop', 'ro.product.model'])
        sdk = self._run(['shell', 'getprop', 'ro.build.version.sdk'])
        abi = self._run(['shell', 'getprop', 'ro.product.cpu.abi'])
        size = self._run(['shell', 'wm', 'size'])
        density = self._run(['shell', 'wm', 'density'])
        sizes = re.findall(r'(?:Physical|Override) size:\s*(\d+)x(\d+)', size.stdout.decode('utf-8', errors='replace'))
        densities = re.findall(r'(?:Physical|Override) density:\s*(\d+)', density.stdout.decode('utf-8', errors='replace'))
        sdk_text = sdk.stdout.decode('utf-8', errors='replace').strip()
        if not sizes or not densities or not sdk_text.isdigit():
            raise AndroidToolError('device metadata is incomplete', size)
        if not model.stdout.strip() or not abi.stdout.strip() or any(int(value) <= 0 for value in (*sizes[-1], densities[-1], sdk_text)):
            raise AndroidToolError('device metadata contains invalid values', size)
        return {'model': model.stdout.decode('utf-8', errors='replace').strip(), 'sdk': int(sdk_text),
                'abi': abi.stdout.decode('utf-8', errors='replace').strip(), 'width': int(sizes[-1][0]),
                'height': int(sizes[-1][1]), 'density': int(densities[-1])}

    def launch(self, package):
        package = self._package(package)
        resolved = self._run(['shell', 'cmd', 'package', 'resolve-activity', '--brief',
                              '-a', 'android.intent.action.MAIN', '-c', 'android.intent.category.LAUNCHER', package])
        lines = resolved.stdout.decode('utf-8', errors='replace').splitlines()
        components = [line.strip() for line in lines if re.fullmatch(re.escape(package) + r'/[A-Za-z0-9_.$]+', line.strip())]
        if len(components) != 1:
            raise AndroidToolError('launcher activity could not be resolved', resolved)
        result = self._run(['shell', 'am', 'start', '-W', '-n', components[0]])
        combined = (result.stdout + result.stderr).decode('utf-8', errors='replace')
        if re.search(r'(?im)^\s*(?:Error:|Exception|Status:\s*(?:timeout|error))', combined):
            raise AndroidToolError('Android activity launch failed', result)
        return result

    def pid(self, package):
        result = self.executor.run([self.adb_path, '-s', self.serial, 'shell', 'pidof', self._package(package)], timeout=30)
        self.command_results.append(result)
        if result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip() and not result.timed_out:
            return ''
        self._checked(result)
        value = result.stdout.decode('utf-8', errors='replace').strip()
        if not re.fullmatch(r'[1-9]\d*', value):
            raise AndroidToolError('one running primary process was not found', result)
        return value

    def foreground_package(self):
        result = self._run(['shell', 'dumpsys', 'window', 'windows'])
        text = result.stdout.decode('utf-8', errors='replace')
        package_pattern = r'([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+)/[A-Za-z0-9_.$]+'
        focused = re.findall(r'(?m)^\s*mCurrentFocus=(.*)$', text)
        if focused:
            matches = [re.search(package_pattern, line) for line in focused]
            if len(matches) == 1 and matches[0] is not None:
                return matches[0][1]
            raise AndroidToolError('current focused window is unknown or ambiguous', result)
        result = self._run(['shell', 'dumpsys', 'activity', 'activities'])
        text = result.stdout.decode('utf-8', errors='replace')
        for field in ('topResumedActivity', 'mResumedActivity'):
            resumed = re.findall(r'(?m)^\s*' + field + r'\s*[:=](.*)$', text)
            if resumed:
                matches = [re.search(package_pattern, line) for line in resumed]
                if len(matches) == 1 and matches[0] is not None:
                    return matches[0][1]
                break
        raise AndroidToolError('foreground activity is unknown or ambiguous', result)

    def screenshot(self, path):
        result = self._run(['exec-out', 'screencap', '-p'])
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.png', delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(result.stdout)
            evidence_for_png(temporary)
            temporary.replace(path)
            return evidence_for_png(path)
        except (ValueError, OSError) as error:
            raise AndroidToolError(f'screenshot evidence is invalid: {error}', result) from error
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def logcat(self, since=None, pid=None):
        args = ['logcat', '-d', '-v', 'threadtime']
        if since is not None:
            if not isinstance(since, str) or not re.fullmatch(r'\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d{1,6})?', since):
                raise ValueError('since must be an Android logcat timestamp')
            args.extend(['-T', since])
        if pid is not None:
            if not re.fullmatch(r'[1-9]\d*', str(pid)):
                raise ValueError('invalid logcat process ID')
            args.append(f'--pid={pid}')
        return self._run(args)

    def tap(self, x, y, width, height):
        if any(type(value) is not int for value in (x, y, width, height)) or width <= 0 or height <= 0 or not (0 <= x < width and 0 <= y < height):
            raise ValueError('tap coordinates must be integers inside the confirmed screen bounds')
        return self._run(['shell', 'input', 'tap', str(x), str(y)])

    def dump_ui(self, path):
        remote = '/sdcard/' + make_id('ui') + '.xml'
        self._run(['shell', 'uiautomator', 'dump', remote])
        result = self._run(['exec-out', 'cat', remote])
        try:
            if b'<!DOCTYPE' in result.stdout.upper() or b'<!ENTITY' in result.stdout.upper():
                raise ValueError('XML declarations are not permitted in UI evidence')
            root = ET.fromstring(result.stdout)
            if root.tag != 'hierarchy':
                raise ValueError('UI hierarchy root is missing')
        except (ET.ParseError, ValueError) as error:
            raise AndroidToolError('UI hierarchy evidence is invalid', result) from error
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(result.stdout)
        return {'path': str(path.resolve()), 'sha256': hashlib.sha256(result.stdout).hexdigest(),
                'bytes': len(result.stdout), 'nodes': [dict(node.attrib) for node in root.iter('node')],
                'command': {'argv': result.argv, 'returncode': result.returncode}}

    def install_apks(self, java, bundletool, apks):
        result = self.executor.run([str(java), '-jar', str(bundletool), 'install-apks', f'--apks={apks}',
                                    f'--device-id={self.serial}', f'--adb={self.adb_path}'], timeout=300)
        self.command_results.append(result)
        return self._checked(result)

    def verify_packaged_asset(self, package, asset, expected_sha256, directory, *, apk_name='base.apk'):
        if (not isinstance(asset, str) or not asset.startswith('assets/') or '..' in asset.split('/')
                or not re.fullmatch(r'[A-Za-z0-9._/-]+', asset)):
            raise ValueError('asset must be an explicit relative path inside the package assets')
        if not isinstance(expected_sha256, str) or not re.fullmatch(r'[a-fA-F0-9]{64}', expected_sha256):
            raise ValueError('approved asset SHA-256 is required')
        if not isinstance(apk_name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+\.apk', apk_name):
            raise ValueError('explicit installed APK basename is required')
        paths = self._run(['shell', 'pm', 'path', self._package(package)])
        bases = [line[len('package:'):].strip() for line in paths.stdout.decode('utf-8', errors='replace').splitlines()
                 if line.startswith('package:') and line.strip().endswith('/' + apk_name)]
        if len(bases) != 1 or not re.fullmatch(r'/data/app/[A-Za-z0-9_~+=/.-]+/' + re.escape(apk_name), bases[0]):
            raise AndroidToolError('installed APK path could not be verified', paths)
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        apk = directory / ('installed-' + apk_name)
        self._run(['pull', bases[0], str(apk)], timeout=120)
        try:
            with zipfile.ZipFile(apk) as archive:
                entry = archive.getinfo(asset)
                if entry.file_size > 1024 * 1024:
                    raise ValueError('configuration verification asset is too large')
                data = archive.read(entry)
        except (zipfile.BadZipFile, KeyError, ValueError) as error:
            raise ValueError(f'installed configuration could not be read: {error}') from error
        digest = hashlib.sha256(data).hexdigest()
        return {'verified': digest.lower() == expected_sha256.lower(), 'sha256': digest,
                'asset': asset, 'bytes': len(data), 'installed_apk': str(apk.resolve()),
                'expected_sha256': expected_sha256.lower(), 'scope': 'packaged_configuration'}
