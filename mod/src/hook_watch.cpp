// Diagnostic: hardware write-watchpoint on specific g_flags cells.
//
// The static scans couldn't identify which instruction actually grants a chest
// item (the binary has duplicate engine code; the give-item/VM stores we hooked
// never fire). A CPU debug-register (DR) write-watchpoint catches the real
// writer regardless of which function it lives in: when the game writes a
// watched address, the CPU raises #DB, our vectored handler logs the faulting
// EIP (the instruction *after* the writer) + registers.
//
// Watches the first-2F-chest cells: g_flags[0x59] (Celcetan Panacea, abs
// 0x76BA80) and g_flags[0x12E] (box-open flag, abs 0x76BDD4). Reload a save with
// that chest unopened, open it, and the log shows the writer. (The save *load*
// also writes these in bulk and will hit first — its EIP will be in the
// save/load serializer ~0x41F/0x431; the *chest* writer is the later, different
// EIP.)
#include <windows.h>
#include <tlhelp32.h>

void mod_log(const char* fmt, ...);

static const DWORD kWatch0 = 0x0076BA80;  // g_flags[0x59]  Panacea item
static const DWORD kWatch1 = 0x0076BDD4;  // g_flags[0x12E] box-open flag

static LONG CALLBACK WatchVeh(EXCEPTION_POINTERS* ep) {
    if (ep->ExceptionRecord->ExceptionCode == EXCEPTION_SINGLE_STEP) {
        DWORD dr6 = (DWORD)ep->ContextRecord->Dr6;
        if (dr6 & 0xF) {  // one of DR0..DR3 fired
            CONTEXT* c = ep->ContextRecord;
            mod_log("WATCH hit dr6=0x%X (dr0=%d dr1=%d) EIP=0x%08X "
                    "eax=%X ecx=%X edx=%X ebx=%X",
                    dr6, (dr6 & 1) ? 1 : 0, (dr6 & 2) ? 1 : 0,
                    (unsigned)c->Eip, (unsigned)c->Eax, (unsigned)c->Ecx,
                    (unsigned)c->Edx, (unsigned)c->Ebx);
            c->Dr6 = 0;  // ack; DR7 stays armed for the next write
            return EXCEPTION_CONTINUE_EXECUTION;
        }
    }
    return EXCEPTION_CONTINUE_SEARCH;
}

static void arm_thread(DWORD tid) {
    HANDLE h = OpenThread(THREAD_GET_CONTEXT | THREAD_SET_CONTEXT |
                          THREAD_SUSPEND_RESUME, FALSE, tid);
    if (!h) return;
    SuspendThread(h);
    CONTEXT ctx{};
    ctx.ContextFlags = CONTEXT_DEBUG_REGISTERS;
    if (GetThreadContext(h, &ctx)) {
        ctx.Dr0 = kWatch0;
        ctx.Dr1 = kWatch1;
        // DR7: enable L0+L1 (bits 0,2); RW=01 (write) + LEN=11 (4 bytes) for
        // slot0 (bits 16-19) and slot1 (bits 20-23).
        ctx.Dr7 = (1u << 0) | (1u << 2) |
                  (0x1u << 16) | (0x3u << 18) |
                  (0x1u << 20) | (0x3u << 22);
        ctx.ContextFlags = CONTEXT_DEBUG_REGISTERS;
        SetThreadContext(h, &ctx);
    }
    ResumeThread(h);
    CloseHandle(h);
}

void watch_install() {
    AddVectoredExceptionHandler(1, WatchVeh);
    DWORD pid = GetCurrentProcessId(), self = GetCurrentThreadId();
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    THREADENTRY32 te{};
    te.dwSize = sizeof(te);
    int n = 0;
    if (Thread32First(snap, &te)) {
        do {
            if (te.th32OwnerProcessID == pid && te.th32ThreadID != self) {
                arm_thread(te.th32ThreadID);
                n++;
            }
        } while (Thread32Next(snap, &te));
    }
    CloseHandle(snap);
    mod_log("watch_install: armed DR0=0x%X DR1=0x%X on %d threads",
            (unsigned)kWatch0, (unsigned)kWatch1, n);
}
