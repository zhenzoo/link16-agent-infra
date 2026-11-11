"""An updater owns its process tree, including silent installer descendants."""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess


class UpdateProcess:
    def __init__(self, command, env):
        self.job = None
        if os.name != "nt":
            self.process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            return
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [("user", ctypes.c_longlong), ("job", ctypes.c_longlong),
                        ("flags", w.DWORD), ("min_ws", ctypes.c_size_t),
                        ("max_ws", ctypes.c_size_t), ("active", w.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", w.DWORD),
                        ("scheduling", w.DWORD)]

        class Limits(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", ctypes.c_ulonglong * 6),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

        api = self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        api.CreateJobObjectW.restype = w.HANDLE
        api.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        api.QueryInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                 w.DWORD, ctypes.c_void_p]
        api.CloseHandle.argtypes = [w.HANDLE]
        self.job = api.CreateJobObjectW(None, None)
        if not self.job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Limits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not api.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error
        try:
            # Assign before any user code executes: even an immediately spawned
            # installer belongs to this job, never to a PID-name kill sweep.
            self.process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW | 0x4)  # CREATE_SUSPENDED
            if not api.AssignProcessToJobObject(self.job, int(self.process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
            resume = ctypes.WinDLL("ntdll").NtResumeProcess
            resume.argtypes, resume.restype = [w.HANDLE], ctypes.c_long
            if resume(int(self.process._handle)) != 0:
                raise OSError("Could not start owned updater")
        except BaseException:
            if hasattr(self, "process"):
                self.process.kill()
                self.process.wait(timeout=2)
            self.close()
            raise

    def pids(self):
        if os.name != 'nt':
            return {self.process.pid}
        # JobObjectBasicProcessIdList: two DWORD counts, then ULONG_PTR IDs.
        data = ctypes.create_string_buffer(8192)
        if not self.job or not self.api.QueryInformationJobObject(self.job, 3, data, len(data), None):
            return set()
        count = ctypes.c_uint32.from_buffer(data, 4).value
        width = ctypes.sizeof(ctypes.c_size_t)
        return {ctypes.c_size_t.from_buffer(data, 8 + i * width).value
                for i in range(min(count, (len(data) - 8) // width))}

    def close(self):
        if os.name == "nt":
            if self.job:
                self.api.CloseHandle(self.job)
                self.job = None
        elif hasattr(self, "process"):
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
