"""Win32 process-memory access for Ys Origin.

A small, dependency-free wrapper over ``ReadProcessMemory`` /
``WriteProcessMemory`` implemented with :mod:`ctypes`. This intentionally does
not depend on ``pymem`` so the client runs on any CPython 3.11+ without needing
a matching binary wheel. ``pymem`` can be swapped in later if desired; the
public surface (``read_int32``, ``read_float``, ``write_int32`` ...) mirrors the
operations ``pymem`` exposes.

All reads/writes operate on *absolute* addresses. Resolve a static offset with
``base_address + offset`` (see :meth:`ProcessMemory.resolve`).
"""

from __future__ import annotations

import ctypes
import struct
from ctypes import wintypes
from typing import Optional

# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class MemoryError_(Exception):
    """Base class for all memory-layer errors."""


class ProcessNotFound(MemoryError_):
    """The target process was not running / could not be opened."""


class ModuleNotFound(MemoryError_):
    """The named module was not found inside the target process."""


class ReadFailed(MemoryError_):
    """A ``ReadProcessMemory`` call failed."""


class WriteFailed(MemoryError_):
    """A ``WriteProcessMemory`` call failed."""


# --------------------------------------------------------------------------- #
# Win32 plumbing
# --------------------------------------------------------------------------- #

# Access rights for OpenProcess.
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_ALL_REQUIRED = (
    PROCESS_QUERY_INFORMATION
    | PROCESS_VM_READ
    | PROCESS_VM_WRITE
    | PROCESS_VM_OPERATION
)

# CreateToolhelp32Snapshot flags.
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_PATH = 260

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * MAX_PATH),
    ]


class MODULEENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", ctypes.c_char * 256),
        ("szExePath", ctypes.c_char * MAX_PATH),
    ]


# Explicit prototypes so 64-bit pointers are not truncated to 32-bit ints.
_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]

_kernel32.Process32First.restype = wintypes.BOOL
_kernel32.Process32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
_kernel32.Process32Next.restype = wintypes.BOOL
_kernel32.Process32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]

_kernel32.Module32First.restype = wintypes.BOOL
_kernel32.Module32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32)]
_kernel32.Module32Next.restype = wintypes.BOOL
_kernel32.Module32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32)]

_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

_kernel32.ReadProcessMemory.restype = wintypes.BOOL
_kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCVOID,
    wintypes.LPVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]

_kernel32.WriteProcessMemory.restype = wintypes.BOOL
_kernel32.WriteProcessMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.LPCVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]

# -- VirtualQueryEx (memory-region enumeration, used by the scanners) -------- #

MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
# Page protections we are willing to read from (R, RW, ExR, ExRW, ExWC).
PAGE_READABLE = 0x02 | 0x04 | 0x20 | 0x40 | 0x80
USERSPACE_MAX = 0x7FFF0000  # 32-bit user-space ceiling


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("__alignment1", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("__alignment2", wintypes.DWORD),
    ]


_kernel32.VirtualQueryEx.restype = ctypes.c_size_t
_kernel32.VirtualQueryEx.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCVOID,
    ctypes.POINTER(MEMORY_BASIC_INFORMATION),
    ctypes.c_size_t,
]


def find_process(name: str) -> int:
    """Return the PID of the first process whose image name matches ``name``.

    Matching is case-insensitive. Raises :class:`ProcessNotFound` if absent.
    """
    snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        raise ProcessNotFound("CreateToolhelp32Snapshot failed")
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        target = name.lower()
        ok = _kernel32.Process32First(snapshot, ctypes.byref(entry))
        while ok:
            exe = entry.szExeFile.decode("ascii", "ignore").lower()
            if exe == target:
                return int(entry.th32ProcessID)
            ok = _kernel32.Process32Next(snapshot, ctypes.byref(entry))
        raise ProcessNotFound(f"No process named {name!r} is running")
    finally:
        _kernel32.CloseHandle(snapshot)


def get_module_info(pid: int, module_name: str) -> tuple[int, int]:
    """Return ``(base_address, size)`` of ``module_name`` within ``pid``.

    Raises :class:`ModuleNotFound` if the module is not loaded.
    """
    snapshot = _kernel32.CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid
    )
    if snapshot == INVALID_HANDLE_VALUE:
        raise ModuleNotFound("CreateToolhelp32Snapshot(MODULE) failed")
    try:
        entry = MODULEENTRY32()
        entry.dwSize = ctypes.sizeof(MODULEENTRY32)
        target = module_name.lower()
        ok = _kernel32.Module32First(snapshot, ctypes.byref(entry))
        while ok:
            mod = entry.szModule.decode("ascii", "ignore").lower()
            if mod == target:
                base = ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value or 0
                return base, int(entry.modBaseSize)
            ok = _kernel32.Module32Next(snapshot, ctypes.byref(entry))
        raise ModuleNotFound(f"Module {module_name!r} not found in pid {pid}")
    finally:
        _kernel32.CloseHandle(snapshot)


def get_module_base(pid: int, module_name: str) -> int:
    """Return just the load base address of ``module_name`` within ``pid``."""
    return get_module_info(pid, module_name)[0]


# --------------------------------------------------------------------------- #
# Public class
# --------------------------------------------------------------------------- #


class ProcessMemory:
    """Open handle to a target process with typed read/write helpers.

    Typical use::

        mem = ProcessMemory.attach("yso_win.exe")
        exp = mem.read_float(mem.resolve(0x7028C0))
        mem.close()

    Or as a context manager::

        with ProcessMemory.attach("yso_win.exe") as mem:
            ...
    """

    def __init__(self, pid: int, handle: int, base_address: int, module_name: str,
                 module_size: int = 0):
        self.pid = pid
        self.handle = handle
        self.base_address = base_address
        self.module_name = module_name
        self.module_size = module_size

    @property
    def module_end(self) -> int:
        """Absolute address just past the end of the module image."""
        return self.base_address + self.module_size

    def in_module(self, address: int) -> bool:
        """True if ``address`` lies inside the module image (a static address)."""
        return self.base_address <= address < self.module_end

    def iter_regions(self):
        """Yield ``(base, size)`` for every committed, readable, non-guard region.

        Walks the target's address space with ``VirtualQueryEx``. Used by the
        scanners to know which memory is safe to read in bulk.
        """
        address = 0
        mbi = MEMORY_BASIC_INFORMATION()
        while address < USERSPACE_MAX:
            ret = _kernel32.VirtualQueryEx(
                self.handle, ctypes.c_void_p(address), ctypes.byref(mbi),
                ctypes.sizeof(mbi),
            )
            if not ret:
                break
            base = mbi.BaseAddress or 0
            size = mbi.RegionSize or 0
            if size == 0:
                break
            protect = mbi.Protect
            if (
                mbi.State == MEM_COMMIT
                and (protect & PAGE_READABLE)
                and not (protect & PAGE_GUARD)
                and not (protect & PAGE_NOACCESS)
            ):
                yield base, size
            address = base + size

    # -- lifecycle ---------------------------------------------------------- #

    @classmethod
    def attach(cls, module_name: str = "yso_win.exe") -> "ProcessMemory":
        """Find the process by image name, open it, and resolve its base."""
        pid = find_process(module_name)
        handle = _kernel32.OpenProcess(PROCESS_ALL_REQUIRED, False, pid)
        if not handle:
            err = ctypes.get_last_error()
            raise ProcessNotFound(
                f"OpenProcess(pid={pid}) failed (WinError {err}). "
                "Try running this client as Administrator."
            )
        try:
            base, size = get_module_info(pid, module_name)
        except MemoryError_:
            _kernel32.CloseHandle(handle)
            raise
        return cls(pid, handle, base, module_name, size)

    def close(self) -> None:
        if self.handle:
            _kernel32.CloseHandle(self.handle)
            self.handle = 0

    def __enter__(self) -> "ProcessMemory":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def is_alive(self) -> bool:
        """Best-effort check that the process is still readable."""
        try:
            self.read_bytes(self.base_address, 2)
            return True
        except MemoryError_:
            return False

    # -- address helpers ---------------------------------------------------- #

    def resolve(self, offset: int) -> int:
        """Turn a static module-relative offset into an absolute address."""
        return self.base_address + offset

    # -- raw I/O ------------------------------------------------------------ #

    def read_bytes(self, address: int, size: int) -> bytes:
        buf = (ctypes.c_char * size)()
        read = ctypes.c_size_t(0)
        ok = _kernel32.ReadProcessMemory(
            self.handle, ctypes.c_void_p(address), buf, size, ctypes.byref(read)
        )
        if not ok or read.value != size:
            err = ctypes.get_last_error()
            raise ReadFailed(
                f"ReadProcessMemory(0x{address:X}, {size}) failed "
                f"(WinError {err}, read {read.value}/{size})"
            )
        return bytes(buf)

    def write_bytes(self, address: int, data: bytes) -> None:
        size = len(data)
        buf = (ctypes.c_char * size).from_buffer_copy(data)
        written = ctypes.c_size_t(0)
        ok = _kernel32.WriteProcessMemory(
            self.handle, ctypes.c_void_p(address), buf, size, ctypes.byref(written)
        )
        if not ok or written.value != size:
            err = ctypes.get_last_error()
            raise WriteFailed(
                f"WriteProcessMemory(0x{address:X}, {size}) failed "
                f"(WinError {err}, wrote {written.value}/{size})"
            )

    # -- typed reads -------------------------------------------------------- #

    def read_int32(self, address: int) -> int:
        return struct.unpack("<i", self.read_bytes(address, 4))[0]

    def read_uint32(self, address: int) -> int:
        return struct.unpack("<I", self.read_bytes(address, 4))[0]

    def read_int16(self, address: int) -> int:
        return struct.unpack("<h", self.read_bytes(address, 2))[0]

    def read_int8(self, address: int) -> int:
        return struct.unpack("<b", self.read_bytes(address, 1))[0]

    def read_float(self, address: int) -> float:
        return struct.unpack("<f", self.read_bytes(address, 4))[0]

    def read_double(self, address: int) -> float:
        return struct.unpack("<d", self.read_bytes(address, 8))[0]

    # yso_win.exe is a 32-bit (WOW64) process, so pointers are 4 bytes.
    pointer_size: int = 4

    def read_pointer(self, address: int) -> int:
        """Read a pointer value sized for the target process (4 bytes / 32-bit)."""
        if self.pointer_size == 8:
            return struct.unpack("<Q", self.read_bytes(address, 8))[0]
        return struct.unpack("<I", self.read_bytes(address, 4))[0]

    # -- typed writes ------------------------------------------------------- #

    def write_int32(self, address: int, value: int) -> None:
        self.write_bytes(address, struct.pack("<i", value))

    def write_uint32(self, address: int, value: int) -> None:
        self.write_bytes(address, struct.pack("<I", value))

    def write_int16(self, address: int, value: int) -> None:
        self.write_bytes(address, struct.pack("<h", value))

    def write_int8(self, address: int, value: int) -> None:
        self.write_bytes(address, struct.pack("<b", value))

    def write_float(self, address: int, value: float) -> None:
        self.write_bytes(address, struct.pack("<f", value))

    def write_double(self, address: int, value: float) -> None:
        self.write_bytes(address, struct.pack("<d", value))

    # -- offset-aware convenience ------------------------------------------- #

    def read_offset_int32(self, offset: int) -> int:
        return self.read_int32(self.resolve(offset))

    def read_offset_float(self, offset: int) -> float:
        return self.read_float(self.resolve(offset))

    def write_offset_int32(self, offset: int, value: int) -> None:
        self.write_int32(self.resolve(offset), value)

    def write_offset_float(self, offset: int, value: float) -> None:
        self.write_float(self.resolve(offset), value)
