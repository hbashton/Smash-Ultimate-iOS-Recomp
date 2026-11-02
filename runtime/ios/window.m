/* iPadOS window, event pump, Metal layer and native text prompts.
 *
 * SDL owns the UIKit window and handles controller discovery. The guest runs on its own host
 * thread; the main() function returns after installing SDL's display callback so UIKit keeps
 * pumping lifecycle and input events. */
#import <UIKit/UIKit.h>
#import <QuartzCore/CAMetalLayer.h>
#import <SDL3/SDL.h>
#import <SDL3/SDL_metal.h>
#import <SDL3/SDL_system.h>
#include <pthread.h>
#include <stdio.h>
#include <time.h>

#include "../display.h"

__attribute__((noreturn)) void ExitProcess(unsigned code);
void gfx_hotkey(int n);
void rt_upnp_remove(void);
void rt_ios_check_game_data(void);  /* host.c: starts the guest after Files has the user's game data */

enum { VK_BACK = 0x08, VK_TAB = 0x09, VK_RETURN = 0x0D, VK_SHIFT = 0x10, VK_CONTROL = 0x11,
       VK_MENU = 0x12, VK_ESCAPE = 0x1B, VK_SPACE = 0x20, VK_LEFT = 0x25, VK_UP = 0x26,
       VK_RIGHT = 0x27, VK_DOWN = 0x28, VK_DELETE = 0x2E, VK_LWIN = 0x5B, VK_RWIN = 0x5C,
       VK_F1 = 0x70 };

static SDL_Window *window;
static CAMetalLayer *layer;
static UIWindow *ui_window;
static volatile int focused = 1;
static const bool *keys;
static int nkeys;

/* ---- the guest-visible keyboard ------------------------------------------------------------ */
static SDL_Scancode scancode_of(int vk) {
    if (vk >= 'A' && vk <= 'Z') return (SDL_Scancode)(SDL_SCANCODE_A + vk - 'A');
    if (vk >= '1' && vk <= '9') return (SDL_Scancode)(SDL_SCANCODE_1 + vk - '1');
    if (vk == '0') return SDL_SCANCODE_0;
    if (vk >= VK_F1 && vk <= VK_F1 + 11) return (SDL_Scancode)(SDL_SCANCODE_F1 + vk - VK_F1);
    switch (vk) {
    case VK_RETURN: return SDL_SCANCODE_RETURN;
    case VK_BACK: return SDL_SCANCODE_BACKSPACE;
    case VK_DELETE: return SDL_SCANCODE_DELETE;
    case VK_TAB: return SDL_SCANCODE_TAB;
    case VK_ESCAPE: return SDL_SCANCODE_ESCAPE;
    case VK_SPACE: return SDL_SCANCODE_SPACE;
    case VK_LEFT: return SDL_SCANCODE_LEFT;
    case VK_RIGHT: return SDL_SCANCODE_RIGHT;
    case VK_UP: return SDL_SCANCODE_UP;
    case VK_DOWN: return SDL_SCANCODE_DOWN;
    case VK_SHIFT: return SDL_SCANCODE_LSHIFT;
    case VK_CONTROL: return SDL_SCANCODE_LCTRL;
    case VK_MENU: return SDL_SCANCODE_LALT;
    case VK_LWIN: return SDL_SCANCODE_LGUI;
    case VK_RWIN: return SDL_SCANCODE_RGUI;
    }
    return SDL_SCANCODE_UNKNOWN;
}

short GetAsyncKeyState(int vk) {
    if (!keys) return 0;
    SDL_Scancode s = scancode_of(vk);
    int down = s != SDL_SCANCODE_UNKNOWN && (int)s < nkeys && keys[s];
    if (vk == VK_RETURN) down |= keys[SDL_SCANCODE_KP_ENTER];
    if (vk == VK_SHIFT) down |= keys[SDL_SCANCODE_RSHIFT];
    if (vk == VK_CONTROL) down |= keys[SDL_SCANCODE_RCTRL];
    if (vk == VK_MENU) down |= keys[SDL_SCANCODE_RALT];
    return down ? (short)0x8000 : 0;
}

void *GetForegroundWindow(void) { return focused ? (void *)window : NULL; }
void *rt_window(void) { return window; }
void *rt_metal_layer(void) { return (__bridge void *)layer; }

/* ---- iOS file sharing paths ---------------------------------------------------------------- */
static NSString *module_directory(void) {
    NSArray<NSURL *> *urls = [[NSFileManager defaultManager] URLsForDirectory:NSDocumentDirectory
                                                                      inDomains:NSUserDomainMask];
    if (!urls.count) return nil;
    return [[urls[0] path] stringByAppendingPathComponent:@"dump/modules"];
}

static NSString *bundled_module_directory(void) {
    return [[[NSBundle mainBundle] resourcePath] stringByAppendingPathComponent:@"dump/modules"];
}

static NSString *leaf_name(NSString *path) {
    NSString *p = [path stringByReplacingOccurrencesOfString:@"\\" withString:@"/"];
    return p.lastPathComponent;
}

/* Keep the small config writable in Documents, while the large game images stay in the app bundle.
 * A user-imported copy in Documents takes precedence when present. */
static BOOL rebase_romfs_config(void) {
    NSString *dir = module_directory();
    if (!dir) return NO;
    NSString *cfg = [dir stringByAppendingPathComponent:@"romfs.cfg"];
    NSString *source = [NSString stringWithContentsOfFile:cfg encoding:NSUTF8StringEncoding error:nil];
    if (!source) {
        NSString *bundled_cfg = [bundled_module_directory() stringByAppendingPathComponent:@"romfs.cfg"];
        source = [NSString stringWithContentsOfFile:bundled_cfg encoding:NSUTF8StringEncoding error:nil];
    }
    if (!source) return NO;
    NSArray<NSString *> *lines = [source componentsSeparatedByCharactersInSet:[NSCharacterSet newlineCharacterSet]];
    NSMutableArray<NSString *> *clean = [NSMutableArray array];
    for (NSString *line in lines) {
        NSString *s = [line stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
        if (s.length) [clean addObject:s];
    }
    if (!clean.count) return NO;
    NSMutableArray<NSString *> *fixed = [NSMutableArray arrayWithArray:clean];
    BOOL layered = [clean[0] isEqualToString:@"layered"];
    NSString *bundle = bundled_module_directory();
    if (layered) {
        if (clean.count != 4) return NO;
        for (NSUInteger i = 1; i < clean.count; i++) {
            NSString *leaf = leaf_name(clean[i]);
            NSString *local = [dir stringByAppendingPathComponent:leaf];
            NSString *built_in = [bundle stringByAppendingPathComponent:leaf];
            fixed[i] = [[NSFileManager defaultManager] fileExistsAtPath:local] ? local : built_in;
        }
    } else {
        if (clean.count != 2) return NO;
        NSString *leaf = leaf_name(clean[0]);
        NSString *local = [dir stringByAppendingPathComponent:leaf];
        NSString *built_in = [bundle stringByAppendingPathComponent:leaf];
        fixed[0] = [[NSFileManager defaultManager] fileExistsAtPath:local] ? local : built_in;
    }
    NSString *text = [[fixed componentsJoinedByString:@"\n"] stringByAppendingString:@"\n"];
    if (![source isEqualToString:text]) {
        NSError *error = nil;
        if (![text writeToFile:cfg atomically:YES encoding:NSUTF8StringEncoding error:&error]) {
            fprintf(stderr, "[fs] cannot prepare the RomFS config: %s\n", error.localizedDescription.UTF8String);
            return NO;
        }
    }
    if (layered) {
        if (fixed.count < 4) return NO;
        for (NSUInteger i = 1; i < 4; i++)
            if (![[NSFileManager defaultManager] fileExistsAtPath:fixed[i]]) return NO;
    } else {
        if (fixed.count < 2 || ![[NSFileManager defaultManager] fileExistsAtPath:fixed[0]]) return NO;
    }
    return YES;
}

int rt_ios_prepare_data(char *out, size_t cap) {
    NSString *dir = module_directory();
    if (!dir) return 0;
    NSError *error = nil;
    if (![[NSFileManager defaultManager] createDirectoryAtPath:dir withIntermediateDirectories:YES attributes:nil error:&error]) {
        fprintf(stderr, "[fs] cannot create the app's game-data folder: %s\n", error.localizedDescription.UTF8String);
        return 0;
    }
    NSString *bundle = bundled_module_directory();
    NSArray<NSString *> *names = [[NSFileManager defaultManager] contentsOfDirectoryAtPath:bundle error:nil];
    if (!names.count) {
        fprintf(stderr, "[fs] the app bundle contains no recompiled module images\n");
        return 0;
    }
    NSUInteger module_count = 0;
    for (NSString *name in names) {
        if (![name hasSuffix:@".reloc.bin"]) continue;
        NSString *src = [bundle stringByAppendingPathComponent:name];
        NSString *dst = [dir stringByAppendingPathComponent:name];
        NSDictionary *a = [[NSFileManager defaultManager] attributesOfItemAtPath:src error:nil];
        if (!a || ![a[NSFileSize] unsignedLongLongValue]) {
            fprintf(stderr, "[fs] the app bundle's module image %s is missing or empty\n", name.UTF8String);
            return 0;
        }
        module_count++;
        NSDictionary *b = [[NSFileManager defaultManager] attributesOfItemAtPath:dst error:nil];
        if (b && [a[NSFileSize] unsignedLongLongValue] == [b[NSFileSize] unsignedLongLongValue]) continue;
        [[NSFileManager defaultManager] removeItemAtPath:dst error:nil];
        if (![[NSFileManager defaultManager] copyItemAtPath:src toPath:dst error:&error]) {
            fprintf(stderr, "[fs] cannot install the built-in module %s: %s\n", name.UTF8String,
                    error.localizedDescription.UTF8String);
            return 0;
        }
    }
    if (!module_count) {
        fprintf(stderr, "[fs] the app bundle contains no recompiled module images\n");
        return 0;
    }
    NSString *bundled_fonts = [[[NSBundle mainBundle] resourcePath] stringByAppendingPathComponent:@"dump/fonts"];
    NSArray<NSString *> *font_names = [[NSFileManager defaultManager] contentsOfDirectoryAtPath:bundled_fonts error:nil];
    if (font_names.count) {
        NSString *fonts = [[dir stringByDeletingLastPathComponent] stringByAppendingPathComponent:@"fonts"];
        if (![[NSFileManager defaultManager] createDirectoryAtPath:fonts withIntermediateDirectories:YES attributes:nil error:&error]) {
            fprintf(stderr, "[fs] cannot create the app's font folder: %s\n", error.localizedDescription.UTF8String);
            return 0;
        }
        for (NSString *name in font_names) {
            if (![name hasSuffix:@".bfttf"]) continue;
            NSString *src = [bundled_fonts stringByAppendingPathComponent:name];
            NSString *dst = [fonts stringByAppendingPathComponent:name];
            if ([[NSFileManager defaultManager] fileExistsAtPath:dst]) continue;
            if (![[NSFileManager defaultManager] copyItemAtPath:src toPath:dst error:&error]) {
                fprintf(stderr, "[fs] cannot install the built-in font %s: %s\n", name.UTF8String, error.localizedDescription.UTF8String);
                return 0;
            }
        }
    }
    if (out && cap) snprintf(out, cap, "%s", dir.UTF8String);
    return 1;
}

int rt_ios_game_data_ready(void) { return rebase_romfs_config(); }

void rt_window_ios_show_import_help(void) {
    if (!ui_window.rootViewController) return;
    UIAlertController *a = [UIAlertController alertControllerWithTitle:@"Add your game data"
        message:@"This build has no bundled game data. In Files, copy the prepared dump/modules folder from your own Smash 13.0.5 setup into this app's Documents folder. Keep romfs.cfg, romfs.layers, the base NCA and romfs_patch.bin together. If available, copy dump/fonts too, then return here."
        preferredStyle:UIAlertControllerStyleAlert];
    [a addAction:[UIAlertAction actionWithTitle:@"OK" style:UIAlertActionStyleDefault handler:nil]];
    [ui_window.rootViewController presentViewController:a animated:YES completion:nil];
}

/* ---- window and Metal ----------------------------------------------------------------------- */
static void note_size(void) {
    int w = 0, h = 0;
    SDL_GetWindowSizeInPixels(window, &w, &h);
    if (w <= 0 || h <= 0) return;
    layer.drawableSize = CGSizeMake(w, h);
    display_note_size((unsigned)w, (unsigned)h);
}

int rt_window_init(void) {
    SDL_SetHint(SDL_HINT_APP_NAME, "Super Smash Bros. Ultimate");
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
    SDL_SetHint(SDL_HINT_AUTO_UPDATE_JOYSTICKS, "0");
    if (!SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS | SDL_INIT_GAMEPAD)) {
        fprintf(stderr, "[gfx] SDL could not start UIKit: %s\n", SDL_GetError());
        return 0;
    }
    if (!SDL_InitSubSystem(SDL_INIT_AUDIO)) fprintf(stderr, "[audio] SDL could not start: %s\n", SDL_GetError());
    window = SDL_CreateWindow("Super Smash Bros. Ultimate", 1920, 1080,
                              SDL_WINDOW_METAL | SDL_WINDOW_FULLSCREEN | SDL_WINDOW_HIGH_PIXEL_DENSITY);
    if (!window) {
        fprintf(stderr, "[gfx] cannot create the iPad window: %s\n", SDL_GetError());
        return 0;
    }
    SDL_MetalView view = SDL_Metal_CreateView(window);
    layer = view ? (__bridge CAMetalLayer *)SDL_Metal_GetLayer(view) : nil;
    if (!layer) {
        fprintf(stderr, "[gfx] cannot create the iPad Metal layer: %s\n", SDL_GetError());
        return 0;
    }
    layer.pixelFormat = MTLPixelFormatBGRA8Unorm;
    CGColorSpaceRef srgb = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    layer.colorspace = srgb;
    CGColorSpaceRelease(srgb);
    layer.framebufferOnly = NO;
    layer.opaque = YES;
    ui_window = (__bridge UIWindow *)SDL_GetPointerProperty(SDL_GetWindowProperties(window),
                                                            SDL_PROP_WINDOW_UIKIT_WINDOW_POINTER, NULL);
    UIApplication.sharedApplication.idleTimerDisabled = YES;
    keys = SDL_GetKeyboardState(&nkeys);
    note_size();
    display_note_refresh((int)UIScreen.mainScreen.maximumFramesPerSecond);
    fprintf(stderr, "[gfx] iPadOS Metal window ready (%dx%d, %d Hz)\n", (int)layer.drawableSize.width,
            (int)layer.drawableSize.height, (int)UIScreen.mainScreen.maximumFramesPerSecond);
    return 1;
}

static void key_down(const SDL_KeyboardEvent *k) {
    SDL_Scancode s = k->scancode;
    if (s >= SDL_SCANCODE_F1 && s <= SDL_SCANCODE_F6) gfx_hotkey(s - SDL_SCANCODE_F1);
    else if ((k->mod & SDL_KMOD_GUI) && s >= SDL_SCANCODE_1 && s <= SDL_SCANCODE_6) gfx_hotkey(s - SDL_SCANCODE_1);
}

static pthread_mutex_t vblank_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t vblank_cond = PTHREAD_COND_INITIALIZER;
static unsigned long long vblanks;

static void animation_tick(void *unused) {
    (void)unused;
    SDL_Event e;
    while (SDL_PollEvent(&e)) {
        switch (e.type) {
        case SDL_EVENT_QUIT:
            fprintf(stderr, "[app] iOS requested termination\n");
            return;
        case SDL_EVENT_WINDOW_FOCUS_GAINED: focused = 1; break;
        case SDL_EVENT_WINDOW_FOCUS_LOST: focused = 0; break;
        case SDL_EVENT_WINDOW_PIXEL_SIZE_CHANGED:
        case SDL_EVENT_WINDOW_DISPLAY_SCALE_CHANGED: note_size(); break;
        case SDL_EVENT_KEY_DOWN:
            if (!e.key.repeat) key_down(&e.key);
            break;
        default: break;
        }
    }
    pthread_mutex_lock(&vblank_lock);
    vblanks++;
    pthread_cond_broadcast(&vblank_cond);
    pthread_mutex_unlock(&vblank_lock);
    rt_ios_check_game_data();
}

int rt_window_ios_start(void) {
    if (!SDL_SetiOSAnimationCallback(window, 1, animation_tick, NULL)) {
        fprintf(stderr, "[app] cannot start the iPad display callback: %s\n", SDL_GetError());
        return 0;
    }
    return 1;
}

_Noreturn void rt_window_loop(int every, void (*status)(void)) {
    (void)every; (void)status;
    for (;;) SDL_Delay(1000);
}

int rt_window_wait_vblank(int n, int timeout_ms) {
    if (n <= 0) return 0;
    struct timespec until;
    clock_gettime(CLOCK_REALTIME, &until);
    until.tv_sec += timeout_ms / 1000;
    until.tv_nsec += (long)(timeout_ms % 1000) * 1000000;
    if (until.tv_nsec >= 1000000000) { until.tv_sec++; until.tv_nsec -= 1000000000; }
    pthread_mutex_lock(&vblank_lock);
    unsigned long long target = vblanks + (unsigned)n;
    while (vblanks < target) {
        if (pthread_cond_timedwait(&vblank_cond, &vblank_lock, &until) == ETIMEDOUT) break;
    }
    int ready = vblanks >= target;
    pthread_mutex_unlock(&vblank_lock);
    return ready;
}

/* ---- software keyboard --------------------------------------------------------------------- */
static void SDLCALL run_block(void *block) { ((__bridge void (^)(void))block)(); }

int rt_window_ask_text(const char *prompt, const char *initial, char *text, size_t cap) {
    if (!window || !ui_window.rootViewController) return -1;
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    __block int accepted = 0;
    __block NSString *answer = nil;
    NSString *question = [NSString stringWithUTF8String:prompt] ?: @"Enter text";
    NSString *start = [NSString stringWithUTF8String:initial] ?: @"";
    void (^ask)(void) = ^{
        UIViewController *top = ui_window.rootViewController;
        while (top.presentedViewController) top = top.presentedViewController;
        UIAlertController *a = [UIAlertController alertControllerWithTitle:question message:nil
                                                            preferredStyle:UIAlertControllerStyleAlert];
        [a addTextFieldWithConfigurationHandler:^(UITextField *f) { f.text = start; f.autocorrectionType = UITextAutocorrectionTypeNo; }];
        [a addAction:[UIAlertAction actionWithTitle:@"OK" style:UIAlertActionStyleDefault handler:^(UIAlertAction *action) {
            (void)action; accepted = 1; answer = a.textFields.firstObject.text; dispatch_semaphore_signal(done);
        }]];
        [a addAction:[UIAlertAction actionWithTitle:@"Cancel" style:UIAlertActionStyleCancel handler:^(UIAlertAction *action) {
            (void)action; dispatch_semaphore_signal(done);
        }]];
        [top presentViewController:a animated:YES completion:nil];
    };
    if (!SDL_RunOnMainThread(run_block, (__bridge void *)ask, true)) return -1;
    dispatch_semaphore_wait(done, DISPATCH_TIME_FOREVER);
    if (accepted) snprintf(text, cap, "%s", answer.UTF8String ?: "");
    return accepted;
}
