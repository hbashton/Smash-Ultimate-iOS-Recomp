/* Round-trip check of runtime/gpu_vp9.c against libvpx: encodes a synthetic clip, parses every
 * frame's headers with an independent reader written from the specification, and requires the
 * writer to reproduce both headers byte for byte from the parsed fields and probabilities.
 *   vp9rt [frames]   (build: see vp9test.c) */
#define _CRT_SECURE_NO_WARNINGS
#include <math.h>
#include <process.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vpx/vpx_encoder.h>
#include <vpx/vp8cx.h>

#include "../../runtime/gpu_vp9.c"

void *const vpx_crt_imports[] = {(void *)_beginthreadex, (void *)round};
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32;

/* ---- plain bits ---- */
typedef struct Rb { const u8 *p; u32 pos, size; } Rb;
static u32 rb(Rb *r, int n) { u32 v = 0; while (n--) { v = v << 1 | (r->pos < r->size * 8 ? (r->p[r->pos >> 3] >> (7 - (r->pos & 7))) & 1 : 0); r->pos++; } return v; }
static int rb_signed(Rb *r, int n) { int v = (int)rb(r, n); return rb(r, 1) ? -v : v; }

/* ---- the boolean decoder (9.2) ---- */
typedef struct Rd { Rb b; u32 value, range; } Rd;
static void rd_init(Rd *r, const u8 *p, u32 size) { r->b = (Rb){p, 0, size}; r->value = rb(&r->b, 8); r->range = 255; }
static int rd_bool(Rd *r, int prob) {
    u32 split = 1 + (((r->range - 1) * (u32)prob) >> 8);
    int bit;
    if (r->value < split) { r->range = split; bit = 0; } else { r->range -= split; r->value -= split; bit = 1; }
    while (r->range < 128) { r->value = (r->value << 1) | rb(&r->b, 1); r->range <<= 1; }
    return bit;
}
static u32 rd_lit(Rd *r, int n) { u32 v = 0; while (n--) v = v << 1 | (u32)rd_bool(r, 128); return v; }
static int rd_subexp(Rd *r) {
    if (!rd_lit(r, 1)) return (int)rd_lit(r, 4);
    if (!rd_lit(r, 1)) return (int)rd_lit(r, 4) + 16;
    if (!rd_lit(r, 1)) return (int)rd_lit(r, 5) + 32;
    int v = (int)rd_lit(r, 7);
    if (v < 65) return v + 64;
    return (v << 1) - 1 + (int)rd_lit(r, 1);
}
static void rd_diff(Rd *r, u8 *p, int n) { for (int i = 0; i < n; i++) if (rd_bool(r, 252)) p[i] = (u8)vp9_inv_remap_prob(rd_subexp(r), p[i]); }
static void rd_mv(Rd *r, u8 *p, int n) { for (int i = 0; i < n; i++) if (rd_bool(r, 252)) p[i] = (u8)(rd_lit(r, 7) << 1 | 1); }

/* ---- the uncompressed header (6.2) ---- */
typedef struct Parsed { Vp9Frame f; u32 header_bytes, compressed_size; int intra; } Parsed;

static int parse_uncompressed(const u8 *p, u32 size, Parsed *o, u16 ref_w[8], u16 ref_h[8]) {
    Rb r = {p, 0, size};
    Vp9Frame *f = &o->f;
    memset(o, 0, sizeof *o);
    if (rb(&r, 2) != 2) return 0;
    f->profile = (u8)rb(&r, 1);
    f->profile |= (u8)(rb(&r, 1) << 1);
    if (f->profile == 3) rb(&r, 1);
    f->bit_depth = 8;
    f->show_existing = (u8)rb(&r, 1);
    if (f->show_existing) { f->existing_idx = (u8)rb(&r, 3); o->header_bytes = (r.pos + 7) >> 3; return 1; }
    f->key = !rb(&r, 1);
    f->show = (u8)rb(&r, 1);
    f->error_resilient = (u8)rb(&r, 1);
    if (f->key) {
        if (rb(&r, 24) != 0x498342) return 0;
        f->color_space = (u8)rb(&r, 3);
        if (f->color_space != 7) f->color_range = (u8)rb(&r, 1);
        f->width = (u16)(rb(&r, 16) + 1); f->height = (u16)(rb(&r, 16) + 1);
        if (rb(&r, 1)) { f->render_width = (u16)(rb(&r, 16) + 1); f->render_height = (u16)(rb(&r, 16) + 1); }
        f->refresh_flags = 0xFF;
    } else {
        if (!f->show) f->intra_only = (u8)rb(&r, 1);
        if (!f->error_resilient) f->reset_context = (u8)rb(&r, 2);
        if (f->intra_only) {
            if (rb(&r, 24) != 0x498342) return 0;
            f->refresh_flags = (u8)rb(&r, 8);
            f->width = (u16)(rb(&r, 16) + 1); f->height = (u16)(rb(&r, 16) + 1);
            if (rb(&r, 1)) { f->render_width = (u16)(rb(&r, 16) + 1); f->render_height = (u16)(rb(&r, 16) + 1); }
        } else {
            f->refresh_flags = (u8)rb(&r, 8);
            for (int i = 0; i < 3; i++) { f->ref_idx[i] = (u8)rb(&r, 3); f->sign_bias[i] = (u8)rb(&r, 1); }
            for (int i = 0; i < 3; i++)
                if (rb(&r, 1)) { f->found_ref = (u8)(i + 1); f->width = ref_w[f->ref_idx[i]]; f->height = ref_h[f->ref_idx[i]]; break; }
            if (!f->found_ref) { f->width = (u16)(rb(&r, 16) + 1); f->height = (u16)(rb(&r, 16) + 1); }
            if (rb(&r, 1)) { f->render_width = (u16)(rb(&r, 16) + 1); f->render_height = (u16)(rb(&r, 16) + 1); }
            f->allow_hp = (u8)rb(&r, 1);
            if (rb(&r, 1)) f->interp_filter = VP9_SWITCHABLE;
            else { static const u8 type_of[4] = {1, 0, 2, 3}; f->interp_filter = type_of[rb(&r, 2)]; }
        }
    }
    o->intra = f->key || f->intra_only;
    if (!f->error_resilient) { f->refresh_context = (u8)rb(&r, 1); f->parallel = (u8)rb(&r, 1); } else f->parallel = 1;
    f->context_idx = (u8)rb(&r, 2);
    f->lf_level = (u8)rb(&r, 6); f->lf_sharpness = (u8)rb(&r, 3);
    f->lf_delta_enabled = (u8)rb(&r, 1);
    if (f->lf_delta_enabled) {
        f->lf_delta_update = (u8)rb(&r, 1);
        if (f->lf_delta_update) {
            for (int i = 0; i < 4; i++) if ((f->lf_ref_update[i] = (u8)rb(&r, 1))) f->lf_ref[i] = (int8_t)rb_signed(&r, 6);
            for (int i = 0; i < 2; i++) if ((f->lf_mode_update[i] = (u8)rb(&r, 1))) f->lf_mode[i] = (int8_t)rb_signed(&r, 6);
        }
    }
    f->base_q = (u8)rb(&r, 8);
    int8_t *dq[3] = {&f->dq_y_dc, &f->dq_uv_dc, &f->dq_uv_ac};
    for (int i = 0; i < 3; i++) if (rb(&r, 1)) *dq[i] = (int8_t)rb_signed(&r, 4);
    f->seg_enabled = (u8)rb(&r, 1);
    if (f->seg_enabled) {
        f->seg_update_map = (u8)rb(&r, 1);
        if (f->seg_update_map) {
            for (int i = 0; i < 7; i++) f->seg_tree[i] = rb(&r, 1) ? (u8)rb(&r, 8) : 255;
            f->seg_temporal = (u8)rb(&r, 1);
            for (int i = 0; i < 3; i++) f->seg_pred[i] = f->seg_temporal && rb(&r, 1) ? (u8)rb(&r, 8) : 255;
        }
        f->seg_update_data = (u8)rb(&r, 1);
        if (f->seg_update_data) {
            static const int bits[4] = {8, 6, 2, 0}, sgn[4] = {1, 1, 0, 0};
            f->seg_abs = (u8)rb(&r, 1);
            for (int i = 0; i < 8; i++)
                for (int j = 0; j < 4; j++)
                    if ((f->seg_on[i][j] = (u8)rb(&r, 1))) {
                        int v = (int)rb(&r, bits[j]);
                        if (sgn[j] && rb(&r, 1)) v = -v;
                        f->seg_data[i][j] = (int16_t)v;
                    }
        }
    }
    int lo, hi;
    tile_log2_bounds(f, &lo, &hi);
    f->tile_cols_log2 = (u8)lo;
    while (f->tile_cols_log2 < hi && rb(&r, 1)) f->tile_cols_log2++;
    f->tile_rows_log2 = (u8)rb(&r, 1);
    if (f->tile_rows_log2) f->tile_rows_log2 = (u8)(f->tile_rows_log2 + rb(&r, 1));
    o->compressed_size = rb(&r, 16);
    o->header_bytes = (r.pos + 7) >> 3;
    return 1;
}

/* ---- the compressed header (6.3): fills in the frame's tx and reference modes, updates p ---- */
static void parse_compressed(const u8 *data, u32 size, Vp9Frame *f, Vp9Probs *p) {
    Rd r;
    rd_init(&r, data, size);
    rd_bool(&r, 128);   /* marker */
    int lossless = f->base_q == 0 && !f->dq_y_dc && !f->dq_uv_dc && !f->dq_uv_ac;
    if (lossless) f->tx_mode = VP9_ONLY_4X4;
    else { f->tx_mode = (u8)rd_lit(&r, 2); if (f->tx_mode == VP9_ALLOW_32X32) f->tx_mode = (u8)(f->tx_mode + rd_lit(&r, 1)); }
    if (f->tx_mode == VP9_TX_MODE_SELECT) { rd_diff(&r, &p->tx8[0][0], 2); rd_diff(&r, &p->tx16[0][0], 4); rd_diff(&r, &p->tx32[0][0], 6); }
    static const u8 biggest[5] = {0, 1, 2, 3, 3};
    for (int t = 0; t <= biggest[f->tx_mode]; t++)
        if (rd_lit(&r, 1))
            for (int i = 0; i < 2; i++) for (int j = 0; j < 2; j++) for (int k = 0; k < 6; k++)
                for (int l = 0; l < (k == 0 ? 3 : 6); l++) rd_diff(&r, p->coef[t][i][j][k][l], 3);
    rd_diff(&r, p->skip, 3);
    if (f->key || f->intra_only) return;
    rd_diff(&r, &p->inter_mode[0][0], 21);
    if (f->interp_filter == VP9_SWITCHABLE) rd_diff(&r, &p->interp_filter[0][0], 8);
    rd_diff(&r, p->is_inter, 4);
    f->reference_mode = VP9_SINGLE_REFERENCE;
    if (f->sign_bias[1] != f->sign_bias[0] || f->sign_bias[2] != f->sign_bias[0])
        if (rd_lit(&r, 1)) f->reference_mode = rd_lit(&r, 1) ? VP9_REFERENCE_MODE_SELECT : VP9_COMPOUND_REFERENCE;
    if (f->reference_mode == VP9_REFERENCE_MODE_SELECT) rd_diff(&r, p->comp_mode, 5);
    if (f->reference_mode != VP9_COMPOUND_REFERENCE) rd_diff(&r, &p->single_ref[0][0], 10);
    if (f->reference_mode != VP9_SINGLE_REFERENCE) rd_diff(&r, p->comp_ref, 5);
    rd_diff(&r, &p->y_mode[0][0], 36);
    rd_diff(&r, &p->partition[0][0], 48);
    rd_mv(&r, p->mv_joint, 3);
    for (int i = 0; i < 2; i++) { rd_mv(&r, &p->mv_sign[i], 1); rd_mv(&r, p->mv_class[i], 10); rd_mv(&r, &p->mv_class0_bit[i], 1); rd_mv(&r, p->mv_bits[i], 10); }
    for (int i = 0; i < 2; i++) { rd_mv(&r, &p->mv_class0_fr[i][0][0], 6); rd_mv(&r, p->mv_fr[i], 3); }
    if (f->allow_hp) for (int i = 0; i < 2; i++) { rd_mv(&r, &p->mv_class0_hp[i], 1); rd_mv(&r, &p->mv_hp[i], 1); }
}

/* ---- what the runtime does with an NVDEC job (gpu_host1x.c: vp9_decode), simulated ---- */
#include <vpx/vpx_decoder.h>
#include <vpx/vp8dx.h>
static vpx_codec_ctx_t ref_dec, my_dec;
static uint64_t ref_sums[4096], my_sums[4096];
static int nref, nmine;

static uint64_t image_sum(const vpx_image_t *img) {
    uint64_t h = 1469598103934665603ull;
    for (int p = 0; p < 3; p++) {
        u32 w = p ? (img->d_w + 1) / 2 : img->d_w, hh = p ? (img->d_h + 1) / 2 : img->d_h;
        for (u32 y = 0; y < hh; y++) for (u32 x = 0; x < w; x++) h = (h ^ img->planes[p][y * img->stride[p] + x]) * 1099511628211ull;
    }
    return h;
}

/* f: the frame as parsed (the engine's picture setup has the same facts); to: the probabilities
 * it decodes with (the engine's probability buffer); surfaces stand for the engine's buffers. */
static void simulate(const Vp9Frame *f, const Vp9Probs *to, const u8 *tiles, u32 ntiles, const u32 refs_sid[3], u32 cur_sid) {
    static Vp9Slots slots;
    static Vp9Probs contexts[4];
    static int8_t lf_ref[4] = {1, 0, -1, -1}, lf_mode[2];
    Vp9Frame g = *f;
    if (f->key || f->intra_only || f->error_resilient) { static const int8_t d[4] = {1, 0, -1, -1}; memcpy(lf_ref, d, 4); memset(lf_mode, 0, 2); }
    if (f->lf_delta_enabled && f->lf_delta_update) {
        for (int i = 0; i < 4; i++) if (f->lf_ref_update[i]) lf_ref[i] = f->lf_ref[i];
        for (int i = 0; i < 2; i++) if (f->lf_mode_update[i]) lf_mode[i] = f->lf_mode[i];
    }
    g.lf_delta_update = g.lf_delta_enabled;   /* the runtime writes all deltas, every frame */
    for (int i = 0; i < 4; i++) { g.lf_ref_update[i] = 1; g.lf_ref[i] = lf_ref[i]; }
    for (int i = 0; i < 2; i++) { g.lf_mode_update[i] = 1; g.lf_mode[i] = lf_mode[i]; }
    g.found_ref = 0;
    uint64_t refs[3] = {refs_sid[0], refs_sid[1], refs_sid[2]};
    int slot = vp9_assign_slots(&slots, &g, refs, cur_sid);
    if (g.key || g.intra_only || g.error_resilient) {
        Vp9Probs def; vp9_default_probs(&def);
        for (int c = 0; c < 4; c++) contexts[c] = def;
        g.reset_context = 3; g.context_idx = 0;
    } else {
        int best = 0;
        for (int c = 1; c < 4; c++) if (vp9_context_score(&contexts[c], to) > vp9_context_score(&contexts[best], to)) best = c;
        g.context_idx = (u8)best;
        if (vp9_context_score(&contexts[best], to) < 0) printf("frame %u: no context can reach its motion vector probabilities\n", cur_sid);
    }
    if (getenv("VP9RT_TRACE")) printf("frame %u: %s%s ctx %u slot %d refs %u %u %u -> slots %u %u %u\n", cur_sid, g.key ? "key" : g.intra_only ? "intra" : "inter", g.show ? "" : " hidden", g.context_idx, slot, refs_sid[0], refs_sid[1], refs_sid[2], g.ref_idx[0], g.ref_idx[1], g.ref_idx[2]);
    g.refresh_context = !g.error_resilient;
    g.parallel = 1;
    static u8 frame[1 << 22];
    u8 ch[0x8000];
    Vp9Probs after;
    u32 csize = vp9_write_compressed(&g, &contexts[g.context_idx], to, &after, ch, sizeof ch);
    u32 usize = vp9_write_uncompressed(&g, csize, frame, 256);
    memcpy(frame + usize, ch, csize);
    memcpy(frame + usize + csize, tiles, ntiles);
    if (g.refresh_context) contexts[g.context_idx] = after;
    if (vpx_codec_decode(&my_dec, frame, usize + csize + ntiles, NULL, 0)) { printf("composed frame rejected: %s\n", vpx_codec_error_detail(&my_dec)); return; }
    vpx_codec_iter_t it = NULL;
    vpx_image_t *img;
    while ((img = vpx_codec_get_frame(&my_dec, &it))) if (nmine < 4096) my_sums[nmine++] = image_sum(img);
}

static void make_picture(vpx_image_t *img, int n, int W, int H) {   /* a moving, textured picture */
    for (int y = 0; y < H; y++) for (int x = 0; x < W; x++)
        img->planes[0][y * img->stride[0] + x] = (u8)(128 + 60 * sin((x + 3 * n) * 0.07) * cos((y - 2 * n) * 0.05) + ((x * 7 + y * 13 + n) & 15));
    for (int y = 0; y < H / 2; y++) for (int x = 0; x < W / 2; x++) {
        img->planes[1][y * img->stride[1] + x] = (u8)(128 + 40 * sin((x + n) * 0.1));
        img->planes[2][y * img->stride[2] + x] = (u8)(128 + 40 * cos((y + n) * 0.08));
    }
}

int main(int argc, char **argv) {
    int frames = argc > 1 ? atoi(argv[1]) : 90;
    vpx_codec_dec_cfg_t dcfg = {1, 0, 0};
    vpx_codec_dec_init(&ref_dec, vpx_codec_vp9_dx(), &dcfg, 0);
    vpx_codec_dec_init(&my_dec, vpx_codec_vp9_dx(), &dcfg, 0);
    u32 slot_sid[8] = {0}, next_sid = 0;
    const int W = 352, H = 288;
    vpx_codec_enc_cfg_t cfg;
    vpx_codec_enc_config_default(vpx_codec_vp9_cx(), &cfg, 0);
    cfg.g_w = W; cfg.g_h = H; cfg.g_timebase.num = 1; cfg.g_timebase.den = 30;
    cfg.rc_target_bitrate = 600; cfg.g_lag_in_frames = 16; cfg.kf_max_dist = 40;
    vpx_codec_ctx_t enc;
    vpx_image_t img;
    vpx_img_alloc(&img, VPX_IMG_FMT_I420, W, H, 16);
    /* two passes (argv[2] "2"): only then does libvpx point the altref the other way, which is
     * what makes compound prediction possible - as in the game's movies */
    static u8 stats[1 << 20];
    size_t nstats = 0;
    if (argc > 2 && atoi(argv[2]) == 2) {
        cfg.g_pass = VPX_RC_FIRST_PASS;
        if (vpx_codec_enc_init(&enc, vpx_codec_vp9_cx(), &cfg, 0)) { printf("first pass init failed: %s (%s)\n", vpx_codec_error(&enc), vpx_codec_error_detail(&enc) ? vpx_codec_error_detail(&enc) : ""); return 1; }
        for (int n = 0; n <= frames; n++) {
            if (n < frames) make_picture(&img, n, W, H);
            vpx_codec_encode(&enc, n < frames ? &img : NULL, n, 1, 0, VPX_DL_GOOD_QUALITY);
            vpx_codec_iter_t it = NULL;
            const vpx_codec_cx_pkt_t *pkt;
            while ((pkt = vpx_codec_get_cx_data(&enc, &it)))
                if (pkt->kind == VPX_CODEC_STATS_PKT && nstats + pkt->data.twopass_stats.sz <= sizeof stats) {
                    memcpy(stats + nstats, pkt->data.twopass_stats.buf, pkt->data.twopass_stats.sz);
                    nstats += pkt->data.twopass_stats.sz;
                }
        }
        vpx_codec_destroy(&enc);
        cfg.g_pass = VPX_RC_LAST_PASS;
        cfg.rc_twopass_stats_in.buf = stats;
        cfg.rc_twopass_stats_in.sz = nstats;
    }
    if (vpx_codec_enc_init(&enc, vpx_codec_vp9_cx(), &cfg, 0)) { printf("encoder init failed\n"); return 1; }
    vpx_codec_control(&enc, VP9E_SET_FRAME_PARALLEL_DECODING, 1);
    vpx_codec_control(&enc, VP8E_SET_ENABLEAUTOALTREF, 1);
    vpx_codec_control(&enc, VP9E_SET_TILE_COLUMNS, 1);
    vpx_codec_control(&enc, VP8E_SET_CPUUSED, 4);
    Vp9Probs ctx[4], def;
    vp9_default_probs(&def);
    for (int c = 0; c < 4; c++) ctx[c] = def;
    u16 ref_w[8] = {0}, ref_h[8] = {0};
    int checked = 0, same_u = 0, same_c = 0, hidden = 0, existing = 0, select_tx = 0, compound = 0;
    for (int n = 0; n <= frames; n++) {
        if (n < frames) make_picture(&img, n, W, H);
        if (vpx_codec_encode(&enc, n < frames ? &img : NULL, n, 1, 0, VPX_DL_GOOD_QUALITY)) { printf("encode failed\n"); return 1; }
        vpx_codec_iter_t it = NULL;
        const vpx_codec_cx_pkt_t *pkt;
        while ((pkt = vpx_codec_get_cx_data(&enc, &it))) {
            if (pkt->kind != VPX_CODEC_CX_FRAME_PKT) continue;
            const u8 *data = pkt->data.frame.buf;
            u32 size = (u32)pkt->data.frame.sz, sizes[8] = {size}, count = 1;
            if (!vpx_codec_decode(&ref_dec, data, size, NULL, 0)) {   /* the reference: libvpx on the real stream */
                vpx_codec_iter_t dit = NULL;
                vpx_image_t *dimg;
                while ((dimg = vpx_codec_get_frame(&ref_dec, &dit))) if (nref < 4096) ref_sums[nref++] = image_sum(dimg);
            }
            u8 last = data[size - 1];
            if ((last & 0xE0) == 0xC0) {   /* a superframe: its index at the end */
                u32 k = (last & 7) + 1, mag = ((last >> 3) & 3) + 1, index = 2 + mag * k;
                if (size >= index && data[size - index] == last) {
                    count = k;
                    for (u32 i = 0; i < k; i++) { sizes[i] = 0; for (u32 b = 0; b < mag; b++) sizes[i] |= (u32)data[size - index + 1 + i * mag + b] << (8 * b); }
                }
            }
            for (u32 i = 0, off = 0; i < count; off += sizes[i++]) {
                Parsed ps;
                if (!parse_uncompressed(data + off, sizes[i], &ps, ref_w, ref_h)) { printf("unparsable frame\n"); return 1; }
                Vp9Frame *f = &ps.f;
                if (f->show_existing) { existing++; continue; }
                hidden += !f->show;
                /* the context it starts from, as the decoder resets them */
                if (ps.intra || f->error_resilient) {
                    if (f->key || f->error_resilient || f->reset_context == 3) for (int c = 0; c < 4; c++) ctx[c] = def;
                    else if (f->reset_context == 2) ctx[f->context_idx] = def;
                    f->context_idx = 0;
                }
                Vp9Probs from = ctx[f->context_idx], to = from;
                parse_compressed(data + off + ps.header_bytes, ps.compressed_size, f, &to);
                select_tx += f->tx_mode == VP9_TX_MODE_SELECT;
                compound += f->reference_mode != VP9_SINGLE_REFERENCE;
                /* write both back */
                u8 uh[256], ch[0x8000];
                Vp9Probs after;
                u32 csize = vp9_write_compressed(f, &from, &to, &after, ch, sizeof ch);
                u32 usize = vp9_write_uncompressed(f, csize, uh, sizeof uh);
                int u_ok = usize == ps.header_bytes && !memcmp(uh, data + off, usize);
                int c_ok = csize == ps.compressed_size && !memcmp(ch, data + off + ps.header_bytes, csize) && !memcmp(&after, &to, sizeof to);
                same_u += u_ok; same_c += c_ok; checked++;
                if ((!u_ok || !c_ok) && checked - same_u + checked - same_c < 6)
                    printf("frame %d.%u (%s%s): uncompressed %s (%u vs %u bytes), compressed %s (%u vs %u bytes)\n", n, i, f->key ? "key" : "inter",
                           f->show ? "" : ", hidden", u_ok ? "same" : "DIFFERS", usize, ps.header_bytes, c_ok ? "same" : "DIFFERS", csize, ps.compressed_size);
                if (f->refresh_context) ctx[f->context_idx] = to;
                for (int s = 0; s < 8; s++) if (f->refresh_flags >> s & 1) { ref_w[s] = f->width; ref_h[s] = f->height; }
                /* the engine's view: surfaces for the references and the frame, the tile data */
                u32 refs_sid[3], cur_sid = ++next_sid;
                for (int r = 0; r < 3; r++) refs_sid[r] = slot_sid[f->ref_idx[r]];
                u32 at = off + ps.header_bytes + ps.compressed_size;
                simulate(f, &to, data + at, off + sizes[i] - at, refs_sid, cur_sid);
                for (int s = 0; s < 8; s++) if (f->refresh_flags >> s & 1) slot_sid[s] = cur_sid;
            }
        }
    }
    printf("%d frames (%d hidden, %d shown existing, %d with tx select, %d compound): uncompressed headers identical %d, compressed %d\n",
           checked, hidden, existing, select_tx, compound, same_u, same_c);
    int match = 0;
    for (int k = 0; k < nref && k < nmine; k++) { match += ref_sums[k] == my_sums[k]; if (ref_sums[k] != my_sums[k] && (k == 0 || ref_sums[k - 1] == my_sums[k - 1])) printf("shown frame %d differs\n", k); }
    printf("as the runtime rebuilds them: %d shown frames, %d of the %d reference frames identical\n", nmine, match, nref);
    return same_u == checked && same_c == checked && match == nref && nmine == nref ? 0 : 1;
}
