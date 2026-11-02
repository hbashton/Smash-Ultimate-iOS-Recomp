/* Offline check of the VP9 path: replays recorded NVDEC jobs (SSBU_DUMP_HOST1X files) through
 * the header writer and libvpx, reporting each frame and saving some as BMPs.
 *   vp9test <dump dir> <out dir> [frames to save, e.g. 0,39,100]
 * Mirrors runtime/gpu_host1x.c's job handling (frames wait for the next job's "last shown"). */
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include <vpx/vpx_decoder.h>
#include <vpx/vp8dx.h>

#include <math.h>
#include <process.h>

#include "../../runtime/gpu_vp9.c"

void *const vpx_crt_imports[] = {(void *)_beginthreadex, (void *)round};   /* (see gpu_host1x.c) */

typedef uint8_t u8; typedef uint32_t u32; typedef uint64_t u64;

typedef struct Job { u32 regs[0x800]; u8 *blob[0x800]; u32 blob_size[0x800]; } Job;

static int load(const char *path, Job *j) {
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    u32 head[2];
    memset(j, 0, sizeof *j);
    if (fread(head, 4, 2, f) != 2 || head[0] != 0x31583148 || fread(j->regs, 4, head[1], f) != head[1]) { fclose(f); return 0; }
    u32 m, size;
    while (fread(&m, 4, 1, f) == 1 && fread(&size, 4, 1, f) == 1 && m < 0x800) {
        j->blob[m] = malloc(size ? size : 1);
        j->blob_size[m] = size;
        if (fread(j->blob[m], 1, size, f) != size) break;
    }
    fclose(f);
    return 1;
}

static u32 rd32(const u8 *p) { u32 v; memcpy(&v, p, 4); return v; }
static int16_t rd16(const u8 *p) { int16_t v; memcpy(&v, p, 2); return v; }

static void probs_from_engine(const u8 *e, Vp9Probs *p) {   /* (as in gpu_host1x.c) */
    vp9_default_probs(p);
    memcpy(p->tx8, e + 0x470, 2); memcpy(p->tx16, e + 0x472, 4); memcpy(p->tx32, e + 0x476, 6);
    for (int i = 0; i < 4 * 2 * 2 * 6 * 6; i++) memcpy(&p->coef[0][0][0][0][0][0] + 3 * i, e + 0x5A0 + 4 * i, 3);
    memcpy(p->skip, e + 0x537, 3);
    for (int i = 0; i < 7; i++) memcpy(p->inter_mode[i], e + 0x400 + 4 * i, 3);
    memcpy(p->interp_filter, e + 0x52A, 8); memcpy(p->is_inter, e + 0x41C, 4); memcpy(p->comp_mode, e + 0x532, 5);
    memcpy(p->single_ref, e + 0x580, 10); memcpy(p->comp_ref, e + 0x58A, 5);
    for (int i = 0; i < 4; i++) { memcpy(p->y_mode[i], e + 0x480 + 8 * i, 8); p->y_mode[i][8] = e[0x47C + i]; }
    for (int i = 0; i < 16; i++) memcpy(p->partition[i], e + 0x4E0 + 4 * i, 3);
    memcpy(p->mv_joint, e + 0x53B, 3); memcpy(p->mv_sign, e + 0x53E, 2); memcpy(p->mv_class0_bit, e + 0x540, 2);
    memcpy(p->mv_fr, e + 0x542, 6); memcpy(p->mv_class0_hp, e + 0x548, 2); memcpy(p->mv_hp, e + 0x54A, 2);
    memcpy(p->mv_class, e + 0x54C, 20); memcpy(p->mv_class0_fr, e + 0x560, 12); memcpy(p->mv_bits, e + 0x56C, 20);
}

static void save_bmp(const char *path, const vpx_image_t *img) {
    u32 w = img->d_w, h = img->d_h, row = (w * 3 + 3) & ~3u;
    FILE *f = fopen(path, "wb");
    if (!f) return;
    u8 hdr[54] = {'B', 'M'};
    u32 size = 54 + row * h;
    memcpy(hdr + 2, &size, 4); hdr[10] = 54; hdr[14] = 40;
    memcpy(hdr + 18, &w, 4); memcpy(hdr + 22, &h, 4); hdr[26] = 1; hdr[28] = 24;
    fwrite(hdr, 1, 54, f);
    u8 *line = calloc(1, row);
    for (int y = (int)h - 1; y >= 0; y--) {
        for (u32 x = 0; x < w; x++) {
            int Y = (img->planes[0][y * img->stride[0] + x] - 16) * 298;
            int U = img->planes[1][(y / 2) * img->stride[1] + x / 2] - 128, V = img->planes[2][(y / 2) * img->stride[2] + x / 2] - 128;
            int c[3] = {(Y + 516 * U + 128) >> 8, (Y - 100 * U - 208 * V + 128) >> 8, (Y + 409 * V + 128) >> 8};
            for (int k = 0; k < 3; k++) line[3 * x + k] = (u8)(c[k] < 0 ? 0 : c[k] > 255 ? 255 : c[k]);
        }
        fwrite(line, 1, row, f);
    }
    free(line);
    fclose(f);
}

/* The specification's boolean decoder (9.2), to check the writer's output against. */
typedef struct Rd { const u8 *p; u32 size, pos, value, range; int bits; } Rd;
static int rd_bit(Rd *r) { int b = r->pos < r->size * 8 ? (r->p[r->pos >> 3] >> (7 - (r->pos & 7))) & 1 : 0; r->pos++; return b; }
static void rd_init(Rd *r, const u8 *p, u32 size) { memset(r, 0, sizeof *r); r->p = p; r->size = size; for (int i = 0; i < 8; i++) r->value = (r->value << 1) | (u32)rd_bit(r); r->range = 255; }
static int rd_bool(Rd *r, int prob) {
    u32 split = 1 + (((r->range - 1) * (u32)prob) >> 8);
    int bit;
    if (r->value < split) { r->range = split; bit = 0; } else { r->range -= split; r->value -= split; bit = 1; }
    while (r->range < 128) { r->value = (r->value << 1) | (u32)rd_bit(r); r->range <<= 1; }
    return bit;
}

static int self_test(void) {
    /* random symbols through the writer and back */
    u8 buf[4096];
    int bits[2000], probs[2000];
    unsigned seed = 1;
    Bool w;
    bool_start(&w, buf, sizeof buf);
    for (int i = 0; i < 2000; i++) {
        seed = seed * 1103515245 + 12345;
        probs[i] = 1 + (int)((seed >> 16) % 255);
        bits[i] = (int)((seed >> 8) % 256) >= probs[i];
        bool_put(&w, bits[i], probs[i]);
    }
    u32 n = bool_end(&w);
    Rd r;
    rd_init(&r, buf, n);
    for (int i = 0; i < 2000; i++)
        if (rd_bool(&r, probs[i]) != bits[i]) { printf("bool coder mismatch at symbol %d\n", i); return 0; }
    /* every probability reachable from every other through the delta coding */
    for (int from = 1; from < 256; from++)
        for (int to = 1; to < 256; to++)
            if (to != from && vp9_inv_remap_prob(vp9_remap_prob(to, from), from) != to) { printf("remap %d -> %d fails\n", from, to); return 0; }
    printf("bool coder and probability deltas: ok (%u bytes for 2000 symbols)\n", n);
    return 1;
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: vp9test <dump dir> <out dir> [frames]\n"); return 1; }
    if (!self_test()) return 1;
    int save[4096] = {0};
    if (argc > 3) for (char *s = strtok(argv[3], ","); s; s = strtok(NULL, ",")) { int n = atoi(s); if (n >= 0 && n < 4096) save[n] = 1; }
    vpx_codec_ctx_t vpx;
    vpx_codec_dec_cfg_t cfg = {4, 0, 0};
    if (vpx_codec_dec_init(&vpx, vpx_codec_vp9_dx(), &cfg, 0)) { fprintf(stderr, "libvpx init failed\n"); return 1; }
    static Job jobs[2];
    static Vp9Slots slots;
    Vp9Probs contexts[4];
    int ok = 0, bad = 0, have_probs = 0;
    for (int n = 0;; n++) {
        char path[1024];
        snprintf(path, sizeof path, "%s/nvdec_%05d.bin", argv[1], n);
        Job *j = &jobs[n & 1], *next = &jobs[(n + 1) & 1];
        if (n == 0 && !load(path, j)) { fprintf(stderr, "no %s\n", path); return 1; }
        snprintf(path, sizeof path, "%s/nvdec_%05d.bin", argv[1], n + 1);
        int has_next = load(path, next);
        const u8 *pic = j->blob[0x101];
        if (!pic) break;
        u32 flags = rd32(pic + 0x68);
        int show = has_next ? (rd32(next->blob[0x101] + 0x68) & 0x10) != 0 : 1;
        Vp9Frame f;
        memset(&f, 0, sizeof f);
        f.bit_depth = 8; f.show = (u8)show;
        f.key = flags & 1; f.intra_only = (flags & 0x20) != 0; f.error_resilient = (flags & 8) != 0;
        f.width = (uint16_t)rd16(pic + 0x60); f.height = (uint16_t)rd16(pic + 0x62);
        for (int i = 0; i < 3; i++) f.sign_bias[i] = pic[0x6D + i] & 1;
        f.allow_hp = pic[0x78] & 1; f.interp_filter = pic[0x79]; f.reference_mode = pic[0x7A]; f.tx_mode = pic[0x77];
        f.lf_level = pic[0x70] & 63; f.lf_sharpness = pic[0x71] & 7; f.lf_delta_enabled = pic[0xE4] & 1; f.lf_delta_update = f.lf_delta_enabled;
        for (int i = 0; i < 4; i++) { f.lf_ref_update[i] = 1; f.lf_ref[i] = (int8_t)pic[0xE5 + i]; }
        for (int i = 0; i < 2; i++) { f.lf_mode_update[i] = 1; f.lf_mode[i] = (int8_t)pic[0xE9 + i]; }
        f.base_q = pic[0x72]; f.dq_y_dc = (int8_t)pic[0x73]; f.dq_uv_ac = (int8_t)pic[0x74]; f.dq_uv_dc = (int8_t)pic[0x75];
        f.tile_cols_log2 = pic[0x7E]; f.tile_rows_log2 = pic[0x7F];
        Vp9Probs probs;
        if (j->blob[0x170] && j->blob_size[0x170] >= 0xEA0) { probs_from_engine(j->blob[0x170], &probs); have_probs = 1; }
        else vp9_default_probs(&probs);
        u64 refs[3], current = (u64)j->regs[0x10F] << 8;
        for (int i = 0; i < 3; i++) refs[i] = (u64)j->regs[0x10C + i] << 8;
        vp9_assign_slots(&slots, &f, refs, current);
        if (f.key || f.intra_only || f.error_resilient) {
            Vp9Probs def; vp9_default_probs(&def);
            for (int c = 0; c < 4; c++) contexts[c] = def;
            f.reset_context = 3; f.context_idx = 0;
        } else {
            int best = 0;
            for (int c = 1; c < 4; c++) if (vp9_context_score(&contexts[c], &probs) > vp9_context_score(&contexts[best], &probs)) best = c;
            f.context_idx = (u8)best;
        }
        f.refresh_context = !f.error_resilient; f.parallel = 1;
        static u8 frame[8 << 20], compressed[0x8000];
        Vp9Probs after;
        u32 csize = vp9_write_compressed(&f, &contexts[f.context_idx], &probs, &after, compressed, sizeof compressed);
        u32 usize = vp9_write_uncompressed(&f, csize, frame, 256);
        u32 ntiles = rd32(pic + 0x30);
        if (!j->blob[0x102] || j->blob_size[0x102] < ntiles) { printf("%4d: no bitstream\n", n); break; }
        memcpy(frame + usize, compressed, csize);
        memcpy(frame + usize + csize, j->blob[0x102], ntiles);
        if (f.refresh_context) contexts[f.context_idx] = after;
        vpx_codec_err_t r = vpx_codec_decode(&vpx, frame, usize + csize + ntiles, NULL, 0);
        const char *detail = vpx_codec_error_detail(&vpx);
        if (r) bad++; else ok++;
        if (r || n < 4 || save[n])
            printf("%4d: %s%s %ux%u q %u tx %u filt %u, header %u + %u, tiles %u -> %s %s\n", n, f.key ? "key" : f.intra_only ? "intra" : "inter",
                   show ? "" : " (hidden)", f.width, f.height, f.base_q, f.tx_mode, f.interp_filter, usize, csize, ntiles,
                   r ? vpx_codec_error(&vpx) : "ok", r && detail ? detail : "");
        vpx_codec_iter_t it = NULL;
        vpx_image_t *img;
        while ((img = vpx_codec_get_frame(&vpx, &it)))
            if (save[n]) { snprintf(path, sizeof path, "%s/frame_%05d.bmp", argv[2], n); save_bmp(path, img); }
        for (int m = 0; m < 0x800; m++) { free(j->blob[m]); j->blob[m] = NULL; }
        if (!has_next) break;
    }
    printf("%d decoded, %d failed (%s probabilities)\n", ok, bad, have_probs ? "recorded" : "default");
    return 0;
}
