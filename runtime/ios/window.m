/* iPadOS window, event pump, Metal layer and native text prompts.
 *
 * SDL owns the UIKit window and handles controller discovery. The guest runs on its own host
 * thread; the main() function returns after installing SDL's display callback so UIKit keeps
 * pumping lifecycle and input events. */
#import <UIKit/UIKit.h>
#import <QuartzCore/CAMetalLayer.h>
#import <UniformTypeIdentifiers/UniformTypeIdentifiers.h>
#import <SDL3/SDL.h>
#import <SDL3/SDL_metal.h>
#import <SDL3/SDL_system.h>
#include <pthread.h>
#include <stdlib.h>
#include <stdio.h>
#include <time.h>
#include <limits.h>
#include <stdatomic.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

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
static atomic_int ssbu_import_in_progress = 0;

@interface SSGameDataImportCoordinator : NSObject <UIDocumentPickerDelegate>
@property(nonatomic, weak) UIViewController *presenter;
@property(nonatomic, strong) UIAlertController *progressAlert;
@property(atomic) BOOL cancelled;
@property(atomic) unsigned long long completedBytes;
@property(atomic) unsigned long long totalBytes;
- (void)presentPicker;
- (void)refreshProgress;
- (void)showImportError:(NSError *)error;
@end

static SSGameDataImportCoordinator *ssbu_import_coordinator;

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

static NSString *documents_directory(void) {
    NSArray<NSURL *> *urls = [[NSFileManager defaultManager] URLsForDirectory:NSDocumentDirectory
                                                                      inDomains:NSUserDomainMask];
    return urls.count ? urls[0].path : nil;
}

static NSString *game_data_import_marker(void) {
    NSString *documents = documents_directory();
    return documents ? [documents stringByAppendingPathComponent:@".ssbu-game-data-import-in-progress"] : nil;
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
    NSString *marker = game_data_import_marker();
    if (atomic_load_explicit(&ssbu_import_in_progress, memory_order_acquire) ||
        (marker && [[NSFileManager defaultManager] fileExistsAtPath:marker])) return NO;
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

@interface SSGameDataImportFile : NSObject
@property(nonatomic, strong) NSURL *url;
@property(nonatomic, copy) NSString *relativePath;
@property(nonatomic) unsigned long long size;
@end

@implementation SSGameDataImportFile
@end

static NSError *ssbu_import_error(NSInteger code, NSString *message) {
    return [NSError errorWithDomain:@"SSBUGameDataImport" code:code
                           userInfo:@{ NSLocalizedDescriptionKey: message ?: @"The import could not be completed." }];
}

static NSURL *ssbu_modules_folder(NSURL *selected) {
    NSFileManager *fm = [NSFileManager defaultManager];
    NSMutableArray<NSURL *> *candidates = [NSMutableArray array];
    if ([[selected.lastPathComponent lowercaseString] isEqualToString:@"modules"])
        [candidates addObject:selected];
    [candidates addObject:[selected URLByAppendingPathComponent:@"modules" isDirectory:YES]];
    [candidates addObject:[[selected URLByAppendingPathComponent:@"dump" isDirectory:YES]
                           URLByAppendingPathComponent:@"modules" isDirectory:YES]];
    for (NSURL *candidate in candidates) {
        NSString *cfg = [[candidate path] stringByAppendingPathComponent:@"romfs.cfg"];
        if ([fm fileExistsAtPath:cfg]) return candidate;
    }
    return nil;
}

static NSArray<SSGameDataImportFile *> *ssbu_import_files(NSURL *modules, unsigned long long *total,
                                                           NSError **error) {
    NSFileManager *fm = [NSFileManager defaultManager];
    NSMutableArray<SSGameDataImportFile *> *files = [NSMutableArray array];
    __block NSError *enumerationError = nil;
    NSDirectoryEnumerator<NSURL *> *enumerator = [fm enumeratorAtURL:modules
        includingPropertiesForKeys:@[NSURLIsRegularFileKey, NSURLIsDirectoryKey, NSURLIsSymbolicLinkKey, NSURLFileSizeKey]
        options:NSDirectoryEnumerationSkipsHiddenFiles
        errorHandler:^BOOL(NSURL *url, NSError *itemError) {
            (void)url;
            enumerationError = itemError;
            return NO;
        }];
    if (!enumerator) {
        if (error) *error = ssbu_import_error(1, @"Files could not read that folder. Choose the extracted dump folder again.");
        return nil;
    }
    NSString *prefix = [[modules path] stringByAppendingString:@"/"];
    unsigned long long byteCount = 0;
    for (NSURL *url in enumerator) {
        NSNumber *isRegular = nil, *isDirectory = nil, *isSymlink = nil;
        NSError *resourceError = nil;
        [url getResourceValue:&isRegular forKey:NSURLIsRegularFileKey error:&resourceError];
        [url getResourceValue:&isDirectory forKey:NSURLIsDirectoryKey error:nil];
        [url getResourceValue:&isSymlink forKey:NSURLIsSymbolicLinkKey error:nil];
        if (resourceError) { enumerationError = resourceError; break; }
        if (isSymlink.boolValue || isDirectory.boolValue || !isRegular.boolValue) continue;
        if ([url.lastPathComponent hasSuffix:@".reloc.bin"]) continue; /* Keep the app's compiled modules. */
        if (![url.path hasPrefix:prefix]) {
            enumerationError = ssbu_import_error(2, @"A file in the selected folder has an invalid path.");
            break;
        }
        NSString *relative = [url.path substringFromIndex:prefix.length];
        BOOL safe = relative.length > 0;
        for (NSString *component in relative.pathComponents)
            if ([component isEqualToString:@"."] || [component isEqualToString:@".."] ||
                [component containsString:@"\\"] || [component containsString:@"/"]) safe = NO;
        if (!safe) {
            enumerationError = ssbu_import_error(3, @"A file in the selected folder has an invalid name.");
            break;
        }
        NSDictionary *attributes = [fm attributesOfItemAtPath:url.path error:&resourceError];
        if (!attributes || resourceError || ![attributes[NSFileType] isEqualToString:NSFileTypeRegular]) {
            enumerationError = resourceError ?: ssbu_import_error(4, @"A game-data file could not be read.");
            break;
        }
        unsigned long long size = [attributes[NSFileSize] unsignedLongLongValue];
        if (ULLONG_MAX - byteCount < size) {
            enumerationError = ssbu_import_error(5, @"The selected folder is too large to import.");
            break;
        }
        SSGameDataImportFile *item = [SSGameDataImportFile new];
        item.url = url;
        item.relativePath = relative;
        item.size = size;
        [files addObject:item];
        byteCount += size;
    }
    if (enumerationError) {
        if (error) *error = enumerationError;
        return nil;
    }
    BOOL hasConfig = NO;
    for (SSGameDataImportFile *file in files)
        if ([file.relativePath isEqualToString:@"romfs.cfg"]) { hasConfig = YES; break; }
    if (!hasConfig || files.count < 2 || !byteCount) {
        if (error) *error = ssbu_import_error(6, @"That folder is missing the game files. Choose the extracted dump folder that contains modules/romfs.cfg.");
        return nil;
    }
    [files sortUsingComparator:^NSComparisonResult(SSGameDataImportFile *a, SSGameDataImportFile *b) {
        return [a.relativePath compare:b.relativePath options:NSLiteralSearch];
    }];
    if (total) *total = byteCount;
    return files;
}

static BOOL ssbu_copy_import_file(SSGameDataImportFile *file, NSString *destination,
                                  SSGameDataImportCoordinator *coordinator, NSError **error) {
    NSString *parent = [destination stringByDeletingLastPathComponent];
    NSError *directoryError = nil;
    if (![[NSFileManager defaultManager] createDirectoryAtPath:parent withIntermediateDirectories:YES
                                                     attributes:nil error:&directoryError]) {
        if (error) *error = directoryError;
        return NO;
    }
    NSString *temporary = [parent stringByAppendingPathComponent:
        [NSString stringWithFormat:@".%@.ssbu-importing", NSUUID.UUID.UUIDString]];
    int input = open(file.url.fileSystemRepresentation, O_RDONLY | O_NOFOLLOW);
    if (input < 0) {
        if (error) *error = [NSError errorWithDomain:NSPOSIXErrorDomain code:errno
            userInfo:@{NSFilePathErrorKey: file.url.path ?: @""}];
        return NO;
    }
    struct stat sourceStat;
    int statResult = fstat(input, &sourceStat);
    if (statResult != 0 || !S_ISREG(sourceStat.st_mode) ||
        (unsigned long long)sourceStat.st_size != file.size) {
        int saved = statResult != 0 ? errno : EIO;
        close(input);
        if (error) *error = [NSError errorWithDomain:NSPOSIXErrorDomain code:saved
            userInfo:@{NSFilePathErrorKey: file.url.path ?: @""}];
        return NO;
    }
    int output = open(temporary.fileSystemRepresentation, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
    if (output < 0) {
        int saved = errno;
        close(input);
        if (error) *error = [NSError errorWithDomain:NSPOSIXErrorDomain code:saved
            userInfo:@{NSFilePathErrorKey: temporary}];
        return NO;
    }
    BOOL success = YES;
    unsigned long long copied = 0;
    NSTimeInterval lastProgressRefresh = 0;
    unsigned char buffer[1024 * 1024];
    for (;;) {
        if (coordinator.cancelled) {
            if (error) *error = ssbu_import_error(7, @"The import was stopped.");
            success = NO;
            break;
        }
        ssize_t count = read(input, buffer, sizeof buffer);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) {
            int saved = errno;
            if (error) *error = [NSError errorWithDomain:NSPOSIXErrorDomain code:saved
                userInfo:@{NSFilePathErrorKey: file.url.path ?: @""}];
            success = NO;
            break;
        }
        if (!count) break;
        ssize_t offset = 0;
        while (offset < count) {
            ssize_t written = write(output, buffer + offset, (size_t)(count - offset));
            if (written < 0 && errno == EINTR) continue;
            if (written <= 0) {
                int saved = errno ? errno : EIO;
                if (error) *error = [NSError errorWithDomain:NSPOSIXErrorDomain code:saved
                    userInfo:@{NSFilePathErrorKey: temporary}];
                success = NO;
                break;
            }
            offset += written;
            copied += (unsigned long long)written;
            coordinator.completedBytes += (unsigned long long)written;
            NSTimeInterval now = [NSDate timeIntervalSinceReferenceDate];
            if (now - lastProgressRefresh >= 0.5) {
                lastProgressRefresh = now;
                dispatch_async(dispatch_get_main_queue(), ^{ [coordinator refreshProgress]; });
            }
        }
        if (!success) break;
    }
    if (success && copied != file.size) {
        if (error) *error = ssbu_import_error(8, @"A game-data file changed while it was being copied.");
        success = NO;
    }
    if (success && fsync(output) != 0) {
        int saved = errno;
        if (error) *error = [NSError errorWithDomain:NSPOSIXErrorDomain code:saved
            userInfo:@{NSFilePathErrorKey: temporary}];
        success = NO;
    }
    close(input);
    if (close(output) != 0 && success) {
        int saved = errno;
        if (error) *error = [NSError errorWithDomain:NSPOSIXErrorDomain code:saved
            userInfo:@{NSFilePathErrorKey: temporary}];
        success = NO;
    }
    if (success && rename(temporary.fileSystemRepresentation, destination.fileSystemRepresentation) != 0) {
        int saved = errno;
        if (error) *error = [NSError errorWithDomain:NSPOSIXErrorDomain code:saved
            userInfo:@{NSFilePathErrorKey: destination}];
        success = NO;
    }
    if (!success) unlink(temporary.fileSystemRepresentation);
    return success;
}

@implementation SSGameDataImportCoordinator

- (UIViewController *)topController {
    UIViewController *top = ui_window.rootViewController;
    while (top.presentedViewController) top = top.presentedViewController;
    return top;
}

- (void)presentPicker {
    if (!ui_window.rootViewController) return;
    UIViewController *top = [self topController];
    self.presenter = top;
    UIDocumentPickerViewController *picker = [[UIDocumentPickerViewController alloc]
        initForOpeningContentTypes:@[UTTypeFolder] asCopy:NO];
    picker.delegate = self;
    picker.allowsMultipleSelection = NO;
    picker.shouldShowFileExtensions = YES;
    picker.modalPresentationStyle = UIModalPresentationFormSheet;
    [top presentViewController:picker animated:YES completion:nil];
}

- (void)documentPicker:(UIDocumentPickerViewController *)controller didPickDocumentsAtURLs:(NSArray<NSURL *> *)urls {
    NSURL *selected = urls.firstObject;
    if (!selected) {
        [self showImportError:ssbu_import_error(9, @"No folder was selected.")];
        return;
    }
    [controller dismissViewControllerAnimated:YES completion:^{ [self beginImportFromURL:selected]; }];
}

- (void)documentPickerWasCancelled:(UIDocumentPickerViewController *)controller {
    [controller dismissViewControllerAnimated:YES completion:^{
        [self showImportError:ssbu_import_error(15, @"No folder was selected.")];
    }];
}

- (void)showProgress {
    self.completedBytes = 0;
    self.progressAlert = [UIAlertController alertControllerWithTitle:@"Importing game data"
        message:@"Checking the selected folder…" preferredStyle:UIAlertControllerStyleAlert];
    __weak SSGameDataImportCoordinator *weakSelf = self;
    [self.progressAlert addAction:[UIAlertAction actionWithTitle:@"Stop import" style:UIAlertActionStyleCancel
        handler:^(UIAlertAction *action) { (void)action; weakSelf.cancelled = YES; }]];
    UIViewController *top = [self topController];
    if (top) [top presentViewController:self.progressAlert animated:YES completion:nil];
}

- (void)refreshProgress {
    unsigned long long done = self.completedBytes;
    unsigned long long total = self.totalBytes;
    if (!self.progressAlert || !total) return;
    double gib = 1024.0 * 1024.0 * 1024.0;
    self.progressAlert.message = [NSString stringWithFormat:@"Copying game files…\n%.1f / %.1f GB", done / gib, total / gib];
}

- (void)beginImportFromURL:(NSURL *)selected {
    self.cancelled = NO;
    [self showProgress];
    BOOL accessGranted = [selected startAccessingSecurityScopedResource];
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        __block NSError *failure = nil;
        __block BOOL success = NO;
        NSFileCoordinator *fileCoordinator = [[NSFileCoordinator alloc] initWithFilePresenter:nil];
        [fileCoordinator coordinateReadingItemAtURL:selected options:NSFileCoordinatorReadingWithoutChanges
            error:&failure byAccessor:^(NSURL *coordinatedURL) {
                NSError *listingError = nil;
                NSURL *sourceModules = ssbu_modules_folder(coordinatedURL);
                if (!sourceModules) {
                    failure = ssbu_import_error(10, @"Choose the extracted dump folder. It must contain a modules folder with romfs.cfg inside.");
                    return;
                }
                unsigned long long total = 0;
                NSArray<SSGameDataImportFile *> *files = ssbu_import_files(sourceModules, &total, &listingError);
                if (!files) { failure = listingError; return; }
                if (self.cancelled) {
                    failure = ssbu_import_error(7, @"The import was stopped.");
                    return;
                }
                self.totalBytes = total;
                dispatch_async(dispatch_get_main_queue(), ^{ [self refreshProgress]; });
                NSString *marker = game_data_import_marker();
                NSData *markerData = [NSData dataWithBytes:"importing" length:9];
                if (!marker || ![markerData writeToFile:marker atomically:YES]) {
                    failure = ssbu_import_error(11, @"The app could not prepare its game-data folder. Check iPad storage and try again.");
                    return;
                }
                atomic_store_explicit(&ssbu_import_in_progress, 1, memory_order_release);
                NSString *destinationModules = module_directory();
                if (!destinationModules) {
                    [[NSFileManager defaultManager] removeItemAtPath:marker error:nil];
                    atomic_store_explicit(&ssbu_import_in_progress, 0, memory_order_release);
                    failure = ssbu_import_error(12, @"The app's Documents folder is unavailable.");
                    return;
                }
                NSUInteger completedFiles = 0;
                for (SSGameDataImportFile *file in files) {
                    if (self.cancelled) {
                        failure = ssbu_import_error(7, @"The import was stopped.");
                        break;
                    }
                    NSString *destination = [destinationModules stringByAppendingPathComponent:file.relativePath];
                    if (!ssbu_copy_import_file(file, destination, self, &failure)) break;
                    completedFiles++;
                }
                if (failure) {
                    if (!completedFiles) {
                        [[NSFileManager defaultManager] removeItemAtPath:marker error:nil];
                        atomic_store_explicit(&ssbu_import_in_progress, 0, memory_order_release);
                    }
                    return;
                }
                if (![[NSFileManager defaultManager] removeItemAtPath:marker error:&failure]) return;
                atomic_store_explicit(&ssbu_import_in_progress, 0, memory_order_release);
                if (!rebase_romfs_config()) {
                    NSData *retryMarker = [NSData dataWithBytes:"retry" length:5];
                    [retryMarker writeToFile:marker atomically:YES];
                    atomic_store_explicit(&ssbu_import_in_progress, 1, memory_order_release);
                    failure = ssbu_import_error(13, @"The files copied, but required game data is still missing. Choose the dump folder again and let the import finish.");
                    return;
                }
                success = YES;
            }];
        if (accessGranted) [selected stopAccessingSecurityScopedResource];
        dispatch_async(dispatch_get_main_queue(), ^{
            UIAlertController *progress = self.progressAlert;
            self.progressAlert = nil;
            void (^finish)(void) = ^{
                if (success) {
                    ssbu_import_coordinator = nil;
                } else {
                    [self showImportError:failure ?: ssbu_import_error(14, @"The import could not be completed. Choose the dump folder again to retry.")];
                }
            };
            if (progress.presentingViewController)
                [progress dismissViewControllerAnimated:YES completion:finish];
            else
                finish();
        });
    });
}

- (void)showImportError:(NSError *)error {
    NSString *message = error.localizedDescription ?: @"The import could not be completed.";
    if ([error.domain isEqualToString:@"SSBUGameDataImport"] && error.code == 7)
        message = @"Import paused. The partial copy cannot start the game. Choose the same dump folder again and let the import finish.";
    else if ([error.domain isEqualToString:NSPOSIXErrorDomain] && error.code == ENOSPC)
        message = @"There isn't enough free space on the iPad to finish copying the game data. Free up storage, then choose the dump folder again.";
    else if ([error.domain isEqualToString:@"SSBUGameDataImport"] && error.code != 10 && error.code != 6)
        message = [message stringByAppendingString:@" Choose the extracted dump folder again to retry."];
    UIAlertController *alert = [UIAlertController alertControllerWithTitle:@"Game data wasn't imported"
        message:message preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:@"Choose Folder" style:UIAlertActionStyleDefault
        handler:^(UIAlertAction *action) { (void)action; [self presentPicker]; }]];
    [alert addAction:[UIAlertAction actionWithTitle:@"Later" style:UIAlertActionStyleCancel handler:nil]];
    UIViewController *top = [self topController];
    if (top) [top presentViewController:alert animated:YES completion:nil];
}

@end

void rt_window_ios_show_import_help(void) {
    if (!ui_window.rootViewController || ssbu_import_coordinator) return;
    ssbu_import_coordinator = [SSGameDataImportCoordinator new];
    BOOL interrupted = [[NSFileManager defaultManager] fileExistsAtPath:game_data_import_marker() ?: @""];
    NSString *message = interrupted
        ? @"A previous import was interrupted. Choose the extracted dump folder again to complete it. The folder must contain modules/romfs.cfg. Keep the app open while the files copy; the game starts when they are ready."
        : @"In Files, extract the separate game-data ZIP. Then choose its top-level dump folder (the one containing modules). The app copies the files here and starts the game when the import is complete.";
    UIAlertController *a = [UIAlertController alertControllerWithTitle:@"Import Smash game data"
        message:message
        preferredStyle:UIAlertControllerStyleAlert];
    [a addAction:[UIAlertAction actionWithTitle:@"Choose dump folder" style:UIAlertActionStyleDefault
        handler:^(UIAlertAction *action) { (void)action; [ssbu_import_coordinator presentPicker]; }]];
    [a addAction:[UIAlertAction actionWithTitle:@"Later" style:UIAlertActionStyleCancel handler:nil]];
    a.preferredAction = a.actions.firstObject;
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
    /* The normal present path only renders to the drawable. Let Core Animation optimize it
     * for display; permit readback only when the optional window-capture diagnostic needs it. */
    layer.framebufferOnly = !(getenv("SSBU_CAPTURE") && getenv("SSBU_CAPTURE_WINDOW"));
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
