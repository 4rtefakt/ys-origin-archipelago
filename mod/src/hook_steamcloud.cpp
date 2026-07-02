// Seed-scoped save redirection for Steam Cloud (ISteamRemoteStorage).
//
// Ys Origin's Steam build saves EXCLUSIVELY through ISteamRemoteStorage (Steam
// Cloud) — the files live in Steam's userdata\<id>\207350\remote\ as yso_NN.bin
// (+ yso_dat.bin), NOT on disk where the CreateFile hook (hook_saveredir.cpp)
// could catch them. So while connected to Archipelago we detour the Remote
// Storage vtable and rewrite each save filename into a per-seed namespace:
//
//     yso_30.bin  ->  archipelago_<seed>/yso_30.bin
//
// (Steam Cloud filenames accept '/'-separated subpaths.) An AP run then never
// reads or overwrites the player's vanilla cloud saves, and the in-game save
// picker — which probes these same calls — sees only this seed's saves.
//
// Interface: STEAMREMOTESTORAGE_INTERFACE_VERSION016 (live-confirmed in
// yso_win.exe + steam_api.dll v06.91.21.57). The vtable slot order below is the
// stable v013-v016 layout; each hooked method logs its first filename so a live
// run confirms the slots and reveals exactly which calls the game makes.
//
// x86 __thiscall methods are detoured as __fastcall (ecx=this, edx=ignored,
// remaining args on the stack) — the standard MinHook thiscall pattern.

#include <windows.h>

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <set>
#include <string>

#include "MinHook.h"

void mod_log(const char* fmt, ...);
extern "C" bool saveredir_active();
extern "C" const char* saveredir_seed_cstr();
extern "C" bool saveredir_match_name(const char* base);

// ISteamRemoteStorage v016 vtable slots (indices into the interface's vtable).
enum {
    kFileWrite = 0,          // bool(const char*, const void*, int32)
    kFileRead = 1,           // int32(const char*, void*, int32)
    kFileForget = 5,         // bool(const char*)
    kFileDelete = 6,         // bool(const char*)
    kFileWriteStreamOpen = 9,  // uint64(const char*)
    kFileExists = 13,        // bool(const char*)
    kFilePersisted = 14,     // bool(const char*)
    kGetFileSize = 15,       // int32(const char*)
    kGetFileTimestamp = 16,  // int64(const char*)
    kGetFileCount = 18,      // int32()
    kGetFileNameAndSize = 19,  // const char*(int iFile, int32* pnSize)
    kVtSlots = 20,
};

typedef bool     (__fastcall* FileWrite_t)(void*, void*, const char*, const void*, int32_t);
typedef int32_t  (__fastcall* FileRead_t)(void*, void*, const char*, void*, int32_t);
typedef bool     (__fastcall* NameBool_t)(void*, void*, const char*);
typedef uint64_t (__fastcall* NameU64_t)(void*, void*, const char*);
typedef int32_t  (__fastcall* NameI32_t)(void*, void*, const char*);
typedef int64_t  (__fastcall* NameI64_t)(void*, void*, const char*);
typedef int32_t  (__fastcall* Count_t)(void*, void*);
typedef const char* (__fastcall* NameAt_t)(void*, void*, int, int32_t*);

static FileWrite_t o_FileWrite = nullptr;
static FileRead_t  o_FileRead = nullptr;
static NameBool_t  o_FileForget = nullptr;
static NameBool_t  o_FileDelete = nullptr;
static NameU64_t   o_FileWriteStreamOpen = nullptr;
static NameBool_t  o_FileExists = nullptr;
static NameBool_t  o_FilePersisted = nullptr;
static NameI32_t   o_GetFileSize = nullptr;
static NameI64_t   o_GetFileTimestamp = nullptr;
static Count_t     o_GetFileCount = nullptr;
static NameAt_t    o_GetFileNameAndSize = nullptr;

static std::mutex g_log_mtx;
static std::set<std::string> g_logged;

// Rewrite a cloud filename into the per-seed namespace. Returns true (and fills
// out) when redirection applies; false to pass the name through untouched.
static bool redirect_cloud(const char* file, char* out, size_t out_sz) {
    if (!file || !saveredir_active()) return false;
    if (!saveredir_match_name(file)) return false;
    if (strstr(file, "archipelago_")) return false;      // already scoped
    int n = snprintf(out, out_sz, "archipelago_%s/%s", saveredir_seed_cstr(), file);
    if (n < 0 || (size_t)n >= out_sz) return false;
    {
        std::lock_guard<std::mutex> lk(g_log_mtx);
        if (g_logged.insert(file).second)
            mod_log("steamcloud: '%s' -> '%s'", file, out);
    }
    return true;
}

// One-time "what did the game call" trace for slots that don't redirect (e.g. a
// name that didn't match the pattern), so a live run reveals the real call set.
static void trace_once(const char* fn, const char* file) {
    if (!file) return;
    std::lock_guard<std::mutex> lk(g_log_mtx);
    std::string key = std::string(fn) + ":" + file;
    if (g_logged.insert(key).second)
        mod_log("steamcloud: %s('%s')%s", fn, file,
                saveredir_active() ? "" : " [inactive]");
}

static bool __fastcall hk_FileWrite(void* self, void* edx, const char* file,
                                    const void* data, int32_t size) {
    char buf[256];
    if (redirect_cloud(file, buf, sizeof(buf)))
        return o_FileWrite(self, edx, buf, data, size);
    trace_once("FileWrite", file);
    return o_FileWrite(self, edx, file, data, size);
}

static int32_t __fastcall hk_FileRead(void* self, void* edx, const char* file,
                                      void* data, int32_t toRead) {
    char buf[256];
    if (redirect_cloud(file, buf, sizeof(buf)))
        return o_FileRead(self, edx, buf, data, toRead);
    trace_once("FileRead", file);
    return o_FileRead(self, edx, file, data, toRead);
}

static bool __fastcall hk_FileForget(void* self, void* edx, const char* file) {
    char buf[256];
    if (redirect_cloud(file, buf, sizeof(buf)))
        return o_FileForget(self, edx, buf);
    return o_FileForget(self, edx, file);
}

static bool __fastcall hk_FileDelete(void* self, void* edx, const char* file) {
    char buf[256];
    if (redirect_cloud(file, buf, sizeof(buf)))
        return o_FileDelete(self, edx, buf);
    return o_FileDelete(self, edx, file);
}

static uint64_t __fastcall hk_FileWriteStreamOpen(void* self, void* edx,
                                                  const char* file) {
    char buf[256];
    if (redirect_cloud(file, buf, sizeof(buf)))
        return o_FileWriteStreamOpen(self, edx, buf);
    trace_once("FileWriteStreamOpen", file);
    return o_FileWriteStreamOpen(self, edx, file);
}

static bool __fastcall hk_FileExists(void* self, void* edx, const char* file) {
    char buf[256];
    if (redirect_cloud(file, buf, sizeof(buf)))
        return o_FileExists(self, edx, buf);
    return o_FileExists(self, edx, file);
}

static bool __fastcall hk_FilePersisted(void* self, void* edx, const char* file) {
    char buf[256];
    if (redirect_cloud(file, buf, sizeof(buf)))
        return o_FilePersisted(self, edx, buf);
    return o_FilePersisted(self, edx, file);
}

static int32_t __fastcall hk_GetFileSize(void* self, void* edx, const char* file) {
    char buf[256];
    if (redirect_cloud(file, buf, sizeof(buf)))
        return o_GetFileSize(self, edx, buf);
    return o_GetFileSize(self, edx, file);
}

static int64_t __fastcall hk_GetFileTimestamp(void* self, void* edx, const char* file) {
    char buf[256];
    if (redirect_cloud(file, buf, sizeof(buf)))
        return o_GetFileTimestamp(self, edx, buf);
    return o_GetFileTimestamp(self, edx, file);
}

// Enumeration — currently LOG-ONLY (pass through). If the save picker lists slots
// via these, the log reveals it and the names it sees; then we filter to present
// only this seed's namespace. If the picker doesn't call these, saves isolate via
// the per-file redirects above and these can be dropped.
static int32_t __fastcall hk_GetFileCount(void* self, void* edx) {
    int32_t n = o_GetFileCount(self, edx);
    {
        std::lock_guard<std::mutex> lk(g_log_mtx);
        if (g_logged.insert("__count").second)
            mod_log("steamcloud: GetFileCount() = %d%s", n,
                    saveredir_active() ? "" : " [inactive]");
    }
    return n;
}

static const char* __fastcall hk_GetFileNameAndSize(void* self, void* edx,
                                                    int iFile, int32_t* pnSize) {
    const char* name = o_GetFileNameAndSize(self, edx, iFile, pnSize);
    if (name) {
        std::lock_guard<std::mutex> lk(g_log_mtx);
        char key[300];
        snprintf(key, sizeof(key), "__enum:%s", name);
        if (g_logged.insert(key).second)
            mod_log("steamcloud: GetFileNameAndSize(%d) = '%s'%s", iFile, name,
                    saveredir_active() ? "" : " [inactive]");
    }
    return name;
}

static bool hook_slot(void** vt, int slot, void* detour, void** orig) {
    if (MH_CreateHook(vt[slot], detour, orig) != MH_OK) return false;
    return MH_EnableHook(vt[slot]) == MH_OK;
}

// This steam_api.dll exports the flat C accessors, NOT a bare SteamRemoteStorage()
// — so resolve the interface the way the game's inlined accessor does:
//   SteamInternal_FindOrCreateUserInterface(SteamAPI_GetHSteamUser(), "..016")
// with the ISteamClient chain as a fallback. (__cdecl on Win32.)
static const char* kRSVersion = "STEAMREMOTESTORAGE_INTERFACE_VERSION016";

static void* resolve_remote_storage(HMODULE steam) {
    typedef int (__cdecl* GetUser_t)();
    typedef int (__cdecl* GetPipe_t)();
    typedef void* (__cdecl* Find_t)(int, const char*);
    typedef void* (__cdecl* Client_t)();
    typedef void* (__cdecl* ClientGetRS_t)(void*, int, int, const char*);
    auto get_user = (GetUser_t)GetProcAddress(steam, "SteamAPI_GetHSteamUser");
    auto find = (Find_t)GetProcAddress(steam, "SteamInternal_FindOrCreateUserInterface");
    if (!get_user) return nullptr;
    int user = get_user();
    if (find) {
        void* rs = find(user, kRSVersion);
        if (rs) return rs;
    }
    // Fallback: ISteamClient::GetISteamRemoteStorage(user, pipe, version).
    auto client_fn = (Client_t)GetProcAddress(steam, "SteamClient");
    auto get_pipe = (GetPipe_t)GetProcAddress(steam, "SteamAPI_GetHSteamPipe");
    auto client_get_rs = (ClientGetRS_t)GetProcAddress(
        steam, "SteamAPI_ISteamClient_GetISteamRemoteStorage");
    if (client_fn && get_pipe && client_get_rs) {
        void* client = client_fn();
        if (client) return client_get_rs(client, user, get_pipe(), kRSVersion);
    }
    return nullptr;
}

// Detour the ISteamRemoteStorage vtable. SteamAPI is initialized by the game
// before it saves, but maybe not the instant the mod loads — so this runs on a
// short retry thread until the interface resolves.
static void install_now() {
    HMODULE steam = GetModuleHandleA("steam_api.dll");
    if (!steam) return;
    void* iface = resolve_remote_storage(steam);
    if (!iface) return;                       // SteamAPI not up yet — retry
    void** vt = *(void***)iface;
    int ok = 0;
    ok += hook_slot(vt, kFileWrite, (void*)&hk_FileWrite, (void**)&o_FileWrite);
    ok += hook_slot(vt, kFileRead, (void*)&hk_FileRead, (void**)&o_FileRead);
    ok += hook_slot(vt, kFileForget, (void*)&hk_FileForget, (void**)&o_FileForget);
    ok += hook_slot(vt, kFileDelete, (void*)&hk_FileDelete, (void**)&o_FileDelete);
    ok += hook_slot(vt, kFileWriteStreamOpen, (void*)&hk_FileWriteStreamOpen,
                    (void**)&o_FileWriteStreamOpen);
    ok += hook_slot(vt, kFileExists, (void*)&hk_FileExists, (void**)&o_FileExists);
    ok += hook_slot(vt, kFilePersisted, (void*)&hk_FilePersisted, (void**)&o_FilePersisted);
    ok += hook_slot(vt, kGetFileSize, (void*)&hk_GetFileSize, (void**)&o_GetFileSize);
    ok += hook_slot(vt, kGetFileTimestamp, (void*)&hk_GetFileTimestamp,
                    (void**)&o_GetFileTimestamp);
    ok += hook_slot(vt, kGetFileCount, (void*)&hk_GetFileCount, (void**)&o_GetFileCount);
    ok += hook_slot(vt, kGetFileNameAndSize, (void*)&hk_GetFileNameAndSize,
                    (void**)&o_GetFileNameAndSize);
    mod_log("steamcloud: hooked ISteamRemoteStorage (iface=%p, %d/11 slots)",
            iface, ok);
}

static DWORD WINAPI install_thread(LPVOID) {
    // Retry for ~60s: steam_api + SteamAPI_Init land early, but not necessarily
    // before this mod's init thread. Install once the interface resolves.
    for (int i = 0; i < 300; i++) {
        HMODULE steam = GetModuleHandleA("steam_api.dll");
        if (steam && resolve_remote_storage(steam)) { install_now(); return 0; }
        Sleep(200);
    }
    mod_log("steamcloud: ISteamRemoteStorage never resolved (cloud redirect off)");
    return 0;
}

void steamcloud_install() {
    CreateThread(nullptr, 0, install_thread, nullptr, 0, nullptr);
}
