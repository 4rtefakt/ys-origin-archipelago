// Seed-scoped save redirection.
//
// While connected to Archipelago, the game's save files are transparently
// redirected into a per-seed subfolder next to where they'd normally live:
//
//     <save dir>/<file>            ->  <save dir>/archipelago_<seed>/<file>
//
// so an AP run NEVER overwrites (or reads) the player's vanilla saves, and
// every multiworld seed keeps its own save set — reconnecting to the same room
// finds its own saves again. Disconnected / vanilla play is untouched (no seed
// -> no redirection), and `save_redirect=0` in yso_ap.cfg disables it entirely.
//
// Implementation: MinHook detours on kernel32 CreateFileA/W (the CRT's fopen
// lands there too). A path is redirected when its FILENAME contains the
// `save_pattern` substring (default "sav", case-insensitive) — tune the pattern
// in yso_ap.cfg if the game's save names don't match. Redirected opens are
// logged (first sighting per file) so a live session can verify the filter
// quickly; if the log shows no "saveredir:" lines after saving in-game, the
// pattern needs adjusting.

#include <windows.h>

#include <cstdio>
#include <cstring>
#include <mutex>
#include <set>
#include <string>

#include "MinHook.h"

void mod_log(const char* fmt, ...);

static bool g_enabled = true;               // save_redirect (cfg; default on)
static char g_pattern[32] = "sav";          // save_pattern (cfg)
static char g_seed[64] = "";                // sanitized AP seed ("" = no redirect)
static std::mutex g_mtx;
static std::set<std::string> g_logged;      // one log line per unique file

typedef HANDLE(WINAPI* CreateFileA_t)(LPCSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES,
                                      DWORD, DWORD, HANDLE);
typedef HANDLE(WINAPI* CreateFileW_t)(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES,
                                      DWORD, DWORD, HANDLE);
static CreateFileA_t o_CreateFileA = nullptr;
static CreateFileW_t o_CreateFileW = nullptr;

// -- config / seed (called from hook_ap.cpp) --------------------------------- #

extern "C" void saveredir_config(int enabled, const char* pattern) {
    if (enabled >= 0) g_enabled = (enabled != 0);
    if (pattern && *pattern) {
        strncpy(g_pattern, pattern, sizeof(g_pattern) - 1);
        g_pattern[sizeof(g_pattern) - 1] = '\0';
    }
}

extern "C" void saveredir_set_seed(const char* seed) {
    size_t n = 0;
    for (const char* p = seed; *p && n < sizeof(g_seed) - 1; ++p) {
        char c = *p;
        bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                  (c >= '0' && c <= '9') || c == '-' || c == '_';
        g_seed[n++] = ok ? c : '_';
    }
    g_seed[n] = '\0';
    mod_log("saveredir: seed '%s' — saves go to archipelago_%s%s",
            g_seed, g_seed, g_enabled ? "" : " (DISABLED via cfg)");
}

// -- path rewrite ------------------------------------------------------------ #

// True if `base` (a filename) contains g_pattern, case-insensitively.
static bool matches_pattern(const char* base) {
    size_t pl = strlen(g_pattern);
    if (!pl) return false;
    for (const char* p = base; *p; ++p) {
        size_t i = 0;
        while (i < pl && p[i] &&
               (char)tolower((unsigned char)p[i]) ==
                   (char)tolower((unsigned char)g_pattern[i]))
            i++;
        if (i == pl) return true;
    }
    return false;
}

// Rewrite `path` into `out` (redirected into the per-seed folder), creating the
// folder on first use. Returns false when redirection doesn't apply.
static bool redirect_path(const char* path, char* out, size_t out_sz) {
    if (!g_enabled || !g_seed[0] || !path) return false;
    const char* base = path;
    for (const char* p = path; *p; ++p)
        if (*p == '\\' || *p == '/') base = p + 1;
    if (!*base || !matches_pattern(base)) return false;
    if (strstr(path, "archipelago_")) return false;      // already redirected
    char dir[MAX_PATH * 2];
    size_t dlen = (size_t)(base - path);
    if (dlen >= sizeof(dir)) return false;
    memcpy(dir, path, dlen);
    dir[dlen] = '\0';
    int n = snprintf(out, out_sz, "%sarchipelago_%s", dir, g_seed);
    if (n < 0 || (size_t)n >= out_sz) return false;
    CreateDirectoryA(out, nullptr);                       // idempotent
    n = snprintf(out, out_sz, "%sarchipelago_%s\\%s", dir, g_seed, base);
    if (n < 0 || (size_t)n >= out_sz) return false;
    {
        std::lock_guard<std::mutex> lk(g_mtx);
        if (g_logged.insert(base).second)
            mod_log("saveredir: '%s' -> '%s'", path, out);
    }
    return true;
}

// -- hooks -------------------------------------------------------------------- #

static HANDLE WINAPI hk_CreateFileA(LPCSTR name, DWORD access, DWORD share,
                                    LPSECURITY_ATTRIBUTES sa, DWORD disp,
                                    DWORD flags, HANDLE tmpl) {
    char buf[MAX_PATH * 2];
    if (redirect_path(name, buf, sizeof(buf)))
        return o_CreateFileA(buf, access, share, sa, disp, flags, tmpl);
    return o_CreateFileA(name, access, share, sa, disp, flags, tmpl);
}

static HANDLE WINAPI hk_CreateFileW(LPCWSTR name, DWORD access, DWORD share,
                                    LPSECURITY_ATTRIBUTES sa, DWORD disp,
                                    DWORD flags, HANDLE tmpl) {
    // Narrow, rewrite, widen. Save paths are plain ANSI on this engine; if the
    // conversion fails, fall through untouched.
    char narrow[MAX_PATH * 2];
    if (name && WideCharToMultiByte(CP_ACP, 0, name, -1, narrow, sizeof(narrow),
                                    nullptr, nullptr) > 0) {
        char buf[MAX_PATH * 2];
        if (redirect_path(narrow, buf, sizeof(buf))) {
            wchar_t wide[MAX_PATH * 2];
            if (MultiByteToWideChar(CP_ACP, 0, buf, -1, wide,
                                    MAX_PATH * 2) > 0)
                return o_CreateFileW(wide, access, share, sa, disp, flags, tmpl);
        }
    }
    return o_CreateFileW(name, access, share, sa, disp, flags, tmpl);
}

void saveredir_install() {
    // MH_Initialize already ran (hook_d3d9_install); just add our detours.
    HMODULE k32 = GetModuleHandleA("kernel32.dll");
    if (!k32) return;
    void* pA = (void*)GetProcAddress(k32, "CreateFileA");
    void* pW = (void*)GetProcAddress(k32, "CreateFileW");
    MH_STATUS a = pA ? MH_CreateHook(pA, (void*)&hk_CreateFileA,
                                     (void**)&o_CreateFileA) : MH_ERROR_NOT_EXECUTABLE;
    MH_STATUS w = pW ? MH_CreateHook(pW, (void*)&hk_CreateFileW,
                                     (void**)&o_CreateFileW) : MH_ERROR_NOT_EXECUTABLE;
    MH_EnableHook(MH_ALL_HOOKS);
    mod_log("saveredir: CreateFileA=%d CreateFileW=%d (0=ok)", (int)a, (int)w);
}
