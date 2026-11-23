// bpx-milkdrop: a small projectM (MilkDrop) front end for Batoplex.
//
//   bpx-milkdrop --window [options] PRESET...      full screen SDL2 window (X11 on Batocera)
//   bpx-milkdrop --headless N OUT_DIR [options] PRESET...   (test build only: -DBPX_HEADLESS)
//                                                  offscreen EGL 640x480, N frames over the
//                                                  presets in order, PPM dumps and flash stats
//
// Options: --secs S (time for each preset, 30), --ctl-fd N (command pipe), --dim A (black layer
// alpha, 0.12), --flash-rise R (largest rise of mean luma per frame, 0.03), --tex DIR.
// A PRESET argument is a .milk file or a folder (searched recursively, 5000 files at most).
//
// PCM: s16le stereo 44100 on stdin. Headless: a built-in test tone when stdin is a tty or
// gives no data in 0.5 s.
//
// The app owns the pad. This program opens no pad. Commands come on --ctl-fd, one per line:
//   next | prev | quit | overlay PATH W H X Y SECS   (PATH: premultiplied RGBA, W*H*4 bytes,
//                                                     top row first)
// EOF on the command pipe also ends the program. Status lines go to stdout:
//   ready <GL renderer> | key <action>
// Keyboard keys (the window has the X focus) come back as "key <action>" with the app's names.
#define GL_GLEXT_PROTOTYPES 1
#include <projectM-4/projectM.h>

#ifdef BPX_HEADLESS
#include <EGL/egl.h>
#include <EGL/eglext.h>
#endif
#include <GL/gl.h>
#include <GL/glext.h>
#include <SDL2/SDL.h>
#ifdef BPX_HEADLESS
#include <gbm.h>
#endif

#include <dirent.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <mutex>
#include <random>
#include <string>
#include <thread>
#include <vector>

static const int W = 640, H = 480, RATE = 44100;
static const size_t MAX_PRESETS = 5000;
static std::atomic<bool> g_stop{false};

using Clock = std::chrono::steady_clock;
static double since(Clock::time_point t) {
    return std::chrono::duration<double>(Clock::now() - t).count();
}

struct Opts {
    double secs = 30, dim = 0.12, rise = 0.03;
    int ctl = -1;
    std::vector<std::string> presets, tex;
};

// ------------------------------------------------------------------ PCM input
struct Pcm {
    std::mutex m;
    std::vector<int16_t> buf;  // interleaved stereo, newest at the end
    std::atomic<bool> got{false}, eof{false};
    void push(const int16_t* s, size_t n) {
        std::lock_guard<std::mutex> l(m);
        buf.insert(buf.end(), s, s + n);
        size_t cap = RATE * 2;  // keep 1 s at most
        if (buf.size() > cap) buf.erase(buf.begin(), buf.end() - cap);
        got = true;
    }
    size_t take(std::vector<int16_t>& out, size_t max) {
        std::lock_guard<std::mutex> l(m);
        size_t n = std::min(max, buf.size() / 2);
        out.assign(buf.begin(), buf.begin() + n * 2);
        buf.erase(buf.begin(), buf.begin() + n * 2);
        return n;
    }
};

static void reader(Pcm* p) {
    std::vector<char> b(8192);
    size_t carry = 0;
    while (!g_stop) {
        struct pollfd pf = {0, POLLIN, 0};
        if (poll(&pf, 1, 100) <= 0) continue;
        ssize_t r = read(0, b.data() + carry, b.size() - carry);
        if (r <= 0) { p->eof = true; return; }
        size_t have = carry + r, whole = have & ~size_t(3);
        p->push(reinterpret_cast<int16_t*>(b.data()), whole / 2);
        carry = have - whole;
        memmove(b.data(), b.data() + whole, carry);
    }
}

// A test tone with a beat: kick at 2 Hz, a slow chord, a noise hat on the off beat.
[[maybe_unused]] static void tone(std::vector<int16_t>& out, size_t frames, uint64_t& t) {
    static std::mt19937 rng(1);
    std::uniform_real_distribution<float> nz(-1.f, 1.f);
    out.resize(frames * 2);
    for (size_t i = 0; i < frames; i++, t++) {
        double s = double(t) / RATE, ph = fmod(s, 0.5);
        double kick = exp(-ph * 18) * sin(2 * M_PI * (50 + 80 * exp(-ph * 30)) * ph);
        double hp = fmod(s + 0.25, 0.5), hat = exp(-hp * 60) * nz(rng) * 0.25;
        double ch = 0.15 * (sin(2 * M_PI * 220 * s) + sin(2 * M_PI * 277.2 * s) + sin(2 * M_PI * 329.6 * s));
        double l = 0.6 * kick + hat + ch * (0.6 + 0.4 * sin(s * 0.7)), r = 0.6 * kick + hat * 0.7 + ch;
        out[i * 2] = int16_t(std::max(-1.0, std::min(1.0, l)) * 30000);
        out[i * 2 + 1] = int16_t(std::max(-1.0, std::min(1.0, r)) * 30000);
    }
}

// ------------------------------------------------------------------ presets
static void collect(const std::string& p, std::vector<std::string>& out, int depth = 0) {
    struct stat st;
    if (out.size() >= MAX_PRESETS || depth > 8 || stat(p.c_str(), &st) != 0) return;
    if (!S_ISDIR(st.st_mode)) {
        if (p.size() > 5 && p.compare(p.size() - 5, 5, ".milk") == 0) out.push_back(p);
        return;
    }
    DIR* d = opendir(p.c_str());
    if (!d) return;
    std::vector<std::string> v;
    while (dirent* e = readdir(d)) {
        std::string n = e->d_name;
        if (!n.empty() && n[0] != '.') v.push_back(p + "/" + n);
    }
    closedir(d);
    std::sort(v.begin(), v.end());
    for (auto& s : v) collect(s, out, depth + 1);
}

// A shuffle bag with a history: next() after prev() goes forward again in the history.
struct Playlist {
    std::vector<std::string> all;
    std::vector<size_t> bag, hist;
    size_t pos = 0;
    std::mt19937 rng{std::random_device{}()};
    size_t draw() {
        if (bag.empty()) {
            for (size_t i = 0; i < all.size(); i++) bag.push_back(i);
            std::shuffle(bag.begin(), bag.end(), rng);
            if (bag.size() > 1 && !hist.empty() && bag.back() == hist.back()) std::swap(bag.front(), bag.back());
        }
        size_t i = bag.back();
        bag.pop_back();
        return i;
    }
    const std::string* next() {
        if (all.empty()) return nullptr;
        if (pos + 1 < hist.size()) return &all[hist[++pos]];
        hist.push_back(draw());
        if (hist.size() > 200) hist.erase(hist.begin());
        pos = hist.size() - 1;
        return &all[hist[pos]];
    }
    const std::string* prev() {
        if (all.empty() || hist.empty()) return nullptr;
        if (pos > 0) pos--;
        return &all[hist[pos]];
    }
    void drop_current() {  // a preset that failed to load: forget it and the history
        if (hist.empty()) return;
        if (all.size() <= 1) { all.clear(); hist.clear(); bag.clear(); pos = 0; return; }  // none left
        all.erase(all.begin() + hist[pos]);
        hist.clear(); bag.clear(); pos = 0;
    }
};

static std::atomic<bool> g_preset_failed{false};
static void on_preset_failed(const char* file, const char* msg, void*) {
    fprintf(stderr, "preset failed: %s: %s\n", file ? file : "?", msg ? msg : "?");
    g_preset_failed = true;
}

static projectm_handle make_pm(const std::vector<std::string>& texdirs) {
    projectm_handle pm = projectm_create();
    if (!pm) { fprintf(stderr, "projectm_create failed (no GL context?)\n"); exit(2); }
    projectm_set_window_size(pm, W, H);
    projectm_set_mesh_size(pm, 48, 36);
    projectm_set_fps(pm, 60);
    projectm_set_aspect_correction(pm, true);
    projectm_set_hard_cut_enabled(pm, false);  // no beat-driven hard cuts (CRT/photosensitivity)
    projectm_set_preset_locked(pm, true);       // this program changes the presets
    projectm_set_beat_sensitivity(pm, 0.8f);
    projectm_set_soft_cut_duration(pm, 4.0);
    projectm_set_preset_switch_failed_event_callback(pm, on_preset_failed, nullptr);
    std::vector<const char*> td;
    for (auto& s : texdirs) td.push_back(s.c_str());
    if (!td.empty()) projectm_set_texture_search_paths(pm, td.data(), td.size());
    return pm;
}

// ------------------------------------------------------------------ flash limiter
// A black layer over each frame: alpha `dim` at least, more when the mean luma of the frame
// rises faster than `rise` per frame. This also covers user presets that nobody screened.
struct Limiter {
    double dim, rise, shown = 0;
    double alpha(double luma) {
        double a = dim;
        if (luma > 0.001) a = std::max(a, 1.0 - (shown + rise) / luma);   // < 1: shown + rise > 0
        shown = luma * (1.0 - a);
        return a;
    }
};

// ------------------------------------------------------------------ headless
// Only in a test build (-DBPX_HEADLESS): the release binary needs no EGL and no GBM.
#ifdef BPX_HEADLESS
static bool egl_headless(EGLDisplay& dpy, EGLSurface& surf, EGLContext& ctx) {
    auto getdpy = (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    if (!getdpy) return false;
    EGLint pbattr[] = {EGL_WIDTH, W, EGL_HEIGHT, H, EGL_NONE};
    EGLint cattr[] = {EGL_CONTEXT_MAJOR_VERSION, 3, EGL_CONTEXT_MINOR_VERSION, 3,
                      EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT, EGL_NONE};
    // 1) Mesa surfaceless platform with a pbuffer (gives projectM a default framebuffer).
    dpy = getdpy(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, nullptr);
    if (dpy != EGL_NO_DISPLAY && eglInitialize(dpy, nullptr, nullptr)) {
        EGLint a[] = {EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT,
                      EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_NONE};
        EGLConfig cfg; EGLint n = 0;
        if (eglBindAPI(EGL_OPENGL_API) && eglChooseConfig(dpy, a, &cfg, 1, &n) && n == 1) {
            surf = eglCreatePbufferSurface(dpy, cfg, pbattr);
            ctx = eglCreateContext(dpy, cfg, EGL_NO_CONTEXT, cattr);
            if (surf != EGL_NO_SURFACE && ctx != EGL_NO_CONTEXT && eglMakeCurrent(dpy, surf, surf, ctx)) {
                fprintf(stderr, "EGL: surfaceless + pbuffer\n");
                return true;
            }
        }
    }
    // 2) GBM on the render node with an offscreen gbm_surface (never scanned out).
    int fd = open("/dev/dri/renderD128", O_RDWR | O_CLOEXEC);
    if (fd < 0) return false;
    gbm_device* gbm = gbm_create_device(fd);
    dpy = getdpy(EGL_PLATFORM_GBM_KHR, gbm, nullptr);
    if (dpy == EGL_NO_DISPLAY || !eglInitialize(dpy, nullptr, nullptr)) return false;
    EGLint a[] = {EGL_SURFACE_TYPE, EGL_WINDOW_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT,
                  EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_NONE};
    EGLConfig cfgs[64]; EGLint n = 0;
    eglBindAPI(EGL_OPENGL_API);
    eglChooseConfig(dpy, a, cfgs, 64, &n);
    for (int i = 0; i < n; i++) {
        EGLint fmt; eglGetConfigAttrib(dpy, cfgs[i], EGL_NATIVE_VISUAL_ID, &fmt);
        if (fmt != GBM_FORMAT_XRGB8888 && fmt != GBM_FORMAT_ARGB8888) continue;
        gbm_surface* gs = gbm_surface_create(gbm, W, H, fmt, GBM_BO_USE_RENDERING);
        surf = eglCreateWindowSurface(dpy, cfgs[i], (EGLNativeWindowType)gs, nullptr);
        ctx = eglCreateContext(dpy, cfgs[i], EGL_NO_CONTEXT, cattr);
        if (surf != EGL_NO_SURFACE && ctx != EGL_NO_CONTEXT && eglMakeCurrent(dpy, surf, surf, ctx)) {
            fprintf(stderr, "EGL: GBM render node (no swaps)\n");
            return true;
        }
    }
    return false;
}

static void write_ppm(const std::string& path, const std::vector<uint8_t>& rgb) {
    FILE* f = fopen(path.c_str(), "wb");
    if (!f) return;
    fprintf(f, "P6\n%d %d\n255\n", W, H);
    for (int y = H - 1; y >= 0; y--) fwrite(&rgb[size_t(y) * W * 3], 1, W * 3, f);
    fclose(f);
}

// Each preset in order for N/presets frames. The stats use the luma after the limiter, so
// they show what the TV shows.
static int headless(int frames, const std::string& outdir, const Opts& o) {
    EGLDisplay dpy; EGLSurface surf; EGLContext ctx;
    if (!egl_headless(dpy, surf, ctx)) { fprintf(stderr, "no headless EGL context\n"); return 2; }
    fprintf(stderr, "GL: %s | %s\n", glGetString(GL_RENDERER), glGetString(GL_VERSION));
    mkdir(outdir.c_str(), 0755);
    projectm_handle pm = make_pm(o.tex);

    Pcm pcm;
    bool use_tone = isatty(0);
    std::thread rd;
    if (!use_tone) {
        rd = std::thread(reader, &pcm);
        for (int i = 0; i < 50 && !pcm.got && !pcm.eof; i++) usleep(10000);
        use_tone = !pcm.got;
    }
    fprintf(stderr, "audio: %s\n", use_tone ? "test tone" : "stdin");

    const auto& presets = o.presets;
    size_t np = std::max<size_t>(1, presets.size());
    int per = std::max(1, frames / int(np));
    std::vector<int16_t> s;
    std::vector<uint8_t> rgb(size_t(W) * H * 3);
    std::vector<double> ms;
    uint64_t tt = 0;
    Limiter lim{o.dim, o.rise};
    double prev = -1, max_d = 0, max_raw = 0, prev_raw = -1, sum_l = 0;
    int flashes = 0, raw_flashes = 0, pn = -1;
    rusage r0; getrusage(RUSAGE_SELF, &r0);
    auto t0 = Clock::now(), next = t0;
    auto report = [&](int p) {
        printf("preset %s: mean luma %.2f, max delta %.3f (raw %.3f), big deltas %d (raw %d)\n",
               presets[p].c_str(), sum_l / per, max_d, max_raw, flashes, raw_flashes);
    };
    for (int f = 0; f < frames && !g_stop; f++) {
        int p = std::min<int>(f / per, int(np) - 1);
        if (p != pn) {
            if (pn >= 0) report(pn);
            pn = p; max_d = max_raw = sum_l = 0; flashes = raw_flashes = 0; prev = prev_raw = -1;
            if (!presets.empty()) projectm_load_preset_file(pm, presets[p].c_str(), false);
        }
        if (use_tone) tone(s, RATE / 60, tt), projectm_pcm_add_int16(pm, s.data(), s.size() / 2, PROJECTM_STEREO);
        else if (size_t n = pcm.take(s, 2048)) projectm_pcm_add_int16(pm, s.data(), n, PROJECTM_STEREO);

        auto a = Clock::now();
        glViewport(0, 0, W, H);
        projectm_opengl_render_frame(pm);
        glFinish();
        ms.push_back(std::chrono::duration<double, std::milli>(Clock::now() - a).count());

        glReadPixels(0, 0, W, H, GL_RGB, GL_UNSIGNED_BYTE, rgb.data());
        double l = 0;
        for (size_t i = 0; i < rgb.size(); i += 3 * 7) l += 0.2126 * rgb[i] + 0.7152 * rgb[i + 1] + 0.0722 * rgb[i + 2];
        l /= (rgb.size() / (3.0 * 7)) * 255.0;
        double shown = l * (1.0 - lim.alpha(l));
        if (prev >= 0) {
            double d = fabs(shown - prev), dr = fabs(l - prev_raw);
            max_d = std::max(max_d, d); max_raw = std::max(max_raw, dr);
            if (d > 0.10) flashes++;
            if (dr > 0.10) raw_flashes++;
        }
        prev = shown; prev_raw = l; sum_l += shown;
        int in = f - p * per;
        if (in == per / 2 || in == per - 1) {
            char nm[64]; snprintf(nm, sizeof nm, "/f%05d_p%02d.ppm", f, p);
            write_ppm(outdir + nm, rgb);
        }
        eglSwapBuffers(dpy, surf);
        next += std::chrono::microseconds(16667);  // 60 fps pacing so the audio stays real time
        std::this_thread::sleep_until(next);
    }
    if (pn >= 0) report(pn);
    double wall = since(t0);
    rusage r1; getrusage(RUSAGE_SELF, &r1);
    double cpu = (r1.ru_utime.tv_sec - r0.ru_utime.tv_sec + r1.ru_stime.tv_sec - r0.ru_stime.tv_sec) +
                 (r1.ru_utime.tv_usec - r0.ru_utime.tv_usec + r1.ru_stime.tv_usec - r0.ru_stime.tv_usec) / 1e6;
    std::vector<double> so = ms;
    std::sort(so.begin(), so.end());
    double avg = 0; for (double v : ms) avg += v; avg /= ms.size();
    printf("frames %zu, render avg %.2f ms, median %.2f ms, p95 %.2f ms, max %.2f ms; "
           "CPU %.0f%% of one core at 60 fps (incl. readback)\n",
           ms.size(), avg, so[so.size() / 2], so[so.size() * 95 / 100], so.back(), 100 * cpu / wall);
    g_stop = true;
    if (rd.joinable()) rd.join();
    projectm_destroy(pm);
    return 0;
}
#endif  // BPX_HEADLESS

// ------------------------------------------------------------------ window: GL helpers
static GLuint shader(GLenum t, const char* src) {
    GLuint s = glCreateShader(t);
    glShaderSource(s, 1, &src, nullptr);
    glCompileShader(s);
    GLint ok = 0; glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) { char log[512]; glGetShaderInfoLog(s, sizeof log, nullptr, log); fprintf(stderr, "shader: %s\n", log); }
    return s;
}

// One program for the black layer and the title: a rect in NDC, a colour or a texture.
struct Quad {
    GLuint prog = 0, vao = 0, vbo = 0;
    GLint u_rect, u_color, u_tex, u_use_tex;
    void init() {
        const char* vs = "#version 330 core\nlayout(location=0) in vec2 p; uniform vec4 rect; out vec2 uv;\n"
                         "void main(){ uv = vec2(p.x, 1.0 - p.y); gl_Position = vec4(rect.xy + p * rect.zw, 0.0, 1.0); }\n";
        const char* fs = "#version 330 core\nin vec2 uv; uniform vec4 color; uniform sampler2D tex; uniform int use_tex;\n"
                         "out vec4 o; void main(){ if (use_tex == 1) { o = texture(tex, uv) * color.a; }"
                         " else o = color; }\n";
        prog = glCreateProgram();
        glAttachShader(prog, shader(GL_VERTEX_SHADER, vs));
        glAttachShader(prog, shader(GL_FRAGMENT_SHADER, fs));
        glLinkProgram(prog);
        u_rect = glGetUniformLocation(prog, "rect");
        u_color = glGetUniformLocation(prog, "color");
        u_tex = glGetUniformLocation(prog, "tex");
        u_use_tex = glGetUniformLocation(prog, "use_tex");
        const float v[] = {0, 0, 1, 0, 0, 1, 1, 1};
        glGenVertexArrays(1, &vao);
        glBindVertexArray(vao);
        glGenBuffers(1, &vbo);
        glBindBuffer(GL_ARRAY_BUFFER, vbo);
        glBufferData(GL_ARRAY_BUFFER, sizeof v, v, GL_STATIC_DRAW);
        glEnableVertexAttribArray(0);
        glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, nullptr);
        glBindVertexArray(0);
    }
    // x, y, w, h in 640x480 pixels from the top left.
    void draw(float x, float y, float w, float h, float r, float g, float b, float a, GLuint tex = 0) {
        glUseProgram(prog);
        glBindVertexArray(vao);
        glEnable(GL_BLEND);
        if (tex) glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);   // the title is premultiplied
        else glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
        glUniform4f(u_rect, x / W * 2 - 1, 1 - (y + h) / H * 2, w / W * 2, h / H * 2);
        glUniform4f(u_color, r, g, b, a);
        glUniform1i(u_use_tex, tex ? 1 : 0);
        if (tex) { glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, tex); glUniform1i(u_tex, 0); }
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
        if (tex) glBindTexture(GL_TEXTURE_2D, 0);
        glBindVertexArray(0);
        glUseProgram(0);
        glDisable(GL_BLEND);
    }
};

// The mean luma of the back buffer: a linear blit to 512x256 (under 2x down, so the taps
// cover every pixel), power-of-two mipmaps, one read of the 1x1 level.
struct Meter {
    static const int MW = 512, MH = 256, ML = 9;   // level 9 is 1x1
    GLuint tex = 0, fbo = 0;
    void init() {
        glGenTextures(1, &tex);
        glBindTexture(GL_TEXTURE_2D, tex);
        for (int l = 0, w = MW, h = MH; l <= ML; l++, w = std::max(1, w / 2), h = std::max(1, h / 2))
            glTexImage2D(GL_TEXTURE_2D, l, GL_RGBA8, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, nullptr);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, ML);
        glGenFramebuffers(1, &fbo);
        glBindFramebuffer(GL_FRAMEBUFFER, fbo);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0);
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
    }
    double luma(int dw, int dh) {
        glActiveTexture(GL_TEXTURE0);
        glBindFramebuffer(GL_READ_FRAMEBUFFER, 0);
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER, fbo);
        glBlitFramebuffer(0, 0, dw, dh, 0, 0, MW, MH, GL_COLOR_BUFFER_BIT, GL_LINEAR);
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        glBindTexture(GL_TEXTURE_2D, tex);
        glGenerateMipmap(GL_TEXTURE_2D);
        uint8_t px[4] = {0, 0, 0, 0};
        glGetTexImage(GL_TEXTURE_2D, ML, GL_RGBA, GL_UNSIGNED_BYTE, px);
        glBindTexture(GL_TEXTURE_2D, 0);
        return (0.2126 * px[0] + 0.7152 * px[1] + 0.0722 * px[2]) / 255.0;
    }
};

// The title from the app: a texture that fades in and out.
struct Title {
    GLuint tex = 0;
    float x = 0, y = 0, w = 0, h = 0;
    double secs = 0;
    Clock::time_point t0;
    bool load(const char* path, int iw, int ih, int ix, int iy, double s) {
        if (iw <= 0 || ih <= 0 || iw > W || ih > H) return false;
        FILE* f = fopen(path, "rb");
        if (!f) return false;
        std::vector<uint8_t> px(size_t(iw) * ih * 4);
        size_t n = fread(px.data(), 1, px.size(), f);
        fclose(f);
        if (n != px.size()) return false;
        if (!tex) glGenTextures(1, &tex);
        glBindTexture(GL_TEXTURE_2D, tex);
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, iw, ih, 0, GL_RGBA, GL_UNSIGNED_BYTE, px.data());
        glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        x = ix; y = iy; w = iw; h = ih; secs = s; t0 = Clock::now();
        return true;
    }
    float alpha() const {
        if (!tex || secs <= 0) return 0;
        double t = since(t0), fade = 0.3;
        if (t >= secs) return 0;
        return float(std::min({1.0, t / fade, (secs - t) / fade}));
    }
};

// ------------------------------------------------------------------ window
static void say(const char* fmt, const char* arg) {
    printf(fmt, arg);
    fflush(stdout);
}

static const char* key_action(const SDL_KeyboardEvent& k) {
    switch (k.keysym.sym) {
        case SDLK_LEFT: return "left";
        case SDLK_RIGHT: return "right";
        case SDLK_UP: return "up";
        case SDLK_DOWN: return "down";
        case SDLK_RETURN: case SDLK_KP_ENTER: return "ok";
        case SDLK_ESCAPE: case SDLK_BACKSPACE: case SDLK_AC_BACK: case SDLK_AC_HOME: return "back";
        case SDLK_F4: return (k.keysym.mod & KMOD_ALT) ? "back" : nullptr;  // Hotkey+Start
        case SDLK_LEFTBRACKET: return "lt";
        case SDLK_RIGHTBRACKET: return "rt";
        case SDLK_PAGEUP: case SDLK_q: return "lb";
        case SDLK_PAGEDOWN: case SDLK_e: return "rb";
        case SDLK_AUDIOREWIND: return "left";
        case SDLK_AUDIOFASTFORWARD: return "right";
        case SDLK_x: return "x";
        case SDLK_y: return "y";
        case SDLK_SPACE: case SDLK_m: case SDLK_MENU: return "menu";
        case SDLK_p: case SDLK_AUDIOPLAY: return "playpause";   // as in the video window
        case SDLK_AUDIOSTOP: return "stop";
        case SDLK_AUDIONEXT: return "next";
        case SDLK_AUDIOPREV: return "prev";
        default: return nullptr;
    }
}

static int window(const Opts& o) {
    SDL_SetHint(SDL_HINT_NO_SIGNAL_HANDLERS, "1");  // SIGTERM stays ours
    SDL_SetHint(SDL_HINT_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR, "1");
    if (SDL_Init(SDL_INIT_VIDEO) != 0) { fprintf(stderr, "SDL_Init: %s\n", SDL_GetError()); return 2; }
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION, 3);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION, 3);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK, SDL_GL_CONTEXT_PROFILE_CORE);
    SDL_Window* win = SDL_CreateWindow("Batoplex Visualizer", 0, 0, W, H,
                                       SDL_WINDOW_OPENGL | SDL_WINDOW_FULLSCREEN_DESKTOP | SDL_WINDOW_BORDERLESS);
    if (!win) { fprintf(stderr, "SDL_CreateWindow: %s\n", SDL_GetError()); return 2; }
    SDL_GLContext gl = SDL_GL_CreateContext(win);
    if (!gl) { fprintf(stderr, "GL context: %s\n", SDL_GetError()); return 2; }
    bool vsync = SDL_GL_SetSwapInterval(1) == 0;     // else pace the loop to 60 frames a second
    if (!vsync) fprintf(stderr, "no vsync: %s\n", SDL_GetError());
    SDL_ShowCursor(SDL_DISABLE);
    int dw, dh; SDL_GL_GetDrawableSize(win, &dw, &dh);
    projectm_handle pm = make_pm(o.tex);
    projectm_set_window_size(pm, dw, dh);

    Quad quad; quad.init();
    Meter meter; meter.init();
    Title title;
    Limiter lim{o.dim, o.rise};
    Playlist pl; pl.all = o.presets;
    auto load = [&](const std::string* p, double blend) {
        if (!p) return;
        projectm_set_soft_cut_duration(pm, blend);
        projectm_load_preset_file(pm, p->c_str(), blend > 0);
    };
    load(pl.next(), 0);
    auto shown_at = Clock::now();

    Pcm pcm;
    std::thread rd(reader, &pcm);
    std::vector<int16_t> s;
    std::string ctl_buf;
    if (o.ctl >= 0) fcntl(o.ctl, F_SETFL, fcntl(o.ctl, F_GETFL) | O_NONBLOCK);
    bool ready = false, quit_sent = false;
    auto frame_t = Clock::now();
    while (!g_stop) {
        SDL_Event e;
        while (SDL_PollEvent(&e)) {
            const char* act = nullptr;
            if (e.type == SDL_QUIT || (e.type == SDL_WINDOWEVENT && e.window.event == SDL_WINDOWEVENT_CLOSE)) {
                if (!quit_sent) act = "back";   // the window manager closed us (Alt+F4)
                quit_sent = true;
                if (o.ctl < 0) g_stop = true;
            } else if (e.type == SDL_KEYDOWN && !e.key.repeat) {
                act = key_action(e.key);
                if (o.ctl < 0 && act) g_stop = true;   // stand-alone test: any key exits
            }
            if (act) say("key %s\n", act);
        }
        if (o.ctl >= 0) {
            char b[512];
            for (;;) {
                ssize_t r = read(o.ctl, b, sizeof b);
                if (r > 0) { ctl_buf.append(b, r); continue; }
                if (r == 0) g_stop = true;               // the app closed the pipe
                break;                                   // EAGAIN: nothing more now
            }
            size_t nl;
            while ((nl = ctl_buf.find('\n')) != std::string::npos) {
                std::string line = ctl_buf.substr(0, nl);
                ctl_buf.erase(0, nl + 1);
                char path[256]; int iw, ih, ix, iy; double secs;
                if (line == "next") load(pl.next(), 1.0), shown_at = Clock::now();
                else if (line == "prev") load(pl.prev(), 1.0), shown_at = Clock::now();
                else if (line == "quit") g_stop = true;
                else if (sscanf(line.c_str(), "overlay %255s %d %d %d %d %lf", path, &iw, &ih, &ix, &iy, &secs) == 6) {
                    if (!title.load(path, iw, ih, ix, iy, secs)) fprintf(stderr, "overlay not loaded: %s\n", path);
                }
            }
            if (ctl_buf.size() > 4096) ctl_buf.clear();
        }
        if (g_preset_failed.exchange(false)) {
            pl.drop_current();
            load(pl.next(), 0);
            shown_at = Clock::now();
        }
        if (size_t n = pcm.take(s, 2048)) projectm_pcm_add_int16(pm, s.data(), n, PROJECTM_STEREO);
        if (pl.all.size() > 1 && since(shown_at) > o.secs) {
            load(pl.next(), 4.0);   // soft cut (crossfade)
            shown_at = Clock::now();
        }
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        glViewport(0, 0, dw, dh);
        projectm_opengl_render_frame(pm);
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        glViewport(0, 0, dw, dh);
        double a = lim.alpha(meter.luma(dw, dh));
        quad.draw(0, 0, W, H, 0, 0, 0, float(a));
        if (float ta = title.alpha()) quad.draw(title.x, title.y, title.w, title.h, 1, 1, 1, ta, title.tex);
        SDL_GL_SwapWindow(win);
        if (!vsync) {
            auto next = frame_t + std::chrono::microseconds(16667);
            if (Clock::now() < next) std::this_thread::sleep_until(next);
            frame_t = std::max(next, Clock::now());   // after a stall: no catch-up burst
        }
        if (!ready) { say("ready %s\n", (const char*)glGetString(GL_RENDERER)); ready = true; }
    }
    g_stop = true;
    rd.join();
    projectm_destroy(pm);
    SDL_GL_DeleteContext(gl);
    SDL_DestroyWindow(win);
    SDL_Quit();
    return 0;
}

static void on_sig(int) { g_stop = true; }

int main(int argc, char** argv) {
    prctl(PR_SET_PDEATHSIG, SIGTERM);   // the app went away: go too
    signal(SIGTERM, on_sig);
    signal(SIGINT, on_sig);
    signal(SIGPIPE, SIG_IGN);
    std::vector<std::string> args(argv + 1, argv + argc), rest;
    Opts o;
    for (size_t i = 0; i < args.size(); i++) {
        const std::string& a = args[i];
        bool more = i + 1 < args.size();
        if (a == "--tex" && more) o.tex.push_back(args[++i]);
        else if (a == "--secs" && more) o.secs = std::max(5.0, atof(args[++i].c_str()));
        else if (a == "--ctl-fd" && more) o.ctl = atoi(args[++i].c_str());
        else if (a == "--dim" && more) o.dim = std::min(0.9, std::max(0.0, atof(args[++i].c_str())));
        else if (a == "--flash-rise" && more) o.rise = std::min(1.0, std::max(0.005, atof(args[++i].c_str())));
        else rest.push_back(a);
    }
#ifdef BPX_HEADLESS
    if (rest.size() >= 3 && rest[0] == "--headless") {
        for (size_t i = 3; i < rest.size(); i++) collect(rest[i], o.presets);
        return headless(atoi(rest[1].c_str()), rest[2], o);
    }
#endif
    if (!rest.empty() && rest[0] == "--window") {
        for (size_t i = 1; i < rest.size(); i++) collect(rest[i], o.presets);
        return window(o);
    }
    fprintf(stderr, "usage: %s --window [options] PRESET... | --headless N OUT_DIR [options] PRESET...\n", argv[0]);
    return 1;
}
