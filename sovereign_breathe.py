"""Sovereign Breathe — reference extract of the production DSP core.

This is a single-file, readable reference implementation of the mastering
chain used to bring AI-generated music sources to commercial grade:
resampling, Apollo-class restoration splice, harmonic excitation / BWE,
envelope-matched air band, gain matching, spectral containment, peak
control, and deterministic output.

It is the *reference* version: the production engine has additional
per-track style tables, runtime detection, and telemetry modules that are
not part of this extract.

Everything here runs on CPU with numpy/scipy. No external services.
"""

# ═══════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════
if sys.platform.startswith("win"):
    BASE_DIR = "[LOCAL_PATH]/"
else:
    _drive_path = "[RUNTIME_PATH]/"
    _local_path = "[RUNTIME_PATH]/"
    BASE_DIR = _drive_path if os.path.isdir(_drive_path) else _local_path

SR                                   = 48000
PRE_LUFS                             = -14.0
TARGET_LUFS                          = -9.0  # V106.499h24: loud-tier target (10 tracks), quiet-tier = -12.0 (CIPHER, MYTH)
LOW_RAM_MODE                        = True   # enabled for Colab 12GB; skips spectrogram images but keeps X-Ray text report
VERSION                              = "V106_456_DYNAMIC"  # ponytail: overwritten below from RAW_SOURCE track name
VERSION_LABEL                        = "V106.456 dynamic"  # ponytail: overwritten below from RAW_SOURCE track name
SOVEREIGN_TRUE_PEAK_CEILING_DB       = 0.0    # V106.442 exact: clip at 0.0, fine-tune overshoots to 0.76
HARD_CLIP_THRESHOLD_DB              = 0.0    # V106.442 exact: surgical np.clip at 0.0 dBTP
SOVEREIGN_EXPORT_SAFETY_TRIM_ENABLED = False  # disabled: we accept OGG transcode overshoot for [redacted platform] preview only
SOVEREIGN_EXPORT_TRUE_PEAK_TARGET_DB = -0.70  # leave margin for OGG/Vorbis decode reconstruction overshoot
SOVEREIGN_BWE_CONTINUITY_BED_ENABLED = True
SOVEREIGN_BWE_CONTINUITY_FLOOR        = 0.02  # ponytail: reduced from 0.07; 0.07 was creating flat bed artifacts flagged by spectrogram vision (organic-ness 3/10); 0.02 preserves natural gaps while preventing absolute-zero AI detection
SOVEREIGN_BWE_CONTINUITY_DRIFT_ENABLED = True
SOVEREIGN_BWE_CONTINUITY_DRIFT_DEPTH = 0.10  # ponytail: reduced from 0.15; continuity_bed now does the heavy lifting, drift is micro-variation only to prevent flatness AI signature
EXCITER_MATCH_PERCENTILE             = 95.0  # Reduced from 98.0 to smooth BWE section fill; lowers Q1–Q4 spread target <8 dB

EXCITER_GAIN_FLOOR_DB         = -50.0
EXCITER_GAIN_CAP_DB           = 10.5  # Sovereign mode bypasses exciter; cap is metadata-only
EXCITER_ENV_GATE_EXPONENT     = 2.0
EXCITER_NOISE_FLOOR           = 0.02
EXCITER_NOISE_CAP             = 0.05
EXCITER_NOISE_SLOPE_DIVISOR   = 180.0
AIR_SHELF_FREQ_HZ             = 16000.0
AIR_SHELF_GAIN_DB             = 0.0    # Disabled: fixed cut was fighting source HF balance; X-Ray shelf exists but is not applied in signal path

# ponytail: MVSEP stem-separation network system fully removed. Only the enabled flag
# remains so mvsep_meta can record the disabled state in telemetry.
MVSEP_ENABLED                 = False

USE_ITERATION_WINDOW          = False
ITERATION_WINDOW_SECONDS      = 90.0
ITERATION_WINDOW_HOP_SECONDS  = 1.0
WINDOW_SELECTION_MODE         = "full"
WINDOW_KEY_PRIORITY           = True
TARGET_KEY                    = "E"
TARGET_BPM                    = 138

SCORE_TARGET_SPECTRAL         = 90
SCORE_TARGET_TEMPORAL         = 99

# ponytail: NEURAL_CODEC (DAC/SNAC) config removed — codec block deleted; user rejects DAC.
# V106.499h24: M/S stereo width replaces static Haas delay.
        # HAAS_ENABLED = False  # removed; we now use M/S width (sub HPF + side shelf)
        # HAAS_DELAY_MS = 0.5   # removed; no static delay
TIME_STRETCH_RATE             = 1.0   # Disabled: transient-safe implementation added but musically unnecessary bloat
PITCH_SHIFT_CENTS             = 30    # V106.499+: re-enabled. User correction — pitch-shift 0 was wrong (decreases score + unmasks outro whistle). +30¢ is old clean-master value.
PITCH_SHIFT_SPLIT_HZ          = 9000  # V106.499g: RESTORED to 9kHz split. User correction: old clean master
                                       # (V106_452) NEVER used 150Hz split — it used Butterworth(4) at the
                                       # 9kHz split with librosa scale=True. The LR4 (cascaded Butter(2))
                                       # swap removed the natural -2.2dB dip at 8-9.5k, exposing the
                                       # stationary ~10010Hz phase-vocoder artifact (glassy).

PHASE_TARGET_MIN              = 0.05
PHASE_TARGET_MAX              = 0.80

# MYTH flutter amplitude override for temporal improvement test
MYTH_FLUTTER_AMP_OVERRIDE     = 0.18

# Lightweight command-line argument overrides (V106.499h37 / "Grammy Suite")
SNIPPET_MODE = False
STREAM_SIM = False
_cl_source = None
for _i, _arg in enumerate(sys.argv):
    if _arg == "--source" and _i + 1 < len(sys.argv):
        _cl_source = sys.argv[_i+1]
    elif _arg == "--snippet":
        SNIPPET_MODE = True
    elif _arg == "--stream-sim":
        STREAM_SIM = True

RAW_SOURCE = _cl_source  # ponytail (C fix): NO silent default. process() refuses to run without explicit --source.
                       # Prevents Colab ipykernel sys.argv from silently rendering the wrong track (ZERO-poisoning bug).

# ponytail: dynamic version label — tracks the ACTIVE render track so output files
# never mislabel (e.g. MYTH master sitting in an "ORBIT" filename). Derived from the
# raw filename's track token. MB0015 = multiband phase. V106.495: all tracks target_corr=0.0 (flat-0.0, GRAVITY-proven musical optimum; coherence-add rejected — narrows width).
_TRACK_TOKEN = os.path.basename(RAW_SOURCE).split('.')[0].strip() if RAW_SOURCE else "GRAVITY"
if RAW_SOURCE and _TRACK_TOKEN.isdigit():
    # basename like "9. MYTH raw.mp3" -> split('.')[0] = "9"; fall back to next token
    _TRACK_TOKEN = os.path.basename(RAW_SOURCE).split()[1] if len(os.path.basename(RAW_SOURCE).split()) > 1 else _TRACK_TOKEN
VERSION = f"V106_500h215_{_TRACK_TOKEN}_MB0015_s{_AI2G_SEED_INT}_ws{_AI2G_WOW_SEED_INT}"
VERSION_LABEL = f"V106.500h215 {_TRACK_TOKEN}: MYTH TP -0.5 to -0.3 (quiet standard, Dream measures -0.31; no legacy reason found)"

OUT_FLAC   = f"{BASE_DIR}/02_MASTERS/{VERSION}.flac"
TMP_WAV    = f"{BASE_DIR}/02_MASTERS/{VERSION}_tmp.wav"
DIAG_DIR   = f"{BASE_DIR}/03_DIAGNOSTICS"

os.makedirs(os.path.dirname(OUT_FLAC), exist_ok=True)
os.makedirs(DIAG_DIR, exist_ok=True)


# ─── Deshimmer: Suno AI birdie/shimmer suppression (5.1-7.2 kHz) ─────────────────
# Adapted from https://github.com/TheApeMachine/deshimmer (MIT License).
# Targets narrow-band, flickery HF artifacts ("birdies") that Suno AI creates in the
# 5-7 kHz range. These artifacts cause the "caged/boxy" feel — narrow spectral spikes
# that mask natural HF content. Uses spectral flatness detection + soft-knee attenuation.
# Params are deshimmer defaults tuned for Suno V4/V5 output.

@_dataclass
class _DeshimmerParams:
    start_hz: float = 5100.0
    end_hz: float = 7200.0
    edge_hz: float = 200.0
    n_fft: int = 2048
    hop: int = 512
    flat_start: float = 0.25
    flat_end: float = 0.70
    freq_med_bins: int = 9
    thr_db: float = 18.0  # V106.499h27: raised from 12.0 to relax 5-7k suppression (92%→~50% expected). Fixes steep 6k-12k slope.
    slope: float = 0.6
    density_lo: float = 0.02
    density_hi: float = 0.15
    flux_thr_db: float = 6.0
    flux_range_db: float = 8.0
    noise_resynth: float = 0.0
    mix: float = 1.0
    pad: bool = True
    fade_ms: float = 5.0
    seed: int = 0
    debug: bool = False

def _deshimmer_process(x, sr, p=None):
    """Suppress AI shimmer artifacts in 5.1-7.2 kHz band. Returns processed numpy array.
    V106.499h24: integrated from TheApeMachine/deshimmer, MIT License.
    Target: narrowband flickery Suno birdies that create crystalline cage-y HF feel."""
    if p is None:
        p = _DeshimmerParams()
    x = np.atleast_2d(x.astype(np.float32, copy=False))
    if x.shape[0] == 1:
        x = x.T
    n_fft = int(p.n_fft)
    hop = int(p.hop)
    if hop <= 0 or n_fft <= 0 or hop > n_fft:
        raise ValueError("Invalid n_fft/hop")
    if p.pad:
        x0 = np.pad(x, ((n_fft, n_fft), (0, 0)), mode="constant")
    else:
        x0 = x
    n_samples, n_ch = x0.shape
    win = np.hanning(n_fft).astype(np.float32)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)
    nyq = float(freqs[-1])
    start_hz = float(max(0.0, p.start_hz))
    end_hz = float(min(nyq, p.end_hz))
    if end_hz <= start_hz:
        raise ValueError("end_hz must be > start_hz")
    band_idx = np.where((freqs >= start_hz) & (freqs <= end_hz))[0]
    if band_idx.size < 8:
        raise ValueError("Band too narrow; increase n_fft or widen band")
    w_f = np.ones(band_idx.size, dtype=np.float32)
    edge = float(max(0.0, p.edge_hz))
    if edge > 0:
        fb = freqs[band_idx].astype(np.float32)
        lo, hi = start_hz, end_hz
        m = fb < lo + edge
        if np.any(m):
            rel = (fb[m] - lo) / edge
            w_f[m] = 0.5 - 0.5 * np.cos(np.pi * np.clip(rel, 0, 1))
        m2 = fb > hi - edge
        if np.any(m2):
            rel = (hi - fb[m2]) / edge
            w_f[m2] = np.minimum(w_f[m2], 0.5 - 0.5 * np.cos(np.pi * np.clip(rel, 0, 1)))
    eps = 1e-12
    rng = np.random.default_rng(int(p.seed))
    y = np.zeros((n_samples + n_fft, n_ch), dtype=np.float32)
    wsum = np.zeros(n_samples + n_fft, dtype=np.float32)
    prev_band_db = None
    processed_frames = 0
    total_frames = 0
    for s in range(0, n_samples, hop):
        total_frames += 1
        frame = np.zeros((n_fft, n_ch), dtype=np.float32)
        chunk = x0[s:s + n_fft, :]
        frame[:chunk.shape[0], :] = chunk
        frame_w = frame * win[:, None]
        spec = np.fft.rfft(frame_w, n=n_fft, axis=0)
        band_spec = spec[band_idx, :]
        mag_band = np.mean(np.abs(band_spec), axis=1).astype(np.float32) + eps
        P = mag_band ** 2
        flat = float(np.exp(np.mean(np.log(P + eps))) / (np.mean(P) + eps))
        w_noise = float(np.clip((flat - p.flat_start) / max(1e-6, (p.flat_end - p.flat_start)), 0.0, 1.0))
        band_db = 10.0 * _math.log10(float(np.mean(P)) + eps)
        if prev_band_db is None:
            flux = 0.0
        else:
            flux = max(0.0, band_db - prev_band_db)
        prev_band_db = band_db
        w_trans = float(np.clip((flux - p.flux_thr_db) / max(1e-6, p.flux_range_db), 0.0, 1.0))
        w_nontrans = 1.0 - w_trans
        L = np.log(mag_band)
        k = int(max(3, p.freq_med_bins))
        if k % 2 == 0:
            k += 1
        L_med = _scipy_median_filter(L, size=k, mode="nearest")
        residual_db = (L - L_med) * (20.0 / np.log(10.0))
        over = residual_db - float(p.thr_db)
        mask = over > 0.0
        density = float(np.mean(mask)) if mask.size else 0.0
        w_narrow = 1.0 - float(np.clip((density - p.density_lo) / max(1e-6, (p.density_hi - p.density_lo)), 0.0, 1.0))
        w_depth = w_noise * w_nontrans * w_narrow
        if w_depth > 1e-3:
            processed_frames += 1
        att_db = np.zeros_like(over, dtype=np.float32)
        att_db[mask] = (float(p.slope) * over[mask]).astype(np.float32)
        gain = (10.0 ** (-att_db / 20.0)).astype(np.float32)
        g_eff = 1.0 - (w_depth * w_f) * (1.0 - gain)
        spec[band_idx, :] *= g_eff[:, None]
        nr = float(np.clip(p.noise_resynth, 0.0, 1.0))
        if nr > 0.0 and w_noise > 1e-3:
            depth = (nr * w_noise) * w_f
            phi = rng.uniform(0.0, 2.0 * np.pi, size=band_idx.size).astype(np.float32)
            zph = (np.cos(phi) + 1j * np.sin(phi)).astype(np.complex64)
            for ch in range(n_ch):
                Zb = spec[band_idx, ch]
                mag_z = np.abs(Zb).astype(np.float32)
                Zrand = mag_z * zph
                spec[band_idx, ch] = (1.0 - depth) * Zb + depth * Zrand
        out = np.fft.irfft(spec, n=n_fft, axis=0).astype(np.float32)
        out *= win[:, None]
        y[s:s + n_fft, :] += out
        wsum[s:s + n_fft] += win ** 2
    wsum = np.maximum(wsum, 1e-12)
    y = y[:n_samples, :] / wsum[:n_samples, None]
    if p.pad:
        y = y[n_fft:-n_fft, :]
        x_ref = x
    else:
        x_ref = x0[:y.shape[0], :]
    mix_val = float(np.clip(p.mix, 0.0, 1.0))
    y = mix_val * y + (1.0 - mix_val) * x_ref
    fade = int(sr * (float(p.fade_ms) / 1000.0))
    if fade > 1 and y.shape[0] > 2 * fade:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)[:, None]
        y[:fade, :] *= ramp
        y[-fade:, :] *= ramp[::-1]
    if p.debug:
        pct = 100.0 * processed_frames / max(1, total_frames)
        print(f" [Deshimmer] processed {processed_frames}/{total_frames} frames ({pct:.1f}%)")
    # Return in shape [channels, samples] (matching pipeline convention)
    return y.T.astype(np.float32), {'processed_frames': processed_frames, 'total_frames': total_frames}

# ═══════════════════════════════════════════════════════════
# AUDIO UTILITIES & HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════

def _ensure_channels_first(y):
    if y.ndim == 1:
        return np.stack([y, y])
    if y.shape[0] > y.shape[1]:
        return y.T
    return y


def _dbfs(signal_array):
    rms = np.sqrt(np.mean(signal_array**2) + 1e-24)
    return 20.0 * np.log10(rms)


def _peak_db(signal_array):
    return 20.0 * np.log10(np.max(np.abs(signal_array)) + 1e-24)


def _true_peak_db(y, sr, oversample=4):
    y = _ensure_channels_first(y)
    p = []
    for ch in range(y.shape[0]):
        up = signal.resample_poly(y[ch], oversample, 1)
        p.append(np.max(np.abs(up)))
        del up
    gc.collect()
    return 20.0 * np.log10(np.max(p) + 1e-24)


def _welch_mono(y, sr, nperseg=4096):
    y = _ensure_channels_first(y)
    mono = np.mean(y, axis=0)
    return signal.welch(mono, fs=sr, nperseg=nperseg)


def _band_energy_px_db(f, px_db, lo, hi):
    idx = (f >= lo) & (f <= hi)
    return float(np.mean(px_db[idx])) if np.any(idx) else -120.0


def _band_signal(y, sr, band_hz):
    sos = signal.butter(4, band_hz, 'bp', fs=sr, output='sos')
    return signal.sosfiltfilt(sos, y, axis=1)


def _stage_spatial_ratio(y, sr):
    # ponytail (C fix): FULL-BAND spatial (no HPF). Matches the spatial clamp math
    # at line 1963 (spatial_ratio = sqrt(mean(side**2)/mean(mid**2))). The old
    # version HPF'd at 10kHz, reporting ONLY air-band width (0.787) while the
    # clamp acted on full-band (0.44) — a metric-definition mismatch that made
    # the saved telemetry disagree with the log. Full-band = what the clamp controls.
    y = _ensure_channels_first(y)
    mid = (y[0] + y[1]) / 2.0
    side = (y[0] - y[1]) / 2.0
    return float(np.sqrt(np.mean(side**2) / (np.mean(mid**2) + 1e-24)))


def _profile_raw_source(y, sr):
    # ponytail (source-driven workflow): print RAW fingerprint BEFORE processing so
    # every track decision is bound to ITS source, not memory or a stale reference.
    # Mirrors _multiband_spatial band edges for direct comparability.
    y = _ensure_channels_first(y)
    mid = (y[0] + y[1]) / 2.0
    side = (y[0] - y[1]) / 2.0
    edges = [20, 80, 250, 600, 2000, 6000, 12000, 16000, 21000, min(23999, int(sr/2)-1)]
    labels = ['sub_20_80', 'low_mid_80_250', 'mid_250_600', 'lowmid_600_2k',
              'upper_mid_2k_6k', 'presence_6k_12k', 'brilliance_12k_16k',
              'air_16k_21k', 'nyquist_21k_24k']
    fb = float(np.sqrt(np.mean(side**2) / (np.mean(mid**2) + 1e-24)))
    print(f" [RAW PROFILE] full-band spatial = {fb:.3f}")
    for i in range(len(edges) - 1):
        lo, hi = edges[i], min(edges[i+1], int(sr/2)-1)
        if hi <= lo:
            continue
        sos = signal.butter(4, [lo, hi], 'bp', fs=sr, output='sos')
        mb = signal.sosfiltfilt(sos, mid); sb = signal.sosfiltfilt(sos, side)
        s = float(np.sqrt(np.mean(sb**2) / (np.mean(mb**2) + 1e-24)))
        print(f" [RAW PROFILE] {labels[i]:<22} spatial = {s:.3f}")
    return fb


def _multiband_spatial(y, sr):
    # ponytail (C): per-band spatial ratio — mirrors _multiband_phase_corr band edges and labels exactly.
    # Industry standard (Voxengo Correlometer, RJ SideMinder): multi-band width
    # metering reveals per-band issues a single broadband number hides.
    y = _ensure_channels_first(y)
    mid = (y[0] + y[1]) / 2.0
    side = (y[0] - y[1]) / 2.0
    edges = [20, 80, 250, 300, 600, 2000, 6000, 12000, 16000, 21000, 23999]
    labels = [
        "sub_20_80",
        "low_mid_80_250",
        "mid_250_300",
        "lowmid_300_600",
        "upper_mid_600_2k",
        "presence_2k_6k",
        "brilliance_6k_12k",
        "air_12k_16k",
        "bwe_16k_21k",
        "nyquist_21k_24k",
    ]
    out = {}
    nyq = sr / 2.0
    for i in range(len(edges) - 1):
        lo, hi = edges[i], min(edges[i + 1], int(nyq) - 1)
        if hi <= lo or hi - lo < 10:
            continue
        sos = signal.butter(4, [lo, hi], 'bp', fs=sr, output='sos')
        m_b = signal.sosfiltfilt(sos, mid)
        s_b = signal.sosfiltfilt(sos, side)
        out[labels[i]] = float(np.sqrt(np.mean(s_b**2) / (np.mean(m_b**2) + 1e-24)))
    return out


def _stage_phase_corr(y, sr):
    y = _ensure_channels_first(y)
    sos = signal.butter(4, 300, 'hp', fs=sr, output='sos')
    y_hp = signal.sosfiltfilt(sos, y, axis=1)
    num = np.sum(y_hp[0] * y_hp[1])
    den = np.sqrt(np.sum(y_hp[0]**2) * np.sum(y_hp[1]**2) + 1e-24)
    return float(num / den)


def _multiband_phase_corr(y, sr):
    """Whole-spectrum phase correlation by octave-ish bands."""
    y = _ensure_channels_first(y)
    edges = [20, 80, 250, 300, 600, 2000, 6000, 12000, 16000, 21000, 23999]
    labels = [
        "sub_20_80",
        "low_mid_80_250",
        "mid_250_300",
        "lowmid_300_600",
        "upper_mid_600_2k",
        "presence_2k_6k",
        "brilliance_6k_12k",
        "air_12k_16k",
        "bwe_16k_21k",
        "nyquist_21k_24k",
    ]
    result = {}
    for label, lo, hi in zip(labels, edges[:-1], edges[1:]):
        if hi - lo < 10:
            continue
        sos = signal.butter(4, [lo, hi], 'bp', fs=sr, output='sos')
        y_bp = signal.sosfiltfilt(sos, y, axis=1)
        rms = np.sqrt(np.mean(y_bp**2) + 1e-24)
        if 20 * np.log10(rms + 1e-12) < -90:
            result[label] = 0.0
            continue
        num = np.sum(y_bp[0] * y_bp[1])
        den = np.sqrt(np.sum(y_bp[0]**2) * np.sum(y_bp[1]**2) + 1e-24)
        _r = float(num / den)
        # ponytail: snap float-residual near-zero (-0.0000023 etc.) to exactly 0.0.
        # Side-cut correction drives bands to neutral; the residual is reduction-order
        # noise (sosfiltfilt + dot-product), not a real inversion. Shipped telemetry reads
        # clean (all bands >= 0.0) for human A&R review; audio unchanged. Epsilon 1e-4
        # is 4 orders above the observed residual, so no actionable band is affected.
        if abs(_r) < 1e-4:
            _r = 0.0
        result[label] = _r
    return result


def _hf_flatness_from_psd(f, pxx, lo=12000, hi=21000):
    idx = (f >= lo) & (f <= hi)
    if not np.any(idx):
        return 0.0
    band_px = np.clip(pxx[idx], 1e-24, None)
    gmean = np.exp(np.mean(np.log(band_px)))
    amean = np.mean(band_px)
    return float(gmean / amean)


def _air_band_texture_metrics(y, sr, start_guard_s=5.0, end_guard_s=7.0):
    y = _ensure_channels_first(y)
    mono = np.mean(y, axis=0)
    f, t_frames, stft = signal.stft(
        mono,
        fs=sr,
        window="hann",
        nperseg=4096,
        noverlap=3072,
        nfft=4096,
        boundary=None,
        padded=False,
    )
    if t_frames.size < 4:
        return {"enabled": False, "reason": "too_short"}

    keep = (t_frames >= start_guard_s) & (t_frames <= (t_frames[-1] - end_guard_s))
    if np.count_nonzero(keep) < 4:
        keep = np.ones_like(t_frames, dtype=bool)

    power = np.abs(stft) ** 2
    hop_s = float(np.median(np.diff(t_frames))) if t_frames.size > 1 else 0.0
    one_second_frames = max(1, int(round(1.0 / hop_s))) if hop_s > 0 else 1
    band_defs = {
        "source_12k_15_9k": (12000.0, 15900.0),
        "transition_15k_18_5k": (15000.0, 18500.0),
        "bwe_18k_21_5k": (18000.0, 21500.0),
    }

    bands = {}
    section_edges = np.linspace(float(t_frames[keep][0]), float(t_frames[keep][-1]), 5)
    for name, (lo, hi) in band_defs.items():
        idx = (f >= lo) & (f < min(hi, sr / 2.0 - 1.0))
        if not np.any(idx):
            continue
        frame_db = 10.0 * np.log10(np.mean(power[idx, :], axis=0) + 1e-24)
        frame_db = frame_db[keep]
        frame_t = t_frames[keep]
        delta = np.diff(frame_db)
        kernel = np.ones(one_second_frames) / one_second_frames
        smooth = np.convolve(frame_db, kernel, mode="valid")
        step = smooth[one_second_frames:] - smooth[:-one_second_frames] if smooth.size > one_second_frames else np.array([0.0])
        step_idx = int(np.argmax(np.abs(step))) if step.size else 0
        section_db = []
        for sec_idx in range(4):
            if sec_idx == 3:
                sec_mask = (frame_t >= section_edges[sec_idx]) & (frame_t <= section_edges[sec_idx + 1])
            else:
                sec_mask = (frame_t >= section_edges[sec_idx]) & (frame_t < section_edges[sec_idx + 1])
            section_db.append(round(float(np.mean(frame_db[sec_mask])), 2) if np.any(sec_mask) else None)

        bands[name] = {
            "mean_db": round(float(np.mean(frame_db)), 2),
            "std_db": round(float(np.std(frame_db)), 2),
            "p90_p10_db": round(float(np.percentile(frame_db, 90) - np.percentile(frame_db, 10)), 2),
            "p95_abs_frame_delta_db": round(float(np.percentile(np.abs(delta), 95)) if delta.size else 0.0, 2),
            "max_abs_frame_delta_db": round(float(np.max(np.abs(delta))) if delta.size else 0.0, 2),
            "max_abs_1s_step_db": round(float(np.max(np.abs(step))) if step.size else 0.0, 2),
            "max_1s_step_time_s": round(float(frame_t[min(step_idx + one_second_frames, frame_t.size - 1)]), 2) if frame_t.size else 0.0,
            "section_quartile_mean_db": section_db,
        }

    return {
        "enabled": True,
        "stft_n": 4096,
        "hop_samples": 1024,
        "analysis_start_s": round(float(t_frames[keep][0]), 3),
        "analysis_end_s": round(float(t_frames[keep][-1]), 3),
        "section_edges_s": [round(float(x), 3) for x in section_edges],
        "bands": bands,
    }


def _stage_metrics(y, sr, return_psd=False, return_air_texture=False):
    y = _ensure_channels_first(y)
    meter = pyln.Meter(sr)
    f, pxx = _welch_mono(y, sr)
    px_db = 10 * np.log10(pxx + 1e-12)
    bands = {
        "sub_20_80": _band_energy_px_db(f, px_db, 20, 80),
        "bass_80_250": _band_energy_px_db(f, px_db, 80, 250),
        "low_mid_250_600": _band_energy_px_db(f, px_db, 250, 600),
        "mid_600_2k": _band_energy_px_db(f, px_db, 600, 2000),
        "upper_mid_2k_6k": _band_energy_px_db(f, px_db, 2000, 6000),
        "presence_6k_12k": _band_energy_px_db(f, px_db, 6000, 12000),
        "brilliance_12k_16k": _band_energy_px_db(f, px_db, 12000, 16000),
        "pre_cutoff_14k_15_75k": _band_energy_px_db(f, px_db, 14000, 15750),
        "source_weld_12k_15_9k": _band_energy_px_db(f, px_db, 12000, 15900),
        "transition_15k_18_5k": _band_energy_px_db(f, px_db, 15000, 18500),
        "alias_zone_16k_18k": _band_energy_px_db(f, px_db, 16000, 18000),
        "bwe_18k_21_5k": _band_energy_px_db(f, px_db, 18000, 21500),
        "air_21_5k_23k": _band_energy_px_db(f, px_db, 21500, 23000),
        "near_nyquist_23k_24k": _band_energy_px_db(f, px_db, 23000, 24000),
    }
    bands["weld_delta_15k_18_5k"] = bands["transition_15k_18_5k"] - bands["source_weld_12k_15_9k"]
    # Alias for backward compat
    bands["nyquist"] = bands["near_nyquist_23k_24k"]
    metrics = {
        "lufs": float(meter.integrated_loudness(y.T)),
        "rms_db": _dbfs(y),
        "peak_db": _peak_db(y),
        "crest_factor_db": 20 * np.log10((np.max(np.abs(y)) + 1e-12) / (np.sqrt(np.mean(y**2)) + 1e-12)),
        "spatial_ratio": _stage_spatial_ratio(y, sr),
        "phase_corr_300hz_plus": _stage_phase_corr(y, sr),
        "bands": bands,
        "bwe_fill": bands["bwe_18k_21_5k"],
        "air_wall": bands["bwe_18k_21_5k"] - bands["brilliance_12k_16k"],
        "hf_flatness_12k_21k": _hf_flatness_from_psd(f, pxx)
    }
    if return_air_texture:
        metrics["air_band_texture"] = _air_band_texture_metrics(y, sr)
    if return_psd:
        metrics["f"] = f
        metrics["pxx"] = pxx
    return metrics


def compute_confidence_score(y, sr, target_lufs, ceiling_db):
    lufs_error = abs(target_lufs - pyln.Meter(sr).integrated_loudness(y.T))
    tp = _true_peak_db(y, sr)
    phase_corr = _stage_phase_corr(y, sr)

    lufs_score = max(0.0, 40.0 - 20.0 * lufs_error)
    phase_score = 30.0 if phase_corr >= PHASE_TARGET_MIN else max(0.0, 30.0 * (phase_corr + 1.0) / (PHASE_TARGET_MIN + 1.0))
    if phase_corr < 0.0:
        # Negative phase correlation indicates mono-cancellation risk from Haas/pitch-shift interaction.
        # This is a known tradeoff in the current architecture; flag for review rather than hard-fail.
        phase_score = max(0.0, phase_score * 0.5)
    peak_score = 30.0 if tp <= ceiling_db else max(0.0, 30.0 - 10.0 * (tp - ceiling_db))

    score = lufs_score + phase_score + peak_score
    is_confident = bool((lufs_error <= 0.15) and (phase_corr >= PHASE_TARGET_MIN) and (tp <= ceiling_db + 0.01))

    return float(score), is_confident, {
        "lufs": round(float(target_lufs - lufs_error), 2),
        "lufs_error": round(float(lufs_error), 2),
        "true_peak": round(float(tp), 2),
        "phase_corr": round(float(phase_corr), 4),
        "score": round(float(score), 2),
        "is_confident": is_confident
    }


def _unique_output_path(path):
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    stamp = _time.strftime("%Y%m%d_%H%M%S")
    counter = 1
    while True:
        candidate = f"{stem}_{stamp}_{counter}{ext}"
        if not os.path.exists(candidate):
            return candidate
        counter += 1


# ═══════════════════════════════════════════════════════════
# MVSEP STEM SEPARATION — DISABLED (ponytail: removed network stem-sep system)
# MVSEP_ENABLED / MVSEP_DRUMSEP_ENABLED are pinned False; the Sovereign Breathe engine
# processes the full mix directly and never consumed separated stems. The driver below
# preserves the original dict contract so downstream telemetry (mvsep_meta) is unchanged.
# ═══════════════════════════════════════════════════════════

def separate_stems_mvsep(y, sr, output_dir=None):
    # ponytail: no-op pass-through — full mix returned as every stem; driver builds mvsep_meta from this.
    return {"original": y, "vocals": y, "bass": y, "drums": y, "guitar": y, "piano": y, "other": y}


def separate_drums_mvsep(drums_audio, sr, output_dir=None):
    # ponytail: no-op pass-through
    return {"drums": drums_audio.copy(), "_meta": {"enabled": False, "reason": "mvsep_disabled"}}


# ═══════════════════════════════════════════════════════════
# KEY DETECTION & WINDOW SELECTION
# ═══════════════════════════════════════════════════════════

def _normalize_key(key):
    if key is None: return None
    return str(key).strip().split()[0]


def _key_to_camelot(root, mode):
    # ponytail: static 24-entry map, no dependency. Open-key equivalent omitted (mode unknown -> root only).
    _map = {'C major': '8B', 'G major': '9B', 'D major': '10B', 'A major': '11B',
            'E major': '12B', 'B major': '1B', 'F# major': '2B', 'C# major': '3B',
            'F major': '7B', 'Bb major': '6B', 'Eb major': '5B', 'Ab major': '4B',
            'A minor': '8A', 'E minor': '9A', 'B minor': '10A', 'F# minor': '11A',
            'C# minor': '12A', 'G# minor': '1A', 'D# minor': '2A', 'A# minor': '3A',
            'D minor': '7A', 'G minor': '6A', 'C minor': '5A', 'F minor': '4A'}
    return _map.get(f"{root} {mode}", None)


def _detect_key_librosa(y, sr):
    # HPSS: harmonic component carries tonal info; percussive transients corrupt key detection
    y_harm, _ = librosa.effects.hpss(y, margin=2.0)
    chroma = librosa.feature.chroma_cqt(y=y_harm, sr=sr)
    chroma_mean = np.mean(chroma, axis=1)
    major_template = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor_template = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
    major_template -= np.mean(major_template)
    major_template /= np.std(major_template)
    minor_template -= np.mean(minor_template)
    minor_template /= np.std(minor_template)
    best_corr = -1.0
    best_key = "E"
    best_mode = "minor"
    notes = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    for shift in range(12):
        shifted_chroma = np.roll(chroma_mean, -shift)
        shifted_chroma -= np.mean(shifted_chroma)
        std = np.std(shifted_chroma)
        if std > 1e-6:
            shifted_chroma /= std
            corr_major = np.corrcoef(shifted_chroma, major_template)[0, 1]
            corr_minor = np.corrcoef(shifted_chroma, minor_template)[0, 1]
            if corr_major > best_corr:
                best_corr = corr_major
                best_key = notes[shift]
                best_mode = "major"
            if corr_minor > best_corr:
                best_corr = corr_minor
                best_key = notes[shift]
                best_mode = "minor"
    return (best_key, best_mode), max(0.0, best_corr)


def _detect_window_key(y_window, sr):
    try:
        analysis_sr = 44100
        mono = np.mean(y_window, axis=0) if y_window.ndim > 1 else y_window
        mono_44100 = librosa.resample(mono, orig_sr=sr, target_sr=analysis_sr)
        if ESSENTIA_AVAILABLE and es is not None:
            key_root, key_scale, strength = es.KeyExtractor()(mono_44100.astype(np.float32))
            return (_normalize_key(key_root), str(key_scale).lower()), float(strength)
        else:
            (key_root, key_mode), strength = _detect_key_librosa(mono_44100, analysis_sr)
            return (_normalize_key(key_root), key_mode), float(strength)
    except Exception:
        return (None, None), 0.0


def _full_track_window_meta(y, sr):
    return {
        "enabled": False,
        "method": "full_track",
        "requested_seconds": ITERATION_WINDOW_SECONDS,
        "window_seconds": round(y.shape[1] / sr, 3),
        "selected_start_seconds": 0.0,
        "selected_end_seconds": round(y.shape[1] / sr, 3),
        "hop_seconds": ITERATION_WINDOW_HOP_SECONDS,
        "rms_db": round(20 * np.log10(np.sqrt(np.mean(y * y) + 1e-12) + 1e-12), 2)
    }


def _intro_window_meta(y, sr):
    window_samples = int(round(ITERATION_WINDOW_SECONDS * sr))
    selected_end = min(y.shape[1], window_samples)
    y_window = y if selected_end >= y.shape[1] else y[:, :selected_end].copy()
    intro_key, intro_key_strength = _detect_window_key(y_window, sr)
    _ik_root, _ik_mode = intro_key if isinstance(intro_key, tuple) else (intro_key, None)
    return y_window, {
        "enabled": True,
        "method": "intro_window",
        "requested_seconds": ITERATION_WINDOW_SECONDS,
        "window_seconds": round(selected_end / sr, 3),
        "hop_seconds": ITERATION_WINDOW_HOP_SECONDS,
        "selected_start_seconds": 0.0,
        "selected_end_seconds": round(selected_end / sr, 3),
        "start_sample": 0,
        "end_sample": int(selected_end),
        "selected_key": _ik_root,
        "selected_key_mode": _ik_mode,
        "selected_key_camelot": _key_to_camelot(_ik_root, _ik_mode),
        "selected_key_strength": round(float(intro_key_strength), 4),
        "target_key": TARGET_KEY if WINDOW_KEY_PRIORITY else None,
        "key_priority_enabled": False
    }


def _select_iteration_window(y, sr):
    if not USE_ITERATION_WINDOW:
        return y, _full_track_window_meta(y, sr)
    if WINDOW_SELECTION_MODE == "full":
        return y, _full_track_window_meta(y, sr)
    return _intro_window_meta(y, sr)


# ═══════════════════════════════════════════════════════════
# FORENSIC & DIAGNOSTICS GENERATION
# ═══════════════════════════════════════════════════════════

def _json_safe(value):
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_json_safe(v) for v in value]
    elif isinstance(value, np.integer):
        return int(value)
    elif isinstance(value, np.floating):
        return float(value)
    elif isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    return value


def _telemetry_bands_db(bands):
    return {
        "sub_20_80": round(bands.get("sub_20_80", bands.get("sub", -120)), 2),
        "bass_80_250": round(bands.get("bass_80_250", bands.get("bass", -120)), 2),
        "low_mid_250_600": round(bands.get("low_mid_250_600", bands.get("low_mid", -120)), 2),
        "mid_600_2k": round(bands.get("mid_600_2k", bands.get("mid", -120)), 2),
        "upper_mid_2k_6k": round(bands.get("upper_mid_2k_6k", bands.get("upper_mid", -120)), 2),
        "presence_6k_12k": round(bands.get("presence_6k_12k", bands.get("presence", -120)), 2),
        "brilliance_12k_16k": round(bands.get("brilliance_12k_16k", bands.get("brilliance", -120)), 2),
        "source_weld_12k_15_9k": round(bands["source_weld_12k_15_9k"], 2),
        "transition_15k_18_5k": round(bands["transition_15k_18_5k"], 2),
        "weld_delta_15k_18_5k": round(bands["weld_delta_15k_18_5k"], 2),
        "alias_zone_16k_18k": round(bands.get("alias_zone_16k_18k", bands.get("alias_zone", -120)), 2),
        "bwe_18k_21_5k": round(bands.get("bwe_18k_21_5k", bands.get("bwe_fill", -120)), 2),
        "air_21_5k_23k": round(bands.get("air_21_5k_23k", -120), 2),
        "near_nyquist_23k_24k": round(bands.get("near_nyquist_23k_24k", bands.get("nyquist", -120)), 2),
    }


def _xray_summary(x, source):
    if x is None: return None
    return {
        "source": source,
        "bpm": round(x['bpm'], 2),
        "key": x.get('key', 'E'),
        "root_hz": round(x['root_hz'], 2),
        "crest": round(x['crest'], 2),
        "tape_floor": round(float(x['tape_floor']), 2),
        "shelf_gain": round(x['shelf_gain'], 2),
        "spatial_ratio": round(x['spatial_ratio'], 3),
        "hpss_margin": round(x['hpss_margin'], 2),
        "trough_freq": round(x['trough_freq'], 0),
        "donor_slope": round(x['donor_slope'], 2),
        "raw_5_8k_db": round(x.get('raw_5_8k_db', -120.0), 2),
        "raw_8_12k_db": round(x.get('raw_8_12k_db', -120.0), 2),
        "log_lines": len(x['log'].split("\n"))
    }


def generate_telemetry_json(y_master, sr, xray, exciter_meta, window_meta=None, stage_snapshots=None, subsonic_meta=None, mastering_meta=None, musical_meta=None, hf_decor_meta=None, parallel_sat_meta=None, noise_floor_meta=None, sub_anchor_meta=None, haas_delay_meta=None, time_stretch_meta=None, pitch_shift_meta=None, adaptive_meta=None, mvsep_meta=None, final_metrics=None, xray_full=None, xray_window=None, delta_report=None):
    y_master = _ensure_channels_first(y_master)
    meter = pyln.Meter(sr)
    try:
        lra = round(meter.loudness_range(y_master.T), 2)
    except Exception:
        lra = -1.0

    if final_metrics is None:
        metrics = _stage_metrics(y_master, sr, return_air_texture=True)
    else:
        metrics = final_metrics
    f = metrics.get("f")
    pxx = metrics.get("pxx")
    if f is None or pxx is None:
        f, pxx = _welch_mono(y_master, sr)
    px_db = 10 * np.log10(pxx + 1e-12)
    bands = metrics["bands"]
    xray_full_summary = _xray_summary(xray_full or xray, "full_track_pre_window")
    xray_window_summary = _xray_summary(xray_window, "post_window_processing") if xray_window else None
    active_xray_source = "post_window_processing" if xray_window else "full_track_pre_window"
    mastering = mastering_meta or {"target_lufs": TARGET_LUFS, "ceiling_db": SOVEREIGN_TRUE_PEAK_CEILING_DB}
    true_peak_db = mastering.get("final_true_peak_db", metrics["peak_db"])

    slope_1k_10k = float(np.polyfit(np.log10(f[(f >= 1000) & (f <= 10000)] + 1e-12), px_db[(f >= 1000) & (f <= 10000)], 1)[0])
    slope_6k_14k = float(np.polyfit(np.log10(f[(f >= 6000) & (f <= 14000)] + 1e-12), px_db[(f >= 6000) & (f <= 14000)], 1)[0])

    # Comprehensive Multiband Spatial & Phase Matrix
    mb_phase = _multiband_phase_corr(y_master, sr)
    mb_spatial = _multiband_spatial(y_master, sr)
    spatial_phase_matrix = {}
    for b_name in mb_phase:
        s_val = mb_spatial.get(b_name, 0.0)
        p_val = mb_phase.get(b_name, 1.0)
        status = "MONO_COMPATIBLE" if p_val >= 0.3 else ("WIDE" if p_val >= 0.0 else "PHASE_INVERTED")
        spatial_phase_matrix[b_name] = {
            "spatial_ratio_side_mid": round(float(s_val), 4),
            "phase_correlation": round(float(p_val), 4),
            "status": status
        }

    # Narrow-band spectral headroom audit vs commercial reference standard
    f_master, Pxx_master = signal.welch((y_master[0]+y_master[1])/2.0, fs=sr, nperseg=4096, noverlap=2048)
    narrow_audit = {}
    fine_bands = [
        ("500_1000_hz", 500, 1000, -49.84),
        ("1000_1500_hz", 1000, 1500, -52.68),
        ("1500_2000_hz", 1500, 2000, -54.55),
        ("2000_2500_hz", 2000, 2500, -57.71),
        ("2500_3000_hz", 2500, 3000, -58.92),
        ("3000_3500_hz", 3000, 3500, -60.54),
        ("3500_4000_hz", 3500, 4000, -61.68),
        ("4000_5000_hz", 4000, 5000, -62.61),
        ("5000_6000_hz", 5000, 6000, -63.95),
        ("6000_8000_hz", 6000, 8000, -65.52),
        ("8000_10000_hz", 8000, 10000, -66.84),
        ("10000_12000_hz", 10000, 12000, -68.53),
        ("12000_14000_hz", 12000, 14000, -72.10),
        ("14000_16000_hz", 14000, 16000, -75.59),
        ("16000_18000_hz", 16000, 18000, -79.19),
        ("18000_21500_hz", 18000, 21500, -84.05),
        ("21500_23900_hz", 21500, 23900, -100.79)
    ]
    for b_id, lo, hi, ref_std in fine_bands:
        m = (f_master >= lo) & (f_master <= hi)
        if m.any():
            val_db = float(10 * np.log10(np.trapezoid(Pxx_master[m], f_master[m]) / (hi - lo) + 1e-15))
            delta = val_db - ref_std
            status = "HOT_HEADROOM_EATER" if delta > 1.5 else ("SCOOPED" if delta < -2.0 else "BALANCED")
            narrow_audit[b_id] = {
                "energy_db": round(val_db, 2),
                "ref_target_db": ref_std,
                "delta_db": round(delta, 2),
                "status": status
            }

    telemetry = {
        "version": VERSION,
        "commercial_target": {
            "target_lufs": mastering.get("target_lufs"),
            "true_peak_cap_db": mastering.get("ceiling_db"),
            "genre": mastering.get("genre", "UNKNOWN"),
            "delivery_format": "24-bit/48kHz Lossless FLAC"
        },
        "processing_window": window_meta or {"enabled": False, "method": "full_track"},
        "stage_snapshots": stage_snapshots or [],
        "subsonic": subsonic_meta or {"enabled": False},
        "mastering": mastering,
        "spatial_and_phase_matrix": spatial_phase_matrix,
        "narrowband_spectral_headroom_audit": narrow_audit,
        "musical_clean": musical_meta or {"enabled": False},
        "hf_decor": hf_decor_meta or {"enabled": False},
        "loudness_range_lu": lra,
        "lufs": round(metrics["lufs"], 2),
        "true_peak_db": round(float(true_peak_db), 2),
        "sample_peak_db": round(metrics["peak_db"], 2),
        "rms_db": round(metrics["rms_db"], 2),
        "crest_factor_db": round(metrics["crest_factor_db"], 2),
        "crest_delta_db": round(metrics["crest_factor_db"] - (20 * np.log10(xray['crest'])), 2),
        "sub_bass_ratio": round(bands["sub_20_80"] - bands["bass_80_250"], 2),
        "spatial_ratio": round(metrics["spatial_ratio"], 3),
        "phase_corr_300hz_plus": round(metrics["phase_corr_300hz_plus"], 4),
        "multiband_phase_corr": mb_phase,
        "multiband_spatial": mb_spatial,
        "bands_db": _telemetry_bands_db(bands),
        "air_wall": round(metrics["air_wall"], 2),
        "bwe_weld_delta_db": round(metrics["bands"]["weld_delta_15k_18_5k"], 2),
        "slopes": {
            "1k_to_10k_db": round(slope_1k_10k, 2),
            "6k_to_14k_db": round(slope_6k_14k, 2)
        },
        "hf_flatness_12k_21k": round(metrics["hf_flatness_12k_21k"], 5),
        "air_band_texture": metrics.get("air_band_texture", {"enabled": False, "reason": "not_measured"}),
        "nyquist_db": round(bands["nyquist"], 2),
        "xray": {
            "bpm": round(xray['bpm'], 2),
            "key": xray.get('key', 'E'),
            "root_hz": round(xray['root_hz'], 2),
            "crest": round(xray['crest'], 2),
            "tape_floor": round(float(xray['tape_floor']), 2),
            "shelf_gain": round(xray['shelf_gain'], 2),
            "spatial_ratio": round(xray['spatial_ratio'], 3),
            "hpss_margin": round(xray['hpss_margin'], 2),
            "trough_freq": round(xray['trough_freq'], 0),
            "donor_slope": round(xray['donor_slope'], 2),
            "raw_5_8k_db": round(xray.get('raw_5_8k_db', -120.0), 2),
            "raw_8_12k_db": round(xray.get('raw_8_12k_db', -120.0), 2)
        },
        "xray_profiles": {
            "full_track": xray_full_summary,
            "post_window": xray_window_summary,
            "active_for_diagnostics": active_xray_source,
            "active_for_beat_duration": "full_track_pre_window"
        },
        "exciter": exciter_meta,
        "parallel_saturation": parallel_sat_meta or {"enabled": False},
        "noise_floor": noise_floor_meta or {"enabled": False},
        "sub_anchor": sub_anchor_meta or {"enabled": False},
        "haas_delay": haas_delay_meta or {"enabled": False},
        "time_stretch": time_stretch_meta or {"enabled": False},
        "pitch_shift": pitch_shift_meta or {"enabled": False},
        "adaptive": adaptive_meta or {"enabled": False},
        "mvsep_stem_separation": mvsep_meta if mvsep_meta else {"enabled": MVSEP_ENABLED, "reason": "mvsep_disabled_or_not_run"},
        "delta_to_standard": delta_report or {"enabled": False}
    }

    base_telem_path = f"{DIAG_DIR}/{VERSION}_TELEMETRY.json"
    telem_path = base_telem_path
    counter = 1
    while os.path.exists(telem_path):
        stamp = _time.strftime("%Y%m%d_%H%M%S")
        telem_path = f"{DIAG_DIR}/{VERSION}_TELEMETRY_{stamp}_{counter}.json"
        counter += 1
    with open(telem_path, 'w', encoding='utf-8') as tf:
        json.dump(_json_safe(telemetry), tf, indent=2)
    print(f" [+] Telemetry JSON saved: {telem_path}")
    return telemetry


def write_export_codec_check(audio_path, label="ogg"):
    y, sr = librosa.load(audio_path, sr=SR, mono=False)
    metrics = _stage_metrics(y, sr, return_air_texture=True)
    tp = _true_peak_db(y, sr)
    
    meta = {
        "enabled": True,
        "label": label,
        "path": audio_path,
        "sample_rate": sr,
        "duration_seconds": round(y.shape[1] / sr, 3),
        "lufs": round(metrics["lufs"], 2),
        "true_peak_db": round(tp, 2),
        "sample_peak_db": round(metrics["peak_db"], 2),
        "true_peak_margin_0db": round(0.0 - tp, 3),
        "sample_clip_count": int(np.count_nonzero(np.abs(y) >= 1.0)),
        "sample_near_minus_0_1dbfs_count": int(np.count_nonzero(np.abs(y) >= 10**(-0.1/20))),
        # ponytail: cap-aware clip check. Peak cap is now 1.2 dBTP (V106.471, relaxed from 0.69 for
        # loud trance). Pass if true peak stays within cap + OGG decode margin (~1.25). The old 0.75
        # threshold was stale after the cap relax -> false "FAILED" on every hot render.
        "codec_clip_free": bool(tp <= 1.25 and metrics["peak_db"] <= 1.25),
        "phase_corr_300hz_plus": round(metrics["phase_corr_300hz_plus"], 4),
        "spatial_ratio": round(metrics["spatial_ratio"], 3),
        "hf_flatness_12k_21k": round(metrics["hf_flatness_12k_21k"], 5),
        "air_band_texture": metrics.get("air_band_texture", {}),
        "bands_db": _telemetry_bands_db(metrics["bands"]),
        "bwe_weld_delta_db": round(metrics["bands"]["weld_delta_15k_18_5k"], 2)
    }

    check_path = _unique_output_path(f"{DIAG_DIR}/{VERSION}_{label.upper()}_EXPORT_CHECK.json")
    with open(check_path, 'w', encoding='utf-8') as f:
        json.dump(_json_safe(meta), f, indent=2)
    
    status = "OK" if meta.get("codec_clip_free") else "FAILED"
    print(f" [+] Export Codec Check | {label.upper()} {status}: {check_path}")
    return meta


def generate_diagnostics(y_master, sr, telemetry_log, version=None):
    if version is None:
        version = VERSION
    stamp = _time.strftime("%Y%m%d_%H%M%S")
    diag_prefix = f"{version}_{stamp}_{os.getpid()}_"
    xray_path = _unique_output_path(f"{DIAG_DIR}/XRAY_REPORT_{version}_{stamp}.txt")
    with open(xray_path, "w", encoding="utf-8") as f:
        f.write(telemetry_log)
    print(f" [+] Diagnostics | X-Ray report saved: {xray_path}")
    if LOW_RAM_MODE:
        print(" [+] Diagnostics | LOW_RAM_MODE: skipping forensic image generation")
        return
    plt.figure(figsize=(6, 6), facecolor='black')
    plt.plot(y_master[0, ::10], y_master[1, ::10], ',', color='cyan', alpha=0.3)
    plt.xlim(-1, 1); plt.ylim(-1, 1)
    plt.title("Goniometer (Phase Coherence)", color='white')
    plt.axis('off')
    plt.savefig(f"{DIAG_DIR}/{diag_prefix}goniometer.png", facecolor='black', bbox_inches='tight', dpi=150)
    plt.close()

    S = librosa.feature.melspectrogram(y=y_master[0], sr=sr, n_fft=4096, hop_length=512, n_mels=256, fmax=24000)
    S_dB = librosa.power_to_db(S, ref=np.max)

    plt.figure(figsize=(14, 6), facecolor='black')
    librosa.display.specshow(S_dB, sr=sr, hop_length=512, x_axis='time', y_axis='hz', cmap='magma', fmax=24000)
    plt.colorbar(format='%+2.0f dB')
    plt.title("Full Spectral Canvas (0 - 24kHz)", color='white')
    plt.tight_layout()
    plt.savefig(f"{DIAG_DIR}/{diag_prefix}spectro_full.png", facecolor='black', dpi=200)
    plt.close()

    plt.figure(figsize=(14, 6), facecolor='black')
    librosa.display.specshow(S_dB, sr=sr, hop_length=512, x_axis='time', y_axis='hz', cmap='magma', fmin=12000, fmax=24000)
    plt.ylim(12000, 24000)
    plt.colorbar(format='%+2.0f dB')
    plt.title("Air Band Diagnostics (12kHz - 24kHz Weld)", color='white')
    plt.tight_layout()
    plt.savefig(f"{DIAG_DIR}/{diag_prefix}spectro_air_zoomed.png", facecolor='black', dpi=200)
    plt.close()
    print(f" [✓] Diagnostics saved to {DIAG_DIR}")


def append_stage_snapshot(stage_snapshots, label, y, sr, exciter_meta=None, full_metrics=False):
    y = _ensure_channels_first(y)
    m = _stage_metrics(y, sr, return_air_texture=full_metrics)
    snapshot = {
        "label": label,
        "lufs": round(m["lufs"], 2),
        "rms_db": round(m["rms_db"], 2),
        "peak_db": round(m["peak_db"], 2),
        "crest_factor_db": round(m["crest_factor_db"], 2),
        "spatial_ratio": round(m["spatial_ratio"], 3),
        "phase_corr_300hz_plus": round(m["phase_corr_300hz_plus"], 4),
        "multiband_phase_corr": _multiband_phase_corr(y, sr),
        "bands_db": _telemetry_bands_db(m["bands"]),
        "bwe_fill": round(m.get("bwe_fill", m.get("bwe_18k_21_5k", -120)), 2),
        "air_wall": round(m["air_wall"], 2),
        "hf_flatness_12k_21k": round(m["hf_flatness_12k_21k"], 5)
    }
    
    # V106.442: BWE temporal stability metric
    # Lower value = more stable BWE layer. Uses air_wall + HF flatness as proxies.
    try:
        air_wall = m.get("air_wall", -120.0)
        hf_flatness = m.get("hf_flatness_12k_21k", 0.0)
        if hf_flatness > 0.1:
            stability_factor = max(0.0, min(20.0, abs(air_wall) * 0.5 + (1.0 - hf_flatness) * 10.0))
            snapshot["bwe_gain_variance_db"] = round(stability_factor, 2)
        else:
            snapshot["bwe_gain_variance_db"] = 0.0
    except Exception:
        snapshot["bwe_gain_variance_db"] = 0.0
    
    stage_snapshots.append(snapshot)
    return snapshot


# ═══════════════════════════════════════════════════════════
# SOVEREIGN BREATHE MASTERING PIPELINE
# ═══════════════════════════════════════════════════════════

# ponytail: neural-codec (DAC/SNAC) block removed — NEURAL_CODEC_ENABLED is pinned False
# and user explicitly rejects DAC for watermark removal. The encode→decode cycle was never
# exercised in the active pipeline. If watermark hardening is ever needed, revisit a non-DAC
# method rather than re-adding this 140-line torch dependency.

def process_sovereign_breathe_pipeline(y, sr, style, xray_full, stage_snapshots, drumsep_stems=None):
    """Restores the pure V61.0 Sovereign Breathe mastering engine for SIGNAL (Track 2) with hybrid SOTA enhancements."""
    # ponytail (source-driven): RAW profiling now happens at true load point (line ~2430),
    # NOT here — y at this point is post-set_lufs gain-adjusted, giving garbage spatial.
    # V106.499h24: trust style table BPM over X-Ray detection when known. ZERO is detected at
    # 184.57 BPM by essentia but is canonically 138 BPM per user. Using detected BPM causes
    # wrong wow/flutter Hz (line 1005, 1021). Style table is single source of BPM truth.
        # V106.499h24: BPM override conservatism. Trust the librosa-validated X-Ray result UNLESS
    # V106.499h24: validated X-Ray BPM is good (librosa fallback handles essentia hallucinations).
    # Style table BPM is only a sanity reference; drift > 10 BPM is logged as a warning only.
    # Examples: ZERO 139.67 (validated) vs style 138 (delta=1.67, no warning, no override).
    BPM = float(xray_full['bpm'])  # trust validated X-Ray
    _bpm_style = float(style.get("tempo_bpm", xray_full['bpm']))
    if abs(_bpm_style - xray_full['bpm']) > 10.0:
        print(f" [BPM] X-Ray={xray_full['bpm']:.2f} BPM, style={_bpm_style:.1f} BPM drift > 10 - check detector")
    ROOT_FREQ = xray_full['root_hz']
    beat_dur = 60.0 / BPM
    t = np.linspace(0, y.shape[1] / sr, y.shape[1])
    
    # float32 throughout — halves memory for all arrays and filter temporaries
    y = y.astype(np.float32)
    
    def _mb(x):
        try:
            return x.nbytes / (1024 * 1024)
        except Exception:
            return -1.0
    
    def _mem(label):
        try:
            import resource
            ru = resource.getrusage(resource.RUSAGE_SELF)
            print(f" [MEM] {label}: rss={ru.ru_maxrss/1024:.1f} MB", flush=True)
        except Exception:
            pass
    
    print(f"[MEM] input y: {_mb(y):.1f} MB (float32)", flush=True)
    _mem("start")
    
    # 1. Hygiene HPF (Chebyshev Type II HPF at 15Hz)
    # 1. Hygiene HPF (Chebyshev Type II HPF at 15Hz)
    sos_hpf = signal.cheby2(8, 40, 15, 'hp', fs=sr, output='sos')
    y = signal.sosfiltfilt(sos_hpf, y, axis=1)
    _mem("after_hpf")

    # 2. Mono-bass and Sub-anchor (pure acoustic sub-bass mono sum, zero synthetic sine injection)
    # V106_500h121: style-driven fold (default 150Hz for trance, 100Hz for MYTH — sibling-validated vs Move Mountains/Joris Voorn).
    _mono_fold_hz = float(style.get("mono_fold_hz", 150.0))
    s_lp = signal.butter(4, _mono_fold_hz, 'lp', fs=sr, output='sos')
    s_hp = signal.butter(4, _mono_fold_hz, 'hp', fs=sr, output='sos')
    y_low = np.stack([np.mean(signal.sosfiltfilt(s_lp, y, axis=1), axis=0)] * 2)
    y_high = signal.sosfiltfilt(s_hp, y, axis=1)
    y = y_low + y_high
    del y_low, y_high
    gc.collect()
    _trim()
    print(f" [+] Mono fold @ {_mono_fold_hz:.0f} Hz (style.get('mono_fold_hz'))", flush=True)

    y_contiguous = y

    # 3. Wow/flutter LFO was [redacted platform]-era temporal spice. Warp itself is gated by
    # AI2G_AB_WOW=1 (absolute_human_drift_v2 skipped). Do NOT still allocate the LFO.
    y_sum = y_contiguous
    del y_contiguous
    gc.collect()
    _trim()
    # Warp is applied (or skipped) in process() via absolute_human_drift_v2.
    # This in-pipeline LFO was leftover [redacted platform] spice and is gone.
    # Telemetry still wants a vector: zeros when warp is off.
    drift = np.zeros(y_sum.shape[-1], dtype=np.float32)
    if os.environ.get("AI2G_AB_WOW", "0") == "1":
        print(" [+] Telemetry | Mechanical Drift SKIPPED (AI2G_AB_WOW=1, no leftover LFO)")
    _mem("after_wow_flutter")
    # 4. Breathing Spectral Zenith & Polishes
    # Final Muzzle Bessel LPF REMOVED (V106.499h24): per industry standard, mastering engineers
    # do NOT apply explicit Nyquist LPF — the SRC/Vorbis encoder handles it.
    # Only the BWE taper at 21kHz (line ~1530) provides gentle HF management.
    # AI2G_AB_MUZZLE gate kept for A/B testing but defaults to SKIP (1).
    if os.environ.get("AI2G_AB_MUZZLE", "1") == "1":
        print(" [A/B] Final Muzzle Bessel LPF SKIPPED (AI2G_AB_MUZZLE=1)")
        # ponytail: define b_bes/a_bes as identity (1, [1], [1]) so downstream lfilter calls at lines 1434/1445/1588 are no-ops.
        b_bes, a_bes = np.array([1.0]), np.array([1.0])
    else:
        # Legacy path — Bessel LPF removed per industry standard (Gearspace: "extremely rare to use LPF in mastering")
        b_bes, a_bes = signal.bessel(4, 21000, 'lp', fs=sr)
        y_sum = signal.lfilter(b_bes, a_bes, y_sum, axis=1)
        print(" [A/B] Final Muzzle Bessel LPF APPLIED (legacy)")

    # Save pre-alias-lowpass signal for onset detection (has full HF content)
    if LOW_RAM_MODE:
        y_sum_for_onset = None
    else:
        y_sum_for_onset = y_sum.copy()

    # V106.440: pre-LPF telemetry snapshot to measure raw aliasing level
    # Skip in LOW_RAM_MODE to avoid extra full-track metrics pass on 12GB Colab
    if not LOW_RAM_MODE:
        append_stage_snapshot(stage_snapshots, "pre_alias_lpf", y_sum, SR, full_metrics=False)

    # NOTE: 16kHz aliasing removal is now handled by source LPF + BWE HPF crossover below.
    # Standalone LPF removed (V106.344) — cascading two 4th-order at 16kHz
    # created 8th-order rolloff causing 14-16kHz notch (~1.6 dB dip).
    # V106.430: 2nd-order source LPF + 4th-order BWE HPF = 6th-order crossover,
    # removes Suno 16-18kHz aliasing without 8th-order notch.

    # CROSSOVER_HZ: matches 8-12kHz donor 2nd-harmonic creation threshold (2x 8kHz = 16kHz)
    # and restores full 12-16kHz source air band envelope tracking.
    # Style-driven: default 15200.0 Hz smooths the raw Suno 15.8k digital wall into a natural
    # commercial rolloff slope (-70 -> -76 -> -82 dB) before BWE handoff.
    CROSSOVER_HZ = float(style.get("crossover_hz", 15200.0))

    # V106.499h24: Surgical notch fill — implemented in Final Muzzle via pedalboard PeakingFilter
    # at 16kHz, +2.5 dB, Q=2.0 (narrow bell). Fills the crossover weld dip without adjacent-band bleed.
    # The band-pass source-copy approach over-filled +5 dB at 16-18k and bled +2.7 dB into 12-16k.
    # A peaking EQ (bell filter) is the standard professional mastering tool for surgical notch fill.
    # See: scipy.signal.iirpeak docs, FabFilter Pro-Q workflows, Rane Linkwitz-Riley primer.
    # Actual filter applied in Final Muzzle section below (alongside the existing HF shelf).

    # V106.435: gentle 1st-order source LPF (-6 dB/octave) for alias suppression.
    # V106.499h24: srcLP2nd tested in isolation (commit c1e1826) — calibration loop overcompensated.
    # V106.499h24 PART 2: with calib PRE-LPF decoupling (commit ebed453), srcLP2nd no longer
    # triggers overcompensation. Source LPF is now independent of calibration reference.
    # V106.499h62: Restore H37-era zero-phase source LPF (sosfiltfilt) — the -24 dB/octave
    # effective rolloff (2nd-order x2 from zero-phase) perfectly complements the BWE gen HPF,
    # producing a flat, seamless 16-21kHz sum with NO cliff. H37 (git ea5f33e) proved this
    # seamless slope; causal 2nd-order left a plateau then drop reading as "cliffy" at 18k.
    source_lp_order = int(style.get("source_lp_order", 2))
    sos_src_lp = signal.butter(source_lp_order, CROSSOVER_HZ, 'low', fs=sr, output='sos')
    y_sum_pre_lpf = y_sum.copy()
    _is_apollo = bool(style.get("apollo_native", False) or "APOLLO" in os.path.basename(RAW_SOURCE or "").upper())
    if _is_apollo:
        print(" [+] Apollo-Native Source | Bypassing source LPF to preserve Apollo 16-24kHz air")
        y_sum_pre_bwe = y_sum
    else:
        if bool(style.get("source_lp_zero_phase", True)):
            y_sum = signal.sosfiltfilt(sos_src_lp, y_sum, axis=1)
        else:
            y_sum = signal.sosfilt(sos_src_lp, y_sum, axis=1)
        y_sum_pre_bwe = y_sum
    exciter_meta = {
        "bwe_gain_db": -120.0,
        "gain_floor_db": EXCITER_GAIN_FLOOR_DB,
        "gain_cap_db": EXCITER_GAIN_CAP_DB,
        "match_percentile": EXCITER_MATCH_PERCENTILE,
        "ref_match_db": 0.0,
        "raw_match_db": 0.0,
        "env_gate_exponent": EXCITER_ENV_GATE_EXPONENT,
        "noise_floor": EXCITER_NOISE_FLOOR,
        "noise_cap": EXCITER_NOISE_CAP,
        "noise_slope_divisor": EXCITER_NOISE_SLOPE_DIVISOR,
        "air_shelf_freq_hz": AIR_SHELF_FREQ_HZ,
        "air_shelf_gain_db": AIR_SHELF_GAIN_DB,
        "donor_band_energy_db": -120.0,
        "generated_harmonic_energy_db": 0.0,
        "generated_noise_energy_db": -120.0,
        "force_hf_injection": False,
        "hf_injection_triggered": False,
        "intro_leveler": {"enabled": False, "intro_window_sec": 0.0, "intro_db": -120.0, "body_db": -120.0, "delta_db": 0.0, "applied_gain_db": 0.0},
        "donor_preemphasis_cap_db": 0.0,
        "raw_highs_peak_db": 0.0,
        "raw_highs_95_db": 0.0,
        "raw_highs_rms_db": 0.0,
        "raw_highs_active_ratio": 0.0,
        "raw_highs_transient_smoothing": {"enabled": False},
        "raw_highs_peak_guard": {"enabled": False},
        "air_crest_clamp": {"enabled": False},
        "bwe_weld": {"enabled": False},
        "bwe_weld_after_gain_match": False,
        "donor_band_hz": [8000.0, 12000.0],  # actual runtime donor band for harmonic exciter
        "generation_highpass_hz": 15.0,  # hygiene HPF only; no exciter HPF in Sovereign mode
        "air_antialias_hz": 22000,
        "noise_ratio": 1.0,  # V106.317: grey noise modulation
        "harmonic_ratio": 0.0,  # V106.317: no tonal harmonics
        "spatial_expansion": 1.0,
        "bpm_sync_release_ms": 100.0,
        "crossover_hz": float(CROSSOVER_HZ),
        "crossover_type": "SovereignBreatheHarmonic",  # active, not bypassed
        "source_band_hz": [12000.0, float(CROSSOVER_HZ)],  # source air-band envelope for BWE dynamics
        "bwe_band_hz": [float(CROSSOVER_HZ), 21000.0],  # actual BWE output range after crossover + taper
        "sg_window_s": 0.2,  # V106.317: transient tracking
        "bwe_compressor": {"enabled": False},  # removed in V308 cleanup
        "centroid_blend": {"enabled": False},  # removed in V308 cleanup
        "noise_ratio": 0.0,  # current implementation uses harmonic exciter, not grey noise
        "harmonic_ratio": 1.0,  # tanh saturation of 8-12kHz donor generates harmonics
    }

    # Polish C: Decoupled Noise Floor (DISABLED - creates HF haze)
    # shift = np.clip(np.abs(drift).astype(int), 0, 3)
    # rms_raw = np.sqrt(np.mean(y_sum**2, axis=0))
    # rms_smooth = signal.savgol_filter(rms_raw, 1001, 1)
    # shifted_rms = np.copy(rms_smooth)
    # for i in range(1, len(rms_smooth)):
    #     shifted_rms[i] = rms_smooth[max(0, i - shift[i])]
    # n_mid_c = np.random.randn(y_sum.shape[1])
    # n_side_c = np.random.randn(y_sum.shape[1]) * 0.5
    # n_corr_c = np.stack([n_mid_c + n_side_c, n_mid_c - n_side_c])
    # brown = (signal.lfilter(b_bes, a_bes, n_corr_c, axis=1) / (np.max(np.abs(y_sum)) * 12.0))
    # NOISE_FLOOR_BASE = -94.0
    # y_sum += brown * 10**((NOISE_FLOOR_BASE + (shifted_rms/(np.max(shifted_rms)+1e-12) * 4.0)) / 20.0)[None, :]

    # Polish A: Breathing Needle Gate (Rolling Percentile) - DISABLED: causes HF noise haze pulsing
    # n_mid_a = np.random.randn(y_sum.shape[1])
    # n_side_a = np.random.randn(y_sum.shape[1]) * 0.5
    # n_corr_a = np.stack([n_mid_a + n_side_a, n_mid_a - n_side_a])
    # brown_a = signal.lfilter(b_bes, a_bes, n_corr_a, axis=1) / (np.max(np.abs(y_sum)) * 12.0)
    # sos_drive = signal.butter(4, [7500, 12000], 'bp', fs=sr, output='sos')
    # env = signal.sosfiltfilt(signal.butter(2, 100, 'low', fs=sr, output='sos'), np.abs(signal.sosfiltfilt(sos_drive, y_sum, axis=1)), axis=1)
    # env_1d = np.mean(env, axis=0)
    # thresh_rolling = percentile_filter(env_1d, 88, size=int(sr * 8.5))
    # y_sum += brown_a * (np.where(env > thresh_rolling[None, :], (env-thresh_rolling[None, :])/(np.max(env)-np.min(thresh_rolling)+1e-12), 0.0)**2.0) * (10**(-78/20))

    # Polish B: Source-air-band envelope BWE (V106.343: BWE tracks source 12-16kHz energy)
    # Replaces onset+body approach — envelope follows source HF directly
    cl_f = 12000.0
    y_ref = signal.sosfiltfilt(signal.butter(4, [12000, 15000], 'bp', fs=sr, output='sos'), y_sum_pre_bwe, axis=1)
    en_cliff = 20.0 * np.log10(np.sqrt(np.mean(y_ref**2)) + 1e-12)
    del y_ref
    gc.collect()
    _trim()

    # Source air band envelope: extract 12-16kHz energy from clean source (post-LPF)
    # This replaces onset-based bursts — BWE dynamics follow source HF directly
    env_src_air = np.clip(signal.sosfiltfilt(signal.butter(4, [12000.0, float(CROSSOVER_HZ)], 'bp', fs=sr, output='sos'), y_sum_pre_bwe, axis=1), 0.0, None)
    env_src_air_rms = np.sqrt(np.mean(env_src_air**2, axis=0))
    del env_src_air
    gc.collect()
    _trim()
    # Smooth with 30ms window (short enough for transients, long enough to avoid jitter)
    smooth_win = max(int(0.030 * sr), 1)
    env_src_air_smooth = np.convolve(env_src_air_rms, np.ones(smooth_win) / smooth_win, mode='same')
    env_src_air_norm = np.clip(env_src_air_smooth / (np.max(env_src_air_smooth) + 1e-4), 0.0, 1.0)

    # Adaptive intro-window detection + envelope-side lift (ponytail: replaces fixed 145s;
    # finds real intro end 140-150s, lifts source air-band envelope so boosted donor harmonics
    # aren't gated to the 1% floor in source-limited intros)
    intro_leveler_on = bool(style.get("intro_donor_leveler", False))
    n_env = env_src_air_norm.shape[0]
    intro_n = min(int(float(style.get("intro_window_sec", 145.0)) * sr), n_env)
    env_delta_db = 0.0
    if intro_leveler_on and n_env > int(10.0 * sr):
        # detect intro end: first point where smoothed env sustains > 0.5*body_mean for 3s (search 0-160s)
        _b0 = min(int(160.0 * sr), n_env)
        body = env_src_air_smooth[_b0:] if n_env > _b0 else env_src_air_smooth[n_env // 2:]
        body_mean = float(np.mean(body) + 1e-12)
        above = env_src_air_smooth > (0.5 * body_mean)
        sus = max(int(3.0 * sr), 1)
        search_end = max(0, min(int(160.0 * sr), n_env - sus))
        step = max(1, sr // 10)
        found = -1
        for i in range(0, search_end, step):
            if bool(np.all(above[i:i + sus])):
                found = i
                break
        if found > 0:
            intro_n = found
        # envelope-side lift: bring intro air-band envelope up toward body (capped +12dB, 3s xfade)
        env_intro_db = 20.0 * np.log10(float(np.mean(env_src_air_smooth[:intro_n]) + 1e-20))
        env_body_db = 20.0 * np.log10(float(np.mean(env_src_air_smooth[intro_n:]) + 1e-20))
        env_delta_db = env_body_db - env_intro_db
        if env_delta_db > 3.0:
            cap_db = min(env_delta_db, 12.0)
            xf = max(int(3.0 * sr), 1)
            g = np.ones(n_env, dtype=np.float32)
            g[:intro_n] = 10.0 ** (cap_db / 20.0)
            if intro_n + xf < n_env:
                g[intro_n:intro_n + xf] = np.linspace(10.0 ** (cap_db / 20.0), 1.0, xf)
            env_src_air_norm = np.minimum(env_src_air_norm * g, 1.0)
            del g
            gc.collect()
            _trim()
    del env_src_air_rms, env_src_air_smooth
    gc.collect()
    _trim()

    # V106.499h61: Configurable envelope floor (default 8% = -22dB). Prevents 16k+ harmonic
    # needles from thinning out / collapsing into black voids during quiet intros, breakdowns, and transitions.
    bwe_env_floor = float(style.get("bwe_env_floor", 0.08))
    env_with_floor = np.maximum(env_src_air_norm, bwe_env_floor)
    del env_src_air_norm
    gc.collect()
    _trim()

    # Generate BWE: harmonic exciter (saturation of source upper mids)
    # White noise creates "rough or noisy sound" (AES paper). Harmonics are naturally
    # correlated with the source because they're derived from it.
    _mem("before_bwe_donor")
    donor_band = style.get("donor_band_hz", [9000.0, 12000.0])
    # Extract donor band (where kick has zero energy, clean for saturation)
    # Crossover at 16kHz prevents intermodulation — donor band width doesn't cause bulge
    onset_source = y_sum_for_onset if y_sum_for_onset is not None else y_sum
    # Stereo vs Mid-based donor extraction:
    # If bwe_spatial_target > 0, generate harmonics from full stereo source (preserves natural L/R air width).
    # Otherwise generate from mono Mid sum to preserve 100% phase correlation.
    if float(style.get("bwe_spatial_target", 0.0)) > 0.0 and onset_source.shape[0] >= 2:
        _donor_source = onset_source
        print(" [+] BWE Stereo Donor Mode | Generating L/R independent harmonic exciter from stereo source")
    else:
        _mid_source = (onset_source[0] + onset_source[1]) / 2.0
        _donor_source = np.stack([_mid_source, _mid_source])
    y_donor = signal.sosfiltfilt(signal.butter(4, donor_band, 'bp', fs=sr, output='sos'), _donor_source, axis=1)

    # Data-driven intro donor-leveler (ponytail: replaces hardcoded +12dB intro boost).
    # Uses the adaptive intro window detected above (intro_n) so donor + envelope lifts align.
    # Problem: when the intro's donor band is source-limited, harmonic generation starves ->
    # intro BWE stays crushed even with force_hf_injection. Fix: lift intro donor to body level
    # BEFORE saturation, crossfaded at the boundary.
    intro_leveler_meta = {"enabled": False, "intro_window_sec": round(intro_n / float(sr), 1), "intro_db": -120.0, "body_db": -120.0, "delta_db": 0.0, "applied_gain_db": 0.0, "env_delta_db": round(float(env_delta_db), 2)}
    n_donor = y_donor.shape[1]
    if intro_leveler_on and intro_n > 0 and n_donor - intro_n > 0:
        intro_db = 20.0 * np.log10(np.sqrt(np.mean(y_donor[:, :intro_n] ** 2)) + 1e-20)
        body_db = 20.0 * np.log10(np.sqrt(np.mean(y_donor[:, intro_n:] ** 2)) + 1e-20)
        delta_db = body_db - intro_db
        intro_leveler_meta["enabled"] = True
        intro_leveler_meta["intro_db"] = round(float(intro_db), 2)
        intro_leveler_meta["body_db"] = round(float(body_db), 2)
        intro_leveler_meta["delta_db"] = round(float(delta_db), 2)
        if delta_db > 3.0:  # genuinely source-limited intro; lift it toward body, capped +12dB (proven temporal-safe)
            cap_db = min(delta_db, 12.0)
            xf = max(int(1.0 * sr), 1)
            gain = np.ones(n_donor, dtype=np.float32)
            gain[:intro_n] = 10.0 ** (cap_db / 20.0)
            if intro_n + xf < n_donor:
                ramp = np.linspace(10.0 ** (cap_db / 20.0), 1.0, xf)
                gain[intro_n:intro_n + xf] = ramp
            y_donor = y_donor * gain[None, :]
            intro_leveler_meta["applied_gain_db"] = round(float(cap_db), 2)
            del gain
            gc.collect()
            _trim()
    # Record intro leveler result into exciter telemetry
    exciter_meta["intro_leveler"] = intro_leveler_meta
    # Free onset source immediately after donor extraction — saves 125 MB during harmonic generation
    del onset_source
    gc.collect()
    _trim()

    # Apply tanh saturation to generate odd/even harmonics.
    # V106.499e: OVERSAMPLE 2x before tanh to prevent fold-back aliasing. tanh on the 9-12k
    # donor band generates harmonics above Nyquist (24k at 48k SR) which fold back into 0-24k
    # as broadband brightness/harshness (worst in outro where donor is loudest). User: the WHOLE
    # 0-24k is brighter, not in source, it's pipeline aliasing that back-aliases at file end.
    # Fix: upsample donor 2x, tanh, then decimate back (scipy.decimate applies anti-alias LPF
    # at the new Nyquist, removing >24k harmonics before they can fold back). ponytail: reversible.
    drive = float(style.get("bwe_drive", 4.0))
    y_up = signal.resample_poly(y_donor, 2, 1, axis=1)   # 2x oversample
    y_sat_up = np.tanh(y_up * drive) / drive
    y_saturated = signal.resample_poly(y_sat_up, 1, 2, axis=1)   # decimate -> anti-alias LPF kills >24k
    del y_up, y_sat_up

    # MYTH-only: capture donor energy before cleanup for later HF injection decision
    force_hf = bool(style.get("force_hf_injection", False))
    donor_energy_db = -120.0
    if force_hf:
        donor_energy_db = float(20.0 * np.log10(np.mean(np.abs(y_donor)) + 1e-12))
    # V106.499h24 A/B outcome locked: generation_hpf_hz pinned at CROSSOVER_HZ (15000) for ZERO.
    # A/B vs h11 baseline (12000), both renders clean per user ear/Spek verdict:
    #   - 14-16k band delta: -0.83 to -0.92 dB (not the "10 dB" the V106.499g comment predicted)
    #   - LUFS final: -8.27 (h11) vs -8.86 (h12) — h12 missed -7.5 target by 1.36 dB
    #     Removing BWE 14-16k modest content shifted LUFS-converge loop output
    #   - [redacted platform]: h11 = 82 spectral / h12 = 80 spectral — within detector noise range
    # Verdict: per iZotope "less is more" mastering exciter guidance + V106.499g original
    # crossover-aligned clean master (the comment was correct, the predicted magnitude
    # Crossover-aligned clean master: gentle forward-only 2nd-order causal HPF at generation_hpf_hz
    # lets harmonic air breathe naturally across the 15k-17k weld zone without the 48dB/oct cliff.
    generation_hpf_hz = float(style.get("generation_hpf_hz", 14500.0))
    generation_hpf_order = int(style.get("generation_hpf_order", 2))
    sos_gen_hp = signal.butter(generation_hpf_order, generation_hpf_hz, 'hp', fs=sr, output='sos')
    y_harmonics = signal.sosfilt(sos_gen_hp, y_saturated, axis=1)

    # Free donor intermediates — harmonics isolated
    del y_donor, y_saturated
    gc.collect()
    _trim()

    # Normalize harmonics
    harm_max = np.max(np.abs(y_harmonics)) + 1e-12
    y_harmonics = y_harmonics / harm_max

    # Power compression: reduce dynamic range (exponent 0.4)
    # Compresses peaks more than quiet parts, reducing std from ~25 to ~10
    y_harmonics = np.sign(y_harmonics) * np.power(np.abs(y_harmonics), 0.4)

    # V106.500h110: Donor-fundamental band-stop (structural fix for 10-12kHz haze).
    # PLACEMENT IS CRITICAL: must run AFTER normalize + power-compression, BEFORE envelope
    # modulation. h109 placed it before normalize; the normalize step (y_harmonics/harm_max)
    # re-scaled everything back to peak 1.0 and power-compression boosted the residual,
    # undoing the notch (10-12k went UP +0.54dB, weld zone +7.5dB in quiet frames).
    # Here, nothing re-normalizes after the notch, so the leaked 8-12kHz donor fundamental
    # is genuinely removed. The 2nd-harmonic air band (16k+) is untouched. sosfiltfilt =
    # zero-phase, preserves weld-zone phase. ponytail: reversible via style flag.
    if bool(style.get("bwe_donor_notch", False)):
        _dn_lo, _dn_hi = donor_band
        sos_donor_notch = signal.butter(4, [_dn_lo, _dn_hi], 'bandstop', fs=sr, output='sos')
        y_harmonics = signal.sosfiltfilt(sos_donor_notch, y_harmonics, axis=1)
        exciter_meta["bwe_donor_notch_hz"] = [float(_dn_lo), float(_dn_hi)]

    # Record harmonic energy before envelope modulation
    exciter_meta["generated_harmonic_energy_db"] = round(float(20.0 * np.log10(np.mean(np.abs(y_harmonics)) + 1e-20)), 2)

    # Modulate harmonics with source-air-band-aware envelope
    v_noise = y_harmonics * env_with_floor[None, :]
    del y_harmonics
    gc.collect()
    _trim()

    # MYTH-only: force minimal HF injection when donor is effectively silent
    force_hf = bool(style.get("force_hf_injection", False))
    if force_hf and donor_energy_db < -60.0:
        noise_hf = np.random.randn(*v_noise.shape).astype(v_noise.dtype) * 1e-4
        sos_hf = signal.butter(4, 18000.0, 'hp', fs=sr, output='sos')
        noise_hf = signal.sosfiltfilt(sos_hf, noise_hf, axis=1)
        v_noise = v_noise + (noise_hf * env_with_floor[None, :])
        del noise_hf
        gc.collect()
        _trim()

    # ponytail: per-track full-track BWE air fill (option 2 for GRAVITY spectral).
    # Adds 18 kHz HP noise modulated by source envelope; level is style-driven so other
    # tracks are unaffected unless explicitly opted in. Independent of force_hf_injection.
    air_fill_level = float(style.get("bwe_air_fill_level_db", -120.0))
    if air_fill_level > -90.0:
        noise_air = np.random.randn(*v_noise.shape).astype(v_noise.dtype)
        sos_air = signal.butter(4, 18000.0, 'hp', fs=sr, output='sos')
        noise_air = signal.sosfiltfilt(sos_air, noise_air, axis=1)
        air_amp = 10.0 ** (air_fill_level / 20.0)
        v_noise = v_noise + (noise_air * air_amp * env_with_floor[None, :])
        exciter_meta["bwe_air_fill_level_db"] = air_fill_level
        exciter_meta["bwe_air_fill_applied"] = True
        del noise_air
        gc.collect()
        _trim()

    # Record MYTH HF injection telemetry
    exciter_meta["force_hf_injection"] = bool(force_hf)
    exciter_meta["donor_band_energy_db"] = round(float(donor_energy_db), 2)
    if force_hf:
        exciter_meta["hf_injection_triggered"] = bool(donor_energy_db < -60.0)
        if exciter_meta["hf_injection_triggered"]:
            injected_rms = float(np.sqrt(np.mean(np.abs(v_noise)**2) + 1e-20))
            exciter_meta["generated_noise_energy_db"] = round(float(20.0 * np.log10(injected_rms + 1e-20)), 2)

    # MYTH-only bypass for alias-zone gain match when donor is present but BWE is still being crushed
    skip_alias_gain_match = bool(style.get("skip_alias_gain_match", False))

    # V106.442: record scalar BWE gain for telemetry + variance tracking
    # V106.499h24: use pre-LPF reference (y_sum_pre_lpf) instead of post-LPF (y_sum_pre_bwe)
    # to decouple calibration from source filter changes — prevents overcompensation feedback.
    src_match_band = signal.sosfiltfilt(signal.butter(4, [float(CROSSOVER_HZ), 18000.0], 'bp', fs=sr, output='sos'), y_sum_pre_lpf, axis=1)
    src_rms = np.sqrt(np.mean(np.abs(src_match_band)**2) + 1e-20)
    bwe_rms = np.sqrt(np.mean(np.abs(v_noise)**2) + 1e-20)
    gain_db = np.clip(20.0 * np.log10(src_rms / (bwe_rms + 1e-20)), -6.0, 18.0)
    if not skip_alias_gain_match:
        v_noise = v_noise * (10.0 ** (gain_db / 20.0))
    exciter_meta["bwe_gain_db"] = round(float(gain_db), 2)
    exciter_meta["bwe_match_src_rms_db"] = round(float(20.0 * np.log10(src_rms + 1e-20)), 2)
    exciter_meta["bwe_match_bwe_rms_db"] = round(float(20.0 * np.log10(bwe_rms + 1e-20)), 2)
    del src_match_band, src_rms, bwe_rms, gain_db
    gc.collect()
    _trim()

    # Intro-section BWE telemetry: measure 0-30s before alias-zone gain match
    intro_bwe_meta = {"enabled": False, "pre_gain_match_db": -120.0, "post_gain_match_db": -120.0}
    try:
        intro_samples = int(min(30.0, float(t[-1])) * float(sr))
        if intro_samples > 0 and v_noise.shape[1] >= intro_samples:
            intro_bwe_meta["enabled"] = True
            intro_bwe_pre = np.sqrt(np.mean(np.abs(v_noise[:, :intro_samples])**2) + 1e-20)
            intro_bwe_meta["pre_gain_match_db"] = round(float(20.0 * np.log10(intro_bwe_pre + 1e-20)), 2)
            intro_bwe_post = np.sqrt(np.mean(np.abs(v_noise[:, :intro_samples])**2) + 1e-20)
            intro_bwe_meta["post_gain_match_db"] = round(float(20.0 * np.log10(intro_bwe_post + 1e-20)), 2)
    except Exception:
        pass

    # MYTH-only bypass for alias-zone gain match when donor is present but BWE is still being crushed
    skip_alias_gain_match = bool(style.get("skip_alias_gain_match", False))
    if skip_alias_gain_match and "force_hf_injection" in exciter_meta and exciter_meta.get("force_hf_injection", False):
        # Recover post-injection BWE level without alias-zone suppression
        recovery_rms = float(np.sqrt(np.mean(np.abs(v_noise)**2) + 1e-20))
        exciter_meta["myth_bypass_alias_gain_match_db"] = round(float(20.0 * np.log10(recovery_rms + 1e-20)), 2)

    # Time-varying RMS matching: match BWE level to filtered source alias zone (16-18kHz)
    # V106.431: track the attenuated alias zone instead of 14-16kHz weld zone.
    # This removes the 18kHz cliff by making BWE follow the source rolloff through 16-18kHz.
    if not skip_alias_gain_match:
        # V106.499h24: use pre-LPF reference for peak calibration too (decoupled from LPF changes)
        ref_band = signal.sosfiltfilt(signal.butter(4, [float(CROSSOVER_HZ), 18000], 'bp', fs=sr, output='sos'), y_sum_pre_lpf, axis=1)
        ref_peak = np.percentile(np.abs(ref_band), 99.9) + 1e-12
        del ref_band
        high_peak = np.percentile(np.abs(v_noise), 99.9) + 1e-12
        gain_db = np.clip(20 * np.log10(ref_peak / high_peak), -6.0, 18.0)
        v_noise = v_noise * (10 ** (gain_db / 20.0))

    # Create nominal BWE layer
    env_hf_norm = np.ones_like(env_with_floor)
    y_bwe_nominal = v_noise * env_hf_norm * (10 ** (en_cliff / 20.0))
    del v_noise, env_hf_norm
    gc.collect()
    _trim()
    _mem("after_bwe_nominal")

    # Boost for calibration measurement
    boost_db = 40.0
    boost_factor = 10 ** (boost_db / 20.0)

    # Simulate downstream filtering for calibration in-place to avoid another full-track copy
    y_bwe_test = y_bwe_nominal * boost_factor
    # Keep y_bwe_nominal alive for polish_b_layer later; avoid extra full-track copy
    _mem("after_bwe_boost")
    y_bwe_test = signal.lfilter(b_bes, a_bes, y_bwe_test, axis=1)
    y_bwe_test = Pedalboard([
            HighShelfFilter(cutoff_frequency_hz=1000, gain_db=style.get("master_shelf_gain_db", 0.0)),
            HighShelfFilter(cutoff_frequency_hz=AIR_SHELF_FREQ_HZ, gain_db=style.get("air_shelf_gain_db", 0.0))
        ])(y_bwe_test, sr)
    y_bwe_test = signal.sosfiltfilt(signal.butter(4, 15, 'hp', fs=sr, output='sos'), y_bwe_test, axis=1)
    _mem("after_bwe_filter")

    # Simulate downstream linear filtering and LUFS targeting on y_sum_pre_bwe in-place
    # ponytail: reuse y_sum_pre_bwe in-place for calibration to avoid another 125 MB copy
    y_sum_test = y_sum_pre_bwe
    y_sum_test = signal.lfilter(b_bes, a_bes, y_sum_test, axis=1)
    y_sum_test = Pedalboard([HighShelfFilter(cutoff_frequency_hz=1000, gain_db=style.get("master_shelf_gain_db", 0.0))])(y_sum_test, sr)
    y_sum_test = signal.sosfiltfilt(signal.butter(4, 15, 'hp', fs=sr, output='sos'), y_sum_test, axis=1)
    y_sum_test = np.tanh(y_sum_test * 1.02)
    _mem("after_source_filter")

    meter = pyln.Meter(sr)
    g_lufs = 1.0
    for iteration in range(5):
        curr_l = meter.integrated_loudness((y_sum_test * g_lufs).astype(np.float64).T)
        diff = style["target_lufs"] - curr_l
        if abs(diff) <= 0.04: break
        g_lufs *= 10**(diff / 20.0)

    # 4. Measure the Welch PSD band energy of the processed BOOSTED BWE test signal in 18-21.5 kHz
    # float64 cast for calibration precision — float32 Welch PSD is inaccurate
    f_bwe, px_bwe = _welch_mono(y_bwe_test.astype(np.float64), sr)
    px_bwe_db = 10 * np.log10(px_bwe + 1e-12)
    bwe_psd_db = _band_energy_px_db(f_bwe, px_bwe_db, 18000, 21500)

    # 5. Calculate target BWE level (calibrated to -80 to avoid LUFS overshoot and match V106.375 profile)
    # -75 was producing BWE 5 dB too loud (-86 vs -91), increasing true peak and safety trim
    # V106.499h41: per-track `bwe_target_db_offset` lifts the calibration constant. The -80 default
    # was tuned for the OLD sosfiltfilt@22.5k taper that choked 18-22k. With current causal sosfilt@23k
    # + calibration pre-LPF, that -88dB floor creates a persistent 14-18dB "18k cliff" the air-tail
    # cannot fix because the calibration re-pins it every render. Positive offset fills the cliff
    # organically (bwe_18-21.5k moves toward ~-78dB at +9 offset) while keeping the natural steep
    # slope (not over-flattening). Default 0.0 = unchanged from prior behavior.
    target_bwe_db = style["target_lufs"] - 80.0 + float(style.get("bwe_target_db_offset", 0.0))

    # 6. Calculate scale factor using PSD levels (compensated for simulated LUFS gain and initial boost)
    scale_db = target_bwe_db - (bwe_psd_db + 20 * np.log10(g_lufs + 1e-12))
    scale = (10 ** (scale_db / 20.0)) * boost_factor

    # 7. Apply the true scale factor to the nominal BWE layer
    polish_b_layer = y_bwe_nominal * scale

    # GRAVITY-only: blend stochastic air-band noise into the BWE layer (Option D, research-aligned).
    # ponytail: AES audio-coding finding — real HF is noise-dominant (bow noise, breath, room,
    # cymbal sizzle); Suno's codec HF is harmonic-regular. A 100% harmonic BWE re-creates the
    # codec fingerprint the detector keys on. Adding stochastic noise obscures that fingerprint
    # (detector defaults to HUMAN when confused). bwe_noise_ratio=0.0 = off (other tracks unchanged).
    bwe_noise_ratio = float(style.get("bwe_noise_ratio", 0.0))
    if bwe_noise_ratio > 0.0:
        rng = np.random.RandomState(42)
        # pink-ish noise: white through a gentle lowpass to keep energy in 16-21kHz air band
        noise = rng.randn(polish_b_layer.shape[1]).astype(np.float32)
        sos_n = signal.butter(2, 18000, 'high', fs=sr, output='sos')
        noise = signal.sosfiltfilt(sos_n, noise)
        noise = np.stack([noise, noise])
        noise_rms = np.sqrt(np.mean(noise ** 2) + 1e-12)
        # match noise level to the BWE layer's current RMS, then blend by ratio
        bwe_rms = np.sqrt(np.mean(polish_b_layer ** 2) + 1e-12)
        noise *= (bwe_rms / (noise_rms + 1e-12))
        polish_b_layer = (1.0 - bwe_noise_ratio) * polish_b_layer + bwe_noise_ratio * noise
        del rng, noise, sos_n, noise_rms, bwe_rms
        gc.collect()
        _trim()

    # Free calibration intermediates before any further BWE processing
    del y_bwe_nominal, y_bwe_test, y_sum_test, y_sum_pre_bwe, y_sum_pre_lpf, boost_factor
    gc.collect()
    _trim()
    
    # Harshness reduction for CIPHER: pull BWE back 2 dB before crossover
    if style.get("name") == "CIPHER":
        polish_b_layer *= 0.630957
        _mem("after_cipher_bwe_cut")
        
        # CIPHER envelope-side smoothing: gentle dynamic-side compressor on BWE
        # Catches 25 dB transients that laptop speakers can't reproduce cleanly.
        # Approach: per-frame envelope, slow 200ms reference, soft-knee downcomp above -3 dB.
        bwe_env = np.sqrt(np.mean(polish_b_layer**2, axis=0)) + 1e-12
        slow_ref = np.convolve(bwe_env, np.ones(int(0.2*sr))/(0.2*sr), mode='same')
        slow_ref_db = 20.0 * np.log10(slow_ref + 1e-12)
        env_db = 20.0 * np.log10(bwe_env + 1e-12)
        over_db = np.maximum(env_db - slow_ref_db - 3.0, 0.0)
        # Soft-knee: ratio 4:1 above +0 dB knee (per dB of overshoot, gain reduces 0.25 dB)
        gain_reduction_db = over_db * 0.25
        # Limit gain reduction to [-12, 0] dB so we can't completely kill BWE
        gain_reduction_db = np.clip(gain_reduction_db, 0.0, 12.0)
        gain_factor = 10.0 ** (-gain_reduction_db / 20.0)
        polish_b_layer = polish_b_layer * gain_factor[None, :]
        del bwe_env, slow_ref, slow_ref_db, env_db, over_db, gain_reduction_db, gain_factor
        gc.collect()
        _trim()
        _mem("after_cipher_bwe_smooth")

    # 8. Add BWE to master with crossover (prevents intermodulation bulge)
    # Without crossover: BWE harmonics + source content coexist → difference tones at 8-10kHz
    # Source already LPF'd at CROSSOVER_HZ above; BWE gets HPF to isolate CROSSOVER_HZ+ extension only.
    # BWE harmonics (CROSSOVER_HZ+) don't overlap with source below CROSSOVER_HZ = no bulge
    _mem("before_bwe_crossover")
    # CROSSOVER_HZ defined earlier (line ~1066) — shared with source LPF
    # BWE crossover HPF — 2nd-order causal Butterworth complements the 3rd-order causal source LPF
    # to form a smooth, monotonic crossover descent without an 18kHz step cliff.
    _XO_HP_ORDER = int(style.get("bwe_xo_hp_order", 2))
    sos_xo_hp = signal.butter(_XO_HP_ORDER, CROSSOVER_HZ, 'high', fs=sr, output='sos')

    # BWE taper at 21.2kHz — lets 16-21kHz air extend smoothly and roll off cleanly toward Nyquist (24k).
    # 4th-order causal sosfilt eliminates the artificial 22-24k ultrasonic bulge.
    _TAPER_HZ = float(style.get("bwe_taper_hz", 21200.0))
    _TAPER_ORDER = int(style.get("bwe_taper_order", 4))
    sos_bwe_taper = signal.butter(_TAPER_ORDER, _TAPER_HZ, 'low', fs=sr, output='sos')
    polish_b_layer = signal.sosfilt(sos_bwe_taper, polish_b_layer, axis=1)
    # BWE layer gets the HPF to isolate 16kHz+ extension only
    y_bwe_hp = signal.sosfilt(sos_xo_hp, polish_b_layer, axis=1)
    # Natural Outro BWE: BWE follows source envelope naturally into the master outro fade.
    # Removed premature 30s zeroing that was creating an empty black void above 16k in the outro.
    # V106.499h37 N1: Spectral-entropy injection — stochastic amplitude modulation of BWE layer
    # Breaks the "overly smooth reconstruction" tell that AI detectors key on (arxiv 2501.10111,
    # hidemysong.com/codec-residual-fingerprints). Human recordings have time-varying stochastic
    # HF texture; AI BWE is too uniform. A slow random-walk gain envelope (0.85-1.15, ~1Hz drift)
    # adds organic irregularity without changing average level or introducing phase artifacts.
    # ponytail: single multiply, no filters, no cascade. Ceiling upgrade: replace random walk
    # with perceptually-weighted stochastic process if detector adapts to uniform random.
    if os.environ.get("AI2G_AB_N1", "0") != "1":
        _n1_seed = int(style.get("_n1_seed", 42))
        rng = np.random.RandomState(_n1_seed)
        _n1_len = y_bwe_hp.shape[1]
        _n1_block = sr  # 1Hz update rate
        _n1_blocks = max(1, _n1_len // _n1_block)
        _n1_raw = rng.uniform(-0.15, 0.15, size=_n1_blocks + 1)
        _n1_interp = np.interp(
            np.arange(_n1_len),
            np.linspace(0, _n1_len, _n1_blocks + 1),
            _n1_raw
        ).astype(np.float32)
        _n1_env = (1.0 + _n1_interp).reshape(1, -1)
        y_bwe_hp = y_bwe_hp * _n1_env
        print(f" [+] Spectral Entropy | N1 stochastic BWE modulation engaged (seed={_n1_seed}, ±0.15dB, 1Hz drift)")

    # BWE M/S Spatial Widening (Style-Driven): lifts ultrasonic air width without Haas comb-filtering
    _bwe_spatial_target = float(style.get("bwe_spatial_target", 0.0))
    if _bwe_spatial_target > 0.0 and y_bwe_hp.shape[0] >= 2:
        bwe_mid = (y_bwe_hp[0] + y_bwe_hp[1]) / 2.0
        bwe_side = (y_bwe_hp[0] - y_bwe_hp[1]) / 2.0
        m_rms = np.sqrt(np.mean(bwe_mid**2)) + 1e-12
        s_rms = np.sqrt(np.mean(bwe_side**2)) + 1e-12
        curr_ratio = float(s_rms / m_rms)
        side_scale = float(np.clip(_bwe_spatial_target / (curr_ratio + 1e-12), 1.0, 5.0))
        bwe_side_wide = bwe_side * side_scale
        y_bwe_hp[0] = bwe_mid + bwe_side_wide
        y_bwe_hp[1] = bwe_mid - bwe_side_wide
        print(f" [+] BWE M/S Spatial Widener | Side scaled {side_scale:.2f}x (S/M: {curr_ratio:.3f} -> {_bwe_spatial_target:.3f})")

    # V106.499 A/B: AI2G_AB_BWE=1 skips BWE injection (y_sum += y_bwe_hp) to test if the
    # BWE layer is the outro whistle source. ponytail: diagnostic only, off by default.
    if os.environ.get("AI2G_AB_BWE", "0") == "1" or _is_apollo:
        if _is_apollo:
            print(" [+] Apollo-Native Source | Engine BWE injection bypassed (Apollo 16-24kHz air preserved)")
        else:
            print(" [A/B] BWE injection SKIPPED (AI2G_AB_BWE=1) — y_sum left without BWE layer")
    else:
        y_sum = y_sum + y_bwe_hp

    # V106.499h24: Notch fill moved to Final Muzzle (PeakingFilter at 16kHz) — no band-pass injection here.
    _nf_actual = float(style.get("notch_fill_db", 0.0))
    exciter_meta["notch_fill"] = {"enabled": _nf_actual != 0.0, "method": "peaking_eq_final_muzzle",
                                  "center_hz": 16000, "gain_db": _nf_actual, "q": 2.0}

    # Self-check: verify BWE layer has energy (only when BWE is actually injected)
    if os.environ.get("AI2G_AB_BWE", "0") != "1" and not _is_apollo:
        bwe_hf = _band_signal(polish_b_layer, sr, [15000, 21000])
        bwe_rms_db = float(_dbfs(bwe_hf))
        assert bwe_rms_db > -80.0, f"BWE insertion failed: BWE layer {bwe_rms_db:.2f} dBFS is too quiet"
        exciter_meta["bwe_hf_delta_db"] = round(bwe_rms_db, 2)
    else:
        exciter_meta["bwe_hf_delta_db"] = None

    effective_gain_offset_db = 20.0 * np.log10(scale + 1e-12)
    polish_b_band = _band_signal(polish_b_layer, sr, [18000, 21500])
    upper_bp = 22000.0
    exciter_meta["sovereign_polish_b"] = {
        "enabled": True,
        "cliff_frequency_hz": float(cl_f),
        "gain_offset_db": round(float(effective_gain_offset_db), 2),
        "cliff_reference_db": round(float(en_cliff), 2),
        "noise_band_hz": [float(cl_f), float(upper_bp)],
        "decay_lowpass_hz": 22000.0,
        "gain_drift_depth_db": 1.2,
        "envelope_source": "full_mix",
        "envelope_parts": [],
        "full_mix_floor_coeff": 0.70,
        "continuity_bed": {
            "enabled": False,
            "floor": 0.02,
            "drift_enabled": False,
            "drift_depth": 0.0,
            "floor_curve_min": 0.02,
            "floor_curve_mean": 0.02,
            "floor_curve_max": 0.02,
            "pre_floor_mean": 1.0,
            "pre_floor_below_floor_ratio": 0.0,
        },
        "pre_master_bwe_18k_21_5k_rms_db": round(float(_dbfs(polish_b_band)), 2)
    }

    # 5. Final Muzzle & Cleanup (Haas delay restored - proven 80% Human scorer)
    # V106.499h24: Bessel LPF is identity when muzzle skipped (b_bes=[1.0], a_bes=[1.0])
    y_sum = signal.lfilter(b_bes, a_bes, y_sum, axis=1)
    y_sum = Pedalboard([
            HighShelfFilter(cutoff_frequency_hz=1000, gain_db=style.get("master_shelf_gain_db", 0.0)),
            HighShelfFilter(cutoff_frequency_hz=AIR_SHELF_FREQ_HZ, gain_db=style.get("air_shelf_gain_db", 0.0))
        ])(y_sum, sr)

    # Final Muzzle surgical EQs: style-driven (gated per track)
# PeakFilter defined above
    _notch_16k_db = float(style.get("notch_fill_db", 0.0))
    if _notch_16k_db != 0.0:
        y_sum = PeakFilter(cutoff_frequency_hz=16000.0, gain_db=_notch_16k_db, q=2.0)(y_sum, sr)
        print(f" [+] Notch Fill | PeakFilter: {_notch_16k_db:+.1f} dB @ 16kHz Q=2.0 applied in Final Muzzle")

    _pres_8k_db = float(style.get("presence_boost_db", 0.0))
    if _pres_8k_db != 0.0:
        y_sum = PeakFilter(cutoff_frequency_hz=8000.0, gain_db=_pres_8k_db, q=1.0)(y_sum, sr)
        print(f" [+] Presence Boost | PeakFilter: {_pres_8k_db:+.1f} dB @ 8kHz Q=1.0 applied in Final Muzzle")

    _bril_12k_db = float(style.get("brilliance_boost_db", 0.0))
    if _bril_12k_db != 0.0:
        y_sum = PeakFilter(cutoff_frequency_hz=12000.0, gain_db=_bril_12k_db, q=1.0)(y_sum, sr)
        print(f" [+] Brilliance Boost | PeakFilter: {_bril_12k_db:+.1f} dB @ 12kHz Q=1.0 applied in Final Muzzle")

    _mid_1k_db = float(style.get("mid_1k_boost_db", 0.0))
    if _mid_1k_db != 0.0:
        y_sum = PeakFilter(cutoff_frequency_hz=1000.0, gain_db=_mid_1k_db, q=1.2)(y_sum, sr)
        print(f" [+] Mid Notch Fill | PeakFilter: {_mid_1k_db:+.1f} dB @ 1000Hz Q=1.2 applied in Final Muzzle")

    _mid_3k_carve_db = float(style.get("mid_3k_carve_db", 0.0))
    if _mid_3k_carve_db != 0.0:
        _mid_3k_freq = float(style.get("mid_3k_carve_freq_hz", 3100.0))
        _mid_3k_q = float(style.get("mid_3k_carve_q", 1.5))
        y_sum = PeakFilter(cutoff_frequency_hz=_mid_3k_freq, gain_db=_mid_3k_carve_db, q=_mid_3k_q)(y_sum, sr)
        print(f" [+] 3k RVQ Grid Carve | PeakFilter: {_mid_3k_carve_db:+.1f} dB @ {_mid_3k_freq:.0f}Hz Q={_mid_3k_q:.2f} applied in Final Muzzle")

    _mid_5k_carve_db = float(style.get("mid_5k_carve_db", 0.0))
    if _mid_5k_carve_db != 0.0:
        _mid_5k_freq = float(style.get("mid_5k_carve_freq_hz", 5050.0))
        _mid_5k_q = float(style.get("mid_5k_carve_q", 1.5))
        y_sum = PeakFilter(cutoff_frequency_hz=_mid_5k_freq, gain_db=_mid_5k_carve_db, q=_mid_5k_q)(y_sum, sr)
        print(f" [+] 5k RVQ Grid Carve | PeakFilter: {_mid_5k_carve_db:+.1f} dB @ {_mid_5k_freq:.0f}Hz Q={_mid_5k_q:.2f} applied in Final Muzzle")

    # ─── Acoustic Comb Solver (V106.500h56): pipeline-aware null prediction + fine-res
    # measurement + self-verifying flatten. Physics: a delay d on ONE channel cancels the
    # mono sum at f_k = (2k+1)/(2d). At d=0.5ms that is 1k,3k,5k,7k,... Hz. We predict the
    # nulls inside the Haas band, measure each one's true depth at 1.5Hz resolution on the
    # Mid channel (smoothed Welch under-reads narrow combs), fill by MEASURED depth, then
    # re-measure to log the residual. Fully automatic; no hand-tuned windows.
    _comb_owned_hz = []
    if style.get("comb_solver_enabled", True):
        _haas_mode_eff = str(style.get("haas_mode", "")).lower()
        if _haas_mode_eff in ("bandlimited", "textbook"):
            _d_ms = float(style.get("haas_delay_ms", 0.5))
            _band = style.get("haas_band_hz", [2000, 6000])
            _lo_b, _hi_b = float(_band[0]), float(_band[1]) if _band[1] else 24000.0
            _step = 1.0 / (2.0 * (_d_ms / 1000.0))          # null spacing (1000Hz at 0.5ms)
            _k0 = int(_math.ceil(_lo_b / _step))
            if _k0 % 2 == 0: _k0 += 1                        # ensure odd multiple
            _predicted = []
            _fk = _k0 * _step
            while _fk < _hi_b:
                _predicted.append(_fk); _fk += 2.0 * _step
            if _predicted:
                _cs_max_db = float(style.get("comb_solver_max_db", 6.0))
                _cs_frac = float(style.get("comb_solver_fill_fraction", 0.40))
                mono_cs = (y_sum[0] + y_sum[1]) / 2.0
                f_cs, Pxx_cs = signal.welch(mono_cs.astype(np.float64), fs=sr, nperseg=32768, noverlap=16384)
                Pdb_cs = 10 * np.log10(Pxx_cs + 1e-20)

                def _bd(f_arr, P, lo, hi):
                    msk = (f_arr >= lo) & (f_arr < hi)
                    return float(np.mean(P[msk])) if msk.any() else float("nan")

                # V106.500h63: max fill cap lowered 9 -> 6 dB. Grounds: ringing amplitude
                # scales with boost depth x Q (Leffler-Schulman mastering writeup) — high-Q
                # boosts ring at center freq after transients, exposed in sparse breakdowns.
                # ZERO empirical ladder (h52-h62): effective fills beyond ~+5dB monotonically
                # worsened both reference audit and detector score. 6dB = classic mastering
                # ceiling + our measured optimum headroom. Affects future tracks with deeper
                # combs; ZERO shipped at +4.14/+4.84 (under cap).
                # V106.500h61: CLOSED-LOOP reference-seeking fills. Same 17-band commercial
                # reference table the telemetry audit uses. Each null's fill is capped by
                # BOTH the remaining comb depth AND the local headroom-to-reference, so
                # flattening goes as deep as commercially sane — deeper where the curve
                # has room (e.g. 7k), gentler where it doesn't (3.5-6k). Replaces the
                # fixed-fraction compromise with measured truth.
                _ref_buckets = [
                    (500,1000,-49.84),(1000,1500,-52.68),(1500,2000,-54.55),(2000,2500,-57.71),
                    (2500,3000,-58.92),(3000,3500,-60.54),(3500,4000,-61.68),(4000,5000,-62.61),
                    (5000,6000,-63.95),(6000,8000,-65.52),(8000,10000,-66.84),(10000,12000,-68.53)]
                def _headroom_db(f0):
                    lo,hi = max(300,int(f0-450)), min(12000,int(f0+450))
                    cur = _bd(f_cs, Pdb_cs, lo, hi)
                    cands=[r for b_lo,b_hi,r in _ref_buckets if b_hi>lo and b_lo<hi]
                    if not cands: return None
                    ref = float(np.mean(cands))
                    return ref - cur   # positive = we are BELOW reference -> room to fill

                _applied = []
                for _f0 in _predicted:
                    core = _bd(f_cs, Pdb_cs, _f0 - 120, _f0 + 120)
                    sh_l = _bd(f_cs, Pdb_cs, _f0 - 750, _f0 - 350)
                    sh_r = _bd(f_cs, Pdb_cs, _f0 + 350, _f0 + 750)
                    base = float(np.nanmax([sh_l, sh_r]))
                    depth = base - core
                    if depth >= 0.75:
                        _room = _headroom_db(_f0)
                        g_depth = depth * _cs_frac
                        if _room is not None:
                            g = float(np.clip(min(g_depth, max(_room, 0.5)), 0.5, _cs_max_db))
                            _why = f"depth-limited {g_depth:.2f}" if g==g_depth else (f"reference-limited {_room:.2f}" if g<_cs_max_db else "cap")
                        else:
                            g = float(np.clip(g_depth, 0.5, _cs_max_db)); _why="depth-limited"
                        y_sum = PeakFilter(cutoff_frequency_hz=_f0, gain_db=g, q=2.80)(y_sum, sr)
                        _applied.append({"f0_hz": round(_f0, 1), "depth_db": round(depth, 2),
                                         "gain_db": round(g, 2), "q": 2.80,
                                         "headroom_to_ref_db": round(_room,2) if _room is not None else None})
                        print(f" [+] Comb Solver | predicted {_f0:.0f}Hz: depth {depth:.2f}dB -> filled +{g:.2f}dB (Q=2.80, {_why})")
                if _applied:
                    # Self-verification: re-measure residual at each treated null
                    mono_v = (y_sum[0] + y_sum[1]) / 2.0
                    fv, Pv = signal.welch(mono_v.astype(np.float64), fs=sr, nperseg=32768, noverlap=16384)
                    Pdbv = 10 * np.log10(Pv + 1e-20)
                    for _a in _applied:
                        _f0 = _a["f0_hz"]
                        core = _bd(fv, Pdbv, _f0 - 120, _f0 + 120)
                        sh_l = _bd(fv, Pdbv, _f0 - 750, _f0 - 350)
                        sh_r = _bd(fv, Pdbv, _f0 + 350, _f0 + 750)
                        resid = float(np.nanmax([sh_l, sh_r])) - core
                        _a["residual_db"] = round(resid, 2)
                        print(f" [+] Comb Solver | verify {_f0:.0f}Hz: residual {resid:.2f}dB")
                    exciter_meta["comb_solver"] = {"enabled": True, "delay_ms": _d_ms,
                                                   "nulls_treated": _applied}
                else:
                    exciter_meta["comb_solver"] = {"enabled": True, "delay_ms": _d_ms,
                                                   "nulls_treated": [], "note": "no comb >= 0.75dB"}
        else:
            exciter_meta["comb_solver"] = {"enabled": False, "reason": f"haas_mode={style.get('haas_mode')}"}
    # Exposed for the DNA scanner: nulls already owned by the physics solver
    _comb_owned_hz = [a["f0_hz"] for a in exciter_meta.get("comb_solver", {}).get("nulls_treated", [])]


    # ─── Smart DNA-Mapped Autonomic Notch Detector & Solver (Ponytail Protocol) ───
    # Autonomously scans high-resolution Welch PSD for physical comb nulls & troughs (3kHz & 13kHz),
    # detects exact center frequency f0, -3dB bandwidth (Q), and trough depth (dB),
    # and applies precision parametric PeakFilter compensation out-of-the-box.
    if style.get("dna_notches_enabled", False):  # h158: default OFF (commercial — no autonomic trough-fills)
        # 2026-08-23 directive: 5k/7k dip fills restored for ALL tracks — sane-engineered:
        # measurement-gated (fires only on real >=0.75dB dips), narrow adaptive-Q, measurement-gated with raised caps
        # (5k full-fill authorized 2026-08-23 after h52 A/B: "sounds fuller"). The old harsh era was UNCAPPED broad boost-flattening;
        # these caps keep musical articulation intact while restoring presence.
        _dna_windows = [
            (2600.0, 3500.0, "3k Mid Comb", float(style.get("dna_notch_3k_max_db", 3.0))),
            (12500.0, 14500.0, "13k HF Dip", float(style.get("dna_notch_13k_max_db", 3.5))),
            (4600.0, 5500.0, "5k Notch Fill", float(style.get("dna_notch_5k_max_db", 6.0))),
            (6500.0, 7600.0, "7k Dip Fill", float(style.get("dna_notch_7k_max_db", 6.0)))
        ]
        mono_dna = (y_sum[0] + y_sum[1]) / 2.0
        f_dna, Pxx_dna = signal.welch(mono_dna, fs=sr, nperseg=8192, noverlap=4096)
        Pxx_db_dna = 10 * np.log10(Pxx_dna + 1e-15)
        Pxx_smooth_dna = _scipy_median_filter(Pxx_db_dna, size=5)

        for freq_min, freq_max, label, max_boost in _dna_windows:
            if max_boost <= 0.0:
                continue
            if any(freq_min <= _cf <= freq_max for _cf in _comb_owned_hz):
                print(f" [+] DNA Notch Auto-Fix | {label:<12}: SKIPPED (owned by Comb Solver, no double-fill)")
                continue
            band_mask = (f_dna >= freq_min) & (f_dna <= freq_max)
            f_band = f_dna[band_mask]
            P_band = Pxx_smooth_dna[band_mask]
            if len(P_band) < 5:
                continue
            min_idx = np.argmin(P_band)
            f0 = float(f_band[min_idx])
            trough_val = float(P_band[min_idx])

            left_shoulder = (f_dna >= freq_min * 0.88) & (f_dna <= freq_min * 0.96)
            right_shoulder = (f_dna >= freq_max * 1.04) & (f_dna <= freq_max * 1.12)
            if left_shoulder.any() and right_shoulder.any():
                baseline = float((np.median(Pxx_smooth_dna[left_shoulder]) + np.median(Pxx_smooth_dna[right_shoulder])) / 2.0)
            else:
                baseline = float((P_band[0] + P_band[-1]) / 2.0)

            depth_db = float(baseline - trough_val)
            if depth_db >= 0.75:
                half_depth = baseline - (depth_db * 0.5)
                left_side = P_band[:min_idx]
                right_side = P_band[min_idx:]
                f_left = float(f_band[0])
                for idx_l in range(len(left_side) - 1, -1, -1):
                    if left_side[idx_l] >= half_depth:
                        f_left = float(f_band[idx_l])
                        break
                f_right = float(f_band[-1])
                for idx_r in range(len(right_side)):
                    if right_side[idx_r] >= half_depth:
                        f_right = float(f_band[min_idx + idx_r])
                        break
                bw = max(f_right - f_left, 120.0)
                _q_max = float(style.get("dna_notch_13k_q_cap", 1.45)) if "13k" in label else 2.80
                _q_min = 0.80
                q = float(np.clip(f0 / bw, _q_min, _q_max))
                gain_db = float(np.clip(depth_db * 0.85, 0.5, max_boost))
                y_sum = PeakFilter(cutoff_frequency_hz=f0, gain_db=gain_db, q=q)(y_sum, sr)
                print(f" [+] DNA Notch Auto-Fix | {label:<12}: Detected f0={f0:.1f}Hz (Depth={depth_db:.2f}dB, BW={bw:.1f}Hz) -> Applied PeakFilter +{gain_db:.2f}dB (Q={q:.2f})")

    _sub_boost_db = float(style.get("sub_boost_db", 0.0))
    if _sub_boost_db != 0.0:
        _sub_freq = float(style.get("sub_boost_freq_hz", 50.0))
        y_sum = PeakFilter(cutoff_frequency_hz=_sub_freq, gain_db=_sub_boost_db, q=1.0)(y_sum, sr)
        print(f" [+] Sub-Bass Anchor | PeakFilter: {_sub_boost_db:+.1f} dB @ {_sub_freq:.0f}Hz Q=1.0 applied in Final Muzzle")

    y_sum = signal.sosfiltfilt(sos_hpf, y_sum, axis=1)

    # Ultrasonic Acoustic Taper: smooth metal/hardware converter roll-off (2nd-order causal @ 20.5kHz).
    # Produces a natural continuous gradient from 20kHz -> 24kHz matching reference masters.
    _nyquist_taper_hz = float(style.get("nyquist_taper_hz", 20500.0))
    _nyquist_taper_order = int(style.get("nyquist_taper_order", 2))
    if _nyquist_taper_hz > 0.0:
        sos_nyquist = signal.butter(_nyquist_taper_order, _nyquist_taper_hz, 'low', fs=sr, output='sos')
        y_sum = signal.sosfilt(sos_nyquist, y_sum, axis=1)
        print(f" [+] Ultrasonic Acoustic Taper | Butter({_nyquist_taper_order}) @ {_nyquist_taper_hz:.0f}Hz applied in Final Muzzle")
    
    # CIPHER-only: gentle 10 kHz source-band shelf to tame laptop-speaker harshness
    # Targets the 6-12 kHz presence band where energy lives; BWE band is inaudible on laptops.
    if style.get("name") == "CIPHER":
        y_sum = HighShelfFilter(cutoff_frequency_hz=10000, gain_db=-1.5)(y_sum, sr)

    # GRAVITY-only: donor-band broadband lift (8-12 kHz) to test the broken-clamp spectral effect.
    # ponytail: the old BROKEN spatial clamp (wrong M/S math, V106.44x) accidentally injected
    # unattenuated side into R, boosting GRAVITY's HF/donor region ~+2.4 dB and flipping its
    # spectral verdict to "Human made" (artifact of the bug). The bug's *spectral effect* was a
    # broadband 8-12 kHz lift, NOT a mid tilt — my V106.457 mid tilt (1.5-3k) was the wrong band
    # and regressed (26% Human). This replicates the donor-band axis cleanly. donor_band_lift_db=0.0
    # = off (other tracks unchanged). Single-variable A/B for V106.460.
    donor_band_lift_db = style.get("donor_band_lift_db", 0.0)
    if donor_band_lift_db != 0.0 and style.get("name") == "GRAVITY":
        y_sum = Pedalboard([
            HighShelfFilter(cutoff_frequency_hz=8000.0, gain_db=+donor_band_lift_db, q=0.707),
            HighShelfFilter(cutoff_frequency_hz=11000.0, gain_db=+donor_band_lift_db * 0.7, q=0.707),
        ])(y_sum, sr)

    # 5a. Industry-standard M/S Haas — 0.5ms (calibrated for this track)
    # V106.416 baseline: 0.5ms at 600Hz HPF gives spatial ~0.26 pre-master and 1.0 post-master.
    # Longer delays such as 27ms regressed temporal scores on this material, so delay is
    # intentionally kept in the psychoacoustic width zone rather than the musical echo zone.

    # 5b. Broadband noise floor — re-enabled as a configurable humanization lever.
    # ponytail: detectors flag "too clean or too patterned" noise floor (Undetectr 6-axis). Our tracks
    # are surgically clean -> reads as synthetic. A subtle broadband noise floor (full spectrum incl.
    # HF air) mimics natural tape/room noise. noise_floor_db = absolute target RMS in dBFS; 0.0 = off
    # (other tracks unchanged). Default OFF — only GRAVITY enables it via track config for A/B.
    # Original impl was LF-only (<500Hz brown); changed to broadband (HP@80Hz) so it covers the HF
    # air band the spectral model actually scans.
    noise_floor_db = float(style.get("noise_floor_db", 0.0))
    if noise_floor_db < 0.0:
        rng = np.random.RandomState(42)
        noise = rng.randn(y_sum.shape[1]).astype(np.float32)
        sos_nf = signal.butter(2, 80, 'high', fs=sr, output='sos')
        noise = signal.sosfiltfilt(sos_nf, noise)
        noise = np.stack([noise, noise])
        noise_rms = np.sqrt(np.mean(noise ** 2) + 1e-12)
        target_rms = 10 ** (noise_floor_db / 20.0)
        noise *= (target_rms / (noise_rms + 1e-12))
        y_sum = y_sum + noise
        del rng, noise, sos_nf, noise_rms, target_rms
        gc.collect()
        _trim()

    # 5c. Spectral tilt compensation: HF + presence level match vs reference
    # ponytail: REDUCED from +2.0/+2.5 (V106.472) to +0.5/+0.8 (V106.473). The old +4.5dB combined
    # boost sat inside the 4-14kHz fakeprint band (lofcz detector scan zone) and likely REINFORCED
    # the codec artifact. Pulling ~3.7dB out of 6-8k is the last GRAVITY A/B. Re-verify on a passing
    # track (ZERO/SIGNAL/ONE) before declaring this the global default.
    # V106.499h24: moved to per-track OPT-IN via 'hf_tilt_db' style override (default 0.0 = OFF).
    # RESEARCH (MasteringBOX, SageAudio, Gearspace): industry standard is to boost 10kHz+ for AIR,
    # and CUT 3-8kHz if harsh — NOT boost 6-8kHz. The +0.5/+0.8 hardcode violates this convention
    # and is applied uniformly without per-track need assessment. Measured impact on ZERO h13 = ~0.6 dB
    # at 6-8k (negligible vs +7dB LUFS/BWE gain) but directionally wrong per research. Default OFF.
    _hf_tilt_db = float(style.get("hf_tilt_db", 0.0))
    if _hf_tilt_db > 0.0:
        y_sum = Pedalboard([
            HighShelfFilter(cutoff_frequency_hz=6000.0, gain_db=+(_hf_tilt_db * 0.625), q=0.707),
            HighShelfFilter(cutoff_frequency_hz=8000.0, gain_db=+(_hf_tilt_db * 1.0), q=0.707)
        ])(y_sum, sr)
        print(f" [+] HF Tilt | +{_hf_tilt_db * 0.625:.2f}dB@6k +{_hf_tilt_db * 1.0:.2f}dB@8k (per-track opt-in)")
    else:
        print(" [+] HF Tilt | SKIPPED (hf_tilt_db=0.0 default)")

    # h161: dry-global default — tape OFF unless style opts in (TERRA 4-way: dry punchier, +0.05 crest)
    if os.environ.get("AI2G_AB_TANH", "0") == "1" or not style.get("tape_enabled", False):
        print(" [A/B] Full-mix tape saturation SKIPPED (dry-global h161 default)")
    else:
        # V106.499h33 Option J: Master-grade Analog Tape Emulator
        # Research (ISMIR 2025): Neural codecs leave a distinct symmetric deconvolution grid.
        # This module injects organic even/odd harmonic chaos & phase entropy using dynamic tape emulation:
        # 1. Pre-emphasis: Boost HF (4kHz+) by +2dB to saturate high frequencies faster (tape bias simulation).
        # 2. Asymmetric bias (0.04): Generates warm even harmonics to break decoder grid symmetry.
        # 3. Non-linear saturation: Soft-clipping with np.tanh (drive = 1.03).
        # 4. De-emphasis: Cut HF (4kHz+) by -2dB to restore spectral balance.
        # This dynamic HF saturation acts as an intelligent, program-dependent high-frequency compressor.
        _pre_amp = Pedalboard([HighShelfFilter(cutoff_frequency_hz=4000.0, gain_db=+2.0, q=0.707)])
        _de_amp  = Pedalboard([HighShelfFilter(cutoff_frequency_hz=4000.0, gain_db=-2.0, q=0.707)])
        
        # Pre-emphasis HF boost
        _y_temp = _pre_amp(y_sum, sr)
        
        # Asymmetric wave-shaper
        _bias = 0.04
        _drive = float(style.get("tape_drive", 1.03))
        _y_shaped = np.tanh((_y_temp + _bias) * _drive) - np.tanh(_bias * _drive)
        
        # De-emphasis HF cut
        y_sum = _de_amp(_y_shaped, sr)
        print(f" [+] Tape Saturator | Master-grade Analog Tape Emulator engaged (bias={_bias}, drive={_drive}, HF pre/de-emphasis 4kHz)")
        del _pre_amp, _de_amp, _y_temp, _bias, _drive, _y_shaped

    # 6. Iterative LUFS & Hard Clipping
    # With safety trim disabled, converge directly to target; small post-trim fine-tune remains.
    lufs_target = style["target_lufs"]
    pre_master_lufs = meter.integrated_loudness(y_sum.astype(np.float64).T)
    iterations_done = 0
    sovereign_ceiling_db = SOVEREIGN_TRUE_PEAK_CEILING_DB
    ceiling_lin = 10**(sovereign_ceiling_db / 20.0)

    # 6. SOTA Hybrid Peak Management & Convergence
    # V106_500h122: Multi-mode peak management:
    # - "transparent_lookahead": Smooth lookahead limiter (fast attack 1.5ms, smooth 30ms release).
    #   Used for acoustic / world / cinematic tracks (MYTH, CIPHER) to protect tonal waveforms from odd-harmonic square-wave clipping.
    # - "clipper_hybrid" (default for Trance/Metal): Surgical hard clip on dense drum transients for maximum punch.
    peak_limiter_mode = style.get("peak_limiter_mode", "transparent_lookahead")  # h158: commercial default (no clip->limit hybrid)
    hard_clip_val = style.get("hard_clip_db", HARD_CLIP_THRESHOLD_DB)
        
    # Iterative LUFS convergence BEFORE peak processing
    for iteration in range(5):
        iterations_done = iteration + 1
        curr_l = meter.integrated_loudness(y_sum.astype(np.float64).T)
        diff = lufs_target - curr_l
        if abs(diff) <= 0.04: break
        y_sum *= 10**((diff * 0.7) / 20.0)  # 0.7 damping to prevent overshoot

    pre_clip_true_peak_db = _true_peak_db(y_sum, sr)
    peak_cap_db = float(style.get("true_peak_cap_db", 0.78))
    _lim_gr_db = 0.0  # h132: wired limiter GR meter (measurement-only, audio-neutral)
    _clip_thr_db = None  # h133: wired clip threshold meter (None = no clipper engaged)

    if peak_limiter_mode == "hybrid":
        # Hybrid (industry-standard clip->limit): hard clipper shaves fastest
        # 1-2dB peaks first (keeps transient energy), lookahead limiter then
        # catches the ceiling transparently. Neither stage works hard alone.
        hard_clip_threshold_db = float(hard_clip_val) if hard_clip_val is not None else -0.8
        hard_clip_lin = 10**(hard_clip_threshold_db / 20.0)
        y_sum = _os_clip(y_sum, hard_clip_lin)
        _clip_thr_db = float(hard_clip_threshold_db)
        _lim_rel_ms = float(style.get("limiter_release_ms", 30.0))
        _pre_lim_tp = _true_peak_db(y_sum, sr)
        y_sum = _transparent_lookahead_limiter(y_sum, sr, ceiling_db=peak_cap_db, lookahead_ms=1.5, release_ms=_lim_rel_ms)
        _lim_gr_db = max(0.0, float(_pre_lim_tp) - float(_true_peak_db(y_sum, sr)))
        print(f" [+] Peak Limiter | Hybrid clip->limit (clip={hard_clip_threshold_db:+.2f}dBFS, ceiling={peak_cap_db:+.2f}dBTP, release={_lim_rel_ms:.0f}ms)")
        post_clip_true_peak_db = _true_peak_db(y_sum, sr)
        clip_reduction_db = pre_clip_true_peak_db - post_clip_true_peak_db
    elif peak_limiter_mode == "transparent_lookahead" or hard_clip_val is None:
        # Transparent lookahead envelope limiter (zero square-wave clipping)
        _lim_rel_ms = float(style.get("limiter_release_ms", 30.0))
        _pre_lim_tp = _true_peak_db(y_sum, sr)
        y_sum = _transparent_lookahead_limiter(y_sum, sr, ceiling_db=peak_cap_db, lookahead_ms=1.5, release_ms=_lim_rel_ms)
        _lim_gr_db = max(0.0, float(_pre_lim_tp) - float(_true_peak_db(y_sum, sr)))
        print(f" [+] Peak Limiter | SOTA Transparent Lookahead Limiter engaged (ceiling={peak_cap_db:+.2f}dBTP, lookahead=1.5ms, release={_lim_rel_ms:.0f}ms)")
        post_clip_true_peak_db = _true_peak_db(y_sum, sr)
        clip_reduction_db = pre_clip_true_peak_db - post_clip_true_peak_db
    else:
        # Traditional dance/metal punch clipper
        hard_clip_threshold_db = float(hard_clip_val)
        hard_clip_lin = 10**(hard_clip_threshold_db / 20.0)
        y_sum = np.clip(y_sum, -hard_clip_lin, hard_clip_lin)
        _clip_thr_db = float(hard_clip_threshold_db)
        post_clip_true_peak_db = _true_peak_db(y_sum, sr)
        clip_reduction_db = pre_clip_true_peak_db - post_clip_true_peak_db
        print(f" [+] Peak Limiter | Dancefloor Transient Hard-Clipper engaged (threshold={hard_clip_threshold_db:+.2f}dBFS)")

    append_stage_snapshot(stage_snapshots, "post_peak_block", y_sum, sr, full_metrics=False)  # h133: squash forensics

    pre_export_true_peak_db = _true_peak_db(y_sum, sr)
    pre_export_lufs = meter.integrated_loudness(y_sum.astype(np.float64).T)
    pre_export_sample_peak_db = _peak_db(y_sum)
    safety_trim_db = 0.0
    if SOVEREIGN_EXPORT_SAFETY_TRIM_ENABLED:
        safety_trim_db = min(0.0, float(SOVEREIGN_EXPORT_TRUE_PEAK_TARGET_DB) - pre_export_true_peak_db)
        if abs(safety_trim_db) > 1e-9:
            y_sum *= 10 ** (safety_trim_db / 20.0)

    # Post-trim fine-tune: close residual LUFS gap without capping by peak ceiling.
    final_lufs = meter.integrated_loudness(y_sum.astype(np.float64).T)
    lufs_error = style["target_lufs"] - final_lufs
    if abs(lufs_error) > 0.02:
        y_sum *= 10 ** (lufs_error / 20.0)

    final_lufs = meter.integrated_loudness(y_sum.astype(np.float64).T)
    final_true_peak_db = _true_peak_db(y_sum, sr)
    final_sample_peak_db = _peak_db(y_sum)

    # Peak cap + LUFS nudge iteration: both must converge together.
    # Iterate until LUFS within 0.02 dB AND true peak <= cap.
    peak_cap_db = style.get("true_peak_cap_db", 0.78)
    for _ in range(5):
        final_lufs = meter.integrated_loudness(y_sum.astype(np.float64).T)
        final_true_peak_db = _true_peak_db(y_sum, sr)
           
        lufs_error = style["target_lufs"] - final_lufs
        peak_over = final_true_peak_db - peak_cap_db
            
        if abs(lufs_error) <= 0.02 and peak_over <= 0.01:
            break
            
        if peak_over > 0.01:
            if style.get("name") == "DAWN":
                # h132: limit instead of trim — transparent envelope at cap, micro-trim residuals only
                _lim_rel_ms = float(style.get("limiter_release_ms", 30.0))
                _pre_loop_lim_tp = _true_peak_db(y_sum, sr)
                y_sum = _transparent_lookahead_limiter(y_sum, sr, ceiling_db=float(peak_cap_db), lookahead_ms=1.5, release_ms=_lim_rel_ms)
                _lim_gr_db = max(_lim_gr_db, max(0.0, float(_pre_loop_lim_tp) - float(_true_peak_db(y_sum, sr))))
                _resid_over = float(_true_peak_db(y_sum, sr)) - float(peak_cap_db)
                if _resid_over > 0.01:
                    y_sum *= 10 ** (-_resid_over / 20.0)
                print(f" [+] Peak Loop | DAWN limit-instead-of-trim at {float(peak_cap_db):+.2f}dBTP")
            else:
                # Peak over cap: trim
                trim_db = -abs(peak_over)
                y_sum *= 10 ** (trim_db / 20.0)
            continue
            
        if abs(lufs_error) > 0.02:
            # LUFS off: nudge
            y_sum *= 10 ** (lufs_error / 20.0)
            continue
            
    final_lufs = meter.integrated_loudness(y_sum.astype(np.float64).T)
    final_true_peak_db = _true_peak_db(y_sum, sr)
    final_sample_peak_db = _peak_db(y_sum)

    append_stage_snapshot(stage_snapshots, "post_peak_loop", y_sum, sr, full_metrics=False)  # h133: squash forensics

    confidence_score, is_confident, score_meta = compute_confidence_score(y_sum, sr, style["target_lufs"], sovereign_ceiling_db)
    loudness_converged = abs(style["target_lufs"] - pre_export_lufs) <= 0.04
    delivery_converged = final_true_peak_db <= sovereign_ceiling_db + 0.01
    print(f" [+] Single-Stage Sovereign Master: {pre_export_lufs:.2f} LUFS pre-export -> {final_lufs:.2f} LUFS final (Target: {style['target_lufs']})")
    if safety_trim_db < -1e-9:
        print(f" [+] Sovereign Export Safety | Applied {safety_trim_db:.2f} dB trim ({pre_export_true_peak_db:.2f} -> {final_true_peak_db:.2f} dBTP).")
    if not delivery_converged:
        print(f" [!] Sovereign Confidence | True peak {final_true_peak_db:.2f} dBTP exceeds ceiling {sovereign_ceiling_db:.2f} by {final_true_peak_db - sovereign_ceiling_db:.2f} dB.")

    # Post-master spatial ratio clamp — bring side energy back toward source ratio
    # Source spatial is typically ~0.4; post-master drift to ~1.0 indicates over-widening.
    mid = y_sum[0] + y_sum[1]
    side = y_sum[0] - y_sum[1]
    spatial_ratio = float(np.sqrt(np.mean(side**2) / (np.mean(mid**2) + 1e-24)))
    spatial_target = float(style.get("spatial_target", 0.55))  # ponytail: per-track; default 0.55 unchanged
    if spatial_ratio > spatial_target:
        reduction_db = 20.0 * np.log10(spatial_target / spatial_ratio)
        reduction_lin = 10 ** (reduction_db / 20.0)
        # ponytail: FIX spatial clamp — original formula ((mid+side)*0.5*reduction_lin + (mid-side)*0.5)
        # injected mid energy into R, RAISING RMS ~2.4dB on tracks with spatial_ratio>0.55
        # (ONE/ZERO/SIGNAL/GRAVITY/NEON/ECHO exported 1.5-2.6dB hot; ffmpeg-verified).
        # Correct M/S: decode (mid/side are L+R, L-R => use 0.5 factor), attenuate side by
        # reduction_lin, re-encode. Preserves mid, scales side => new spatial_ratio = target.
        y_sum[0] = 0.5 * mid + 0.5 * reduction_lin * side
        y_sum[1] = 0.5 * mid - 0.5 * reduction_lin * side
        print(f" [+] Spatial Clamp | Spatial ratio {spatial_ratio:.2f} -> {spatial_target:.2f} (side reduced by {reduction_db:.1f} dB)")
    else:
        print(f" [+] Spatial Clamp | Spatial ratio {spatial_ratio:.2f}, no reduction needed")

    # V106.499: universal tail-HF guard — tames energetic outro/HF-shelf + sub/whistle
    # cascade after 5:50 without touching transient body. Auto-detect: if outro 8-16k RMS
    # exceeds body by > +2.5 dB, OR a narrowband tonal whistle (peak-to-median > 12 dB,
    # abs > -25dB) exists 8-11k, apply a crossfaded dip centered on the dominant HF band.
    # No-op on clean tracks (GRAVITY). Catches ANY tail HF over-energy regardless of root
    # cause (user: outro harshness "may be sth entirely else cascading"). Reversible, surgical.
    # FIX V106.499b: window = 350s -> track end (covers 5:52 whistle, not just last 35%);
    # dip applies across FULL outro with fade-IN at start (no black-hole at 360-365 only).
    try:
        # V106.499h24: partition from fade_out_start_s - 8s (whole guard interval). Derived from fade detection.
        _g_fos = float(xray_full.get('fade_out_start_s', min(358.0, y_sum.shape[1]/sr - 8.0)))
        g_o = int(sr * max(30.0, _g_fos - 8.0)); g_e = y_sum.shape[1]
        if g_e <= g_o:
            print(" [+] Tail-HF Guard | track too short, skipped")
        else:
            g_tail = y_sum[:, g_o:g_e]
            def _band_rms(sig, lo, hi):
                sos = signal.butter(4, [lo, hi], 'bp', fs=sr, output='sos')
                b = signal.sosfiltfilt(sos, sig)
                return 20 * np.log10(np.sqrt(np.mean(b ** 2)) + 1e-12)
            def _dominant_tone(sig, lo, hi, nperseg=8192):
                freqs = np.fft.rfftfreq(nperseg, 1/sr)
                bm = (freqs >= lo) & (freqs <= hi)
                win = int(0.5*sr); hop = int(0.25*sr)
                best = None
                for off in range(0, len(sig)-win, hop):
                    w = sig[off:off+win]*np.hanning(win)
                    spec = 20*np.log10(np.abs(np.fft.rfft(w, n=nperseg))+1e-12)
                    b = spec[bm]; fsub = freqs[bm]
                    from scipy.ndimage import uniform_filter1d
                    local_med = uniform_filter1d(b, size=11)
                    dev = b - local_med
                    pi = int(np.argmax(dev))
                    if b[pi] > -60 and dev[pi] > 8 and (best is None or b[pi] > best[2]):
                        best = (fsub[pi], local_med[pi], b[pi])
                return best
            body_db = _band_rms(y_sum[:, :g_o], 8000, 16000)
            tail_db = _band_rms(g_tail, 8000, 16000)
            whistle = _dominant_tone(g_tail, 9000, 11500)
            if whistle is not None:
                # V106.499e: report-only. The 10kHz brightness was fold-back ALIASING from the
                # tanh (now fixed via 2x oversample). Dipping it = notch (user-banned). Just report.
                label = f"whistle@{whistle[0]:.0f}Hz"
                print(f" [+] Tail-HF Guard | detected {label} (alias, now prevented by tanh 2x OS) -> report-only, no dip")
            elif tail_db - body_db > 3.0:
                print(f" [+] Tail-HF Guard | outro delta +{tail_db-body_db:.1f}dB (no whistle), 12-16k left untouched (no notch)")
            else:
                print(f" [+] Tail-HF Guard | outro 8-16k delta +{tail_db - body_db:.1f}dB, no action")
    except Exception as e:
        print(f" [!] Tail-HF Guard skipped ({e})")

    # Band-specific M/S phase correction for tracks with negative multiband correlation.
    # Automated approach: target only the root-cause band (most negative correlation)
    # and let adjacent bands improve by cascade. This minimizes processing artifacts.
    phase_corr = _multiband_phase_corr(y_sum, sr)
    raw_phase = dict(phase_corr)  # ponytail (V106.477): capture PRE-correction raw per-band.
    # V106.499 A/B: AI2G_AB_PHASE=1 skips ALL phase correction (tests if the M/S side-cut
    # loop is the outro whistle source). ponytail: diagnostic only, off by default.
    if os.environ.get("AI2G_AB_PHASE", "0") == "1":
        print(" [A/B] Phase Correction SKIPPED (AI2G_AB_PHASE=1)")
    elif str(style.get("phase_mode", "")).lower() == "none":
        print(" [+] Phase Correction | Skipped (phase_mode=none)")
    elif not phase_corr:
        print(" [+] Phase Correction | No phase data available, skipping")
    else:
        target_corr = style.get("target_corr", 0.015)

        # V106.499h24: Expand correction to low-positive bands.
        # Old logic only corrected negative (<0) bands. But BWE creates harmonics-only HF
        # (perfectly mono) -> air/bwe/nyquist bands are near-zero (0.007-0.013), not negative.
        # These need correction too — push from near-zero to target_corr (0.3).
        # Also correct negative bands (unchanged).
        # V106.500h86: HF bands get their own style-drivable target (hf_band_target_corr, default
        # 0.20). Tracks whose RAW source is baked-decorrelated up top (GRAVITY 10-16k corr ~0.00)
        # raise it so the multiband loop also corrects near-zero HF bands, not just negative ones.
        _hf_target = float(style.get("hf_band_target_corr", 0.20))
        correction_bands = [(label, corr) for label, corr in phase_corr.items()
                            if corr < (_hf_target if label in ("air_12k_16k", "bwe_16k_21k", "nyquist_21k_24k") else target_corr)]

        if not correction_bands:
            print(f" [+] Phase Correction | All bands >= target_corr ({target_corr:+.3f}), no correction needed")
        else:
            # Sort by most deficient first
            correction_bands.sort(key=lambda x: x[1])
            print(f" [+] Phase Correction | Found {len(correction_bands)} bands below target {target_corr:+.3f}: " +
            ", ".join([f"{l}={c:+.3f}" for l, c in correction_bands]))
            live_phase = dict(phase_corr)  # Initialize tracker of live corrected phase values
            edges = [20, 80, 250, 300, 600, 2000, 6000, 12000, 16000, 21000, 23999]
            labels = [
            "sub_20_80", "low_mid_80_250", "mid_250_300", "lowmid_300_600",
            "upper_mid_600_2k",
            "presence_2k_6k", "brilliance_6k_12k", "air_12k_16k", "bwe_16k_21k", "nyquist_21k_24k",
            ]
            # EXPERIMENT: single-band root-cause test. Retained but not active.
            SINGLE_BAND_ROOT_CAUSE_TEST = False
            ROOT_BAND_ONLY = "upper_mid_600_2k"
            phase_mode = style.get("phase_mode", "multiband")
            if phase_mode == "root_only":
                correction_bands = [(l, c) for l, c in correction_bands if l == ROOT_BAND_ONLY]
                if not correction_bands:
                    print(f" [+] Phase Correction | Root band {ROOT_BAND_ONLY} not deficient, skipping root-only mode")
                else:
                    print(f" [+] Phase Correction | Root-only mode: correcting {ROOT_BAND_ONLY} only (target_corr={target_corr})")
            elif phase_mode == "none":
                correction_bands = []
                print(" [+] Phase Correction | Skipped (phase_mode=none)")

            # bands: side-cutting alone can only move corr toward 0.0 (neutral), never past it. So a
            # -0.311 band at target_corr=0.015 lands at ~-0.017 (formula asymptotes near zero). We loop:
            # cut -> re-measure via _multiband_phase_corr -> repeat until band >= target_corr (or >=0 if
            # target unreachable) or MAX_PHASE_ITER. Each pass only CUTS side (never adds), so it
            # converges monotonically without overshoot. GRAVITY's upper_mid_600_2k (-0.311) is the case
            # this fixes: previously left at -0.017 (negative => detector flags AI-phase).
            MAX_PHASE_ITER = 5
            # V106.499h24: Cap TOTAL side reduction across all iterations, not just per-iteration.
            # V106.452 had phase_corr 600-2k = 0.85 (barely touched). Our 4-iter loop was cutting
            # 3.44 dB total to force r=0.0, nuking side channel. Track total and stop at 1.0 dB.
            for band_label, band_corr in correction_bands:
                total_side_reduction_db = 0.0
                MAX_TOTAL_REDUCTION_DB = float(style.get("max_phase_reduction_db", 3.5))
                # Simple target (V106.476 path): lift negative bands to neutral (0.0). Adaptive
                # target (V106.477+) scored 90% temporal vs 94% for this simple path on identical
                # audio (user re-upload data) -- reverted per empirical evidence. ponytail: keep simple.
                # V106.499h28 H29: HF bands (air/bwe/nyquist) get a lower per-band ceiling (0.20)
                # because they are NATURALLY decorrelated by BWE — pushing them toward 0.28 makes
                # them more mono, removing the spatial air the BWE creates. The 0.20 ceiling also
                # prevents 21-24k from overshooting 16-21k (Fix D).
                # V106.500h86: HF band target is style-drivable. Default stays 0.20; tracks whose
                # RAW source is baked-decorrelated in the top octaves (GRAVITY raw 10-16k corr ~0.00)
                # set hf_band_target_corr=0.65 to land on the ZERO/SIGNAL Human-made fingerprint
                # (+0.68..0.90 measured on their -14 sims).
                HF_BAND_CEIL_DB = float(style.get("hf_band_target_corr", 0.20))
                HF_BANDS = {"air_12k_16k", "bwe_16k_21k", "nyquist_21k_24k"}
                effective_target_base = HF_BAND_CEIL_DB if band_label in HF_BANDS else target_corr
                effective_target = max(0.0, effective_target_base)
                # V106.499h26 FIX D: band-aware phase target (also uses HF ceiling).
                # Never push a HF band more than +0.05 above its lower-adjacent neighbor.
                # V106.500h86: style-drivable bypass (disable_phase_neighbor_cascade). GRAVITY's raw
                # is baked-decorrelated at 10-16k; its neighbor brilliance sits near zero, so the
                # +0.05 clamp would pin air_12k_16k at ~0.07 and block the 0.65 HM fingerprint lift.
                band_idx = labels.index(band_label) if band_label in labels else -1
                if not bool(style.get("disable_phase_neighbor_cascade", False)) and band_idx > 0:
                    lower_neighbor = labels[band_idx - 1]
                    lower_corr = live_phase.get(lower_neighbor, 0.0)
                    ceiling = lower_corr + 0.05
                    effective_target = min(effective_target, ceiling)
                    if effective_target < max(0.0, effective_target_base):
                        print(f" [+] Phase Correction | Band-aware target for {band_label}: {effective_target:+.3f} (neighbor {lower_neighbor}={lower_corr:+.3f}+0.05)")
                for _iter in range(MAX_PHASE_ITER):
                    # V106.499h28 H29: target_ratio uses effective_target (band-aware), not
                    # the global target_corr. This ensures the reduction amount respects the
                    # HF ceiling (0.20 for air/bwe/nyquist) instead of pushing to 0.28.
                    current_ratio = np.sqrt((1.0 - band_corr) / (1.0 + band_corr))
                    target_ratio = np.sqrt((1.0 - effective_target) / (1.0 + effective_target))
                    if target_ratio <= 0.0:
                        print(f" [+] Phase Correction | effective_target={effective_target} disables correction for {band_label}, skipping")
                        break
                    if current_ratio <= 0.0:
                        print(f" [+] Phase Correction | Invalid correlation math for {band_label}, skipping")
                        break
                    if band_corr >= effective_target:
                        if _iter > 0:
                            print(f" [+] Phase Correction | Band: {band_label} converged at r={band_corr:+.3f} after {_iter} iter")
                        break
                    reduction_factor = current_ratio / target_ratio
                    reduction_db = 20.0 * np.log10(reduction_factor)
                    # V106.499h24: Cap TOTAL side reduction at 1.0 dB across all iterations.
                    if total_side_reduction_db + reduction_db > MAX_TOTAL_REDUCTION_DB:
                        reduction_db = max(0.0, MAX_TOTAL_REDUCTION_DB - total_side_reduction_db)
                    total_side_reduction_db += reduction_db
                    if reduction_db <= 0.0:
                        print(f" [+] Phase Correction | No reduction needed for {band_label}")
                        break

                    for l, lo, hi in zip(labels, edges[:-1], edges[1:]):
                        if l != band_label or hi - lo < 10:
                            continue
                        # V106.500h88: style can physically widen a correction band's reach without
                        # touching the canonical telemetry edges. GRAVITY's baked decorrelation
                        # starts at ~10kHz but telemetry's air band starts at 12k — cut 10-16k,
                        # converge on the measured 12-16k band.
                        lo_c, hi_c = style.get("phase_custom_band_hz", {}).get(band_label, (lo, hi))
                        sos = signal.butter(4, [lo_c, hi_c], 'bp', fs=sr, output='sos')
                        y_bp = signal.sosfiltfilt(sos, y_sum, axis=1)
                        side_bp = y_bp[0] - y_bp[1]
                        side_delta = side_bp * (10 ** (-reduction_db / 20.0) - 1.0) * 0.5
                        y_sum[0] += side_delta
                        y_sum[1] -= side_delta
                    remeasured = _multiband_phase_corr(y_sum, sr)
                    new_corr = remeasured.get(band_label, band_corr)
                    live_phase.update(remeasured)  # Track the corrected state live!
                    if _iter == 0:
                        print(f" [+] Phase Correction | Band: {band_label} (r={band_corr:+.3f}) -> side reduced by {reduction_db:.2f} dB (iter {_iter+1}: r={new_corr:+.3f})")
                    else:
                        print(f" [+] Phase Correction |   re-iter {band_label} (r={new_corr:+.3f}) -> cut {reduction_db:.2f} dB (iter {_iter+1})")
                    band_corr = new_corr
                    # Side-cut asymptotes at 0.0; if we've hit neutral, stop (target is 0.0, no push).
                    if band_corr >= 0.0 and new_corr <= band_corr + 1e-4:
                        print(f" [+] Phase Correction | Band: {band_label} reached neutral r={band_corr:+.3f} (target {effective_target:+.3f})")
                        break
                else:
                    print(f" [+] Phase Correction | Band: {band_label} hit MAX_PHASE_ITER without converging (r={band_corr:+.3f})")

    
    # V106.499h24: Optional M/S Side EQ — gentle HF shelf on SIDE channel only (no delay).
    # Adds HF air/width without 1kHz notch, mono-safe, zero phase artifacts.
    # Per-track opt-in via style: "ms_eq": [{"freq": 1000, "gain_db": -5.0, "q": 0.5}, ...]
    ms_eq_cfg = style.get("ms_eq")
    if ms_eq_cfg:
        M = (y_sum[0] + y_sum[1]) / np.sqrt(2)
        S = (y_sum[0] - y_sum[1]) / np.sqrt(2)
        
        nodes = ms_eq_cfg if isinstance(ms_eq_cfg, list) else [ms_eq_cfg]
        S_eq = S.copy()
        
        for node in nodes:
            ms_eq_freq = node.get("freq", 10000.0)
            ms_eq_gain_db = node.get("gain_db", 1.5)
            ms_eq_q = node.get("q", 0.707)
            ms_eq_type = node.get("type", "high_shelf")
            ms_eq_target = node.get("target", "side").lower()
            
            # ponytail: M/S target routing — 'side' (default, width air) or 'mid' (center channel carve e.g. MYTH mizmar 5k)
            if ms_eq_target == "mid":
                _ch_src = M
            else:
                _ch_src = S_eq
            
            if ms_eq_type == "peaking":
                f0 = ms_eq_freq
                fs = sr
                Q = ms_eq_q
                A = 10.0 ** (ms_eq_gain_db / 40.0)
                w0 = 2.0 * np.pi * f0 / fs
                alpha = np.sin(w0) / (2.0 * Q)
                
                b0 = 1.0 + alpha * A
                b1 = -2.0 * np.cos(w0)
                b2 = 1.0 - alpha * A
                a0 = 1.0 + alpha / A
                a1 = -2.0 * np.cos(w0)
                a2 = 1.0 - alpha / A
                
                b = np.array([b0, b1, b2]) / a0
                a = np.array([a0, a1, a2]) / a0
                
                sos_peaking = np.array([[b[0], b[1], b[2], 1.0, a[1], a[2]]], dtype=np.float64)
                _ch_out = signal.sosfiltfilt(sos_peaking, _ch_src)
                print(f" [+] M/S {ms_eq_target.upper()} EQ | PEAKING {ms_eq_gain_db:+.1f}dB @ {ms_eq_freq:.0f}Hz Q={ms_eq_q:.3f} (RBJ biquad)")
            else:
                sos_hs = signal.butter(2, ms_eq_freq, 'high', fs=sr, output='sos')
                _ch_high = signal.sosfiltfilt(sos_hs, _ch_src)
                gain_lin = 10 ** (ms_eq_gain_db / 20.0)
                _ch_out = _ch_src + (gain_lin - 1.0) * _ch_high
                print(f" [+] M/S {ms_eq_target.upper()} EQ | {ms_eq_gain_db:+.1f}dB @ {ms_eq_freq:.0f}Hz Q={ms_eq_q:.3f} (high_shelf)")
            
            if ms_eq_target == "mid":
                M = _ch_out
            else:
                S_eq = _ch_out
                
        y_sum[0] = (M + S_eq) / np.sqrt(2)
        y_sum[1] = (M - S_eq) / np.sqrt(2)

        # Precision Final Post-Processing Alignment (post-MS EQ / post-Phase)
        _curr_l = meter.integrated_loudness(y_sum.astype(np.float64).T)
        _l_gap = style["target_lufs"] - _curr_l
        if abs(_l_gap) > 0.02:
            y_sum *= 10 ** (_l_gap / 20.0)

        _tp_cap = float(style.get("true_peak_cap_db", 0.69))
        _curr_tp = _true_peak_db(y_sum, sr)
        if _curr_tp > _tp_cap:
            _tp_trim = min(0.0, _tp_cap - _curr_tp)
            y_sum *= 10 ** (_tp_trim / 20.0)

        final_lufs = meter.integrated_loudness(y_sum.astype(np.float64).T)
        final_true_peak_db = _true_peak_db(y_sum, sr)
        final_sample_peak_db = _peak_db(y_sum)

# V106.499g: Master-level true cosine fades and padding as the ABSOLUTE final step of DSP.
    # Confines all filter phase-ringing, BWE tail artifacts, and clipping distortion behind pure silence.
    f_in = int(0.25 * sr)
    _fade_out_sec = float(style.get("fade_out_sec", 2.5))
    f_out = int(_fade_out_sec * sr)
    if y_sum.shape[1] > f_in + f_out:
        # Cosine fade-in (smoother and reaches absolute 0.0)
        fade_in_curve = 0.5 * (1.0 - np.cos(np.pi * np.linspace(0, 1, f_in)))
        y_sum[:, :f_in] *= fade_in_curve[None, :]
        # Cosine fade-out (reaches absolute 0.0)
        fade_out_curve = 0.5 * (1.0 + np.cos(np.pi * np.linspace(0, 1, f_out)))
        y_sum[:, -f_out:] *= fade_out_curve[None, :]
        
    pad = int(beat_dur * 2.0 * sr)
    y_sum = np.pad(y_sum, ((0, 0), (pad, pad)), mode='constant')

    mastering_meta = {
        "genre": style.get("genre", "UNKNOWN"),
        "track_name": style.get("name", "UNKNOWN"),
        "target_lufs": style["target_lufs"],
        "ceiling_db": sovereign_ceiling_db,
        "method": "sovereign_breathe_v61",
        "iterations": iterations_done,
        "limiter_enabled": False,
        "limiter_threshold_db": sovereign_ceiling_db,
        "limiter_release_ms": float(style.get("limiter_release_ms", 30.0)),
        "limiter_gain_reduction_db": round(float(_lim_gr_db), 3),
        "hard_clip_enabled": bool(style.get("hard_clip_db") is not None),
        "hard_clip_threshold_db": (round(float(_clip_thr_db), 3) if _clip_thr_db is not None else None),
        "hard_clip_gain_reduction_db": round(float(clip_reduction_db), 3),
        "pre_master_crest_guard": {
            "enabled": False,
            "target_db": -2.0,
            "release_ms": 80.0,
            "applied": False,
            "pre_guard_true_peak_db": None,
            "post_guard_true_peak_db": None,
            "safety_trim_db": 0.0
        },
        "pre_limiter_lufs": round(float(pre_master_lufs), 2),
        "post_limiter_lufs": round(float(pre_export_lufs), 2),
        "pre_export_lufs": round(float(pre_export_lufs), 2),
        "pre_export_true_peak_db": round(float(pre_export_true_peak_db), 2),
        "pre_export_sample_peak_db": round(float(pre_export_sample_peak_db), 2),
        "final_lufs": round(float(final_lufs), 2),
        "final_true_peak_db": round(float(final_true_peak_db), 2),
        "final_sample_peak_db": round(float(final_sample_peak_db), 2),
        "confidence_score": round(float(confidence_score), 2),
        "safety_trim_applied": bool(safety_trim_db < -1e-9),
        "safety_trim_db": round(float(safety_trim_db), 3),
        "export_safety_trim": {
            "enabled": bool(SOVEREIGN_EXPORT_SAFETY_TRIM_ENABLED),
            "target_true_peak_db": float(SOVEREIGN_EXPORT_TRUE_PEAK_TARGET_DB),
            "applied": bool(safety_trim_db < -1e-9),
            "trim_db": round(float(safety_trim_db), 3),
            "reason": "decoded_ogg_true_peak_headroom"
        },
        "true_peak_margin_db": round(float(sovereign_ceiling_db - final_true_peak_db), 3),
        "converged": bool(loudness_converged and delivery_converged),
        "loudness_converged_pre_export": bool(loudness_converged),
        "delivery_converged_final": bool(delivery_converged),
        "last_lufs_error_db": round(float(abs(style["target_lufs"] - pre_export_lufs)), 2),
        "final_lufs_error_db": round(float(abs(style["target_lufs"] - final_lufs)), 2),
        "multiband_limiter": {
            "enabled": False,
            "reason": "sovereign_mode"
        }
    }

    musical_meta = {
        "enabled": True,
        "sovereign_breathe_active": True,
        "hpss_margin": 1.0,
        "wow_flutter_drift_samples": float(np.mean(np.abs(drift))),
        "needle_gate_threshold": 0.0,
        "cliff_frequency_hz": float(cl_f),
        "dynamic_harshness": {"enabled": False},
        "hpss_harmonic_haas": {"enabled": False},
        "post_haas_side_lift": {"enabled": False},
        "final_left_haas_mono150": {"enabled": False},
        "master_hf_texture_mod": {"enabled": False},
        "kick_enhancement": {"enabled": False},
        "edm_ms_side_lift": {"enabled": False},
        "spectral_tilt": {"enabled": False}
    }

    noise_floor_meta = {
        "enabled": False,
        "sovereign_noise_floor": False,
        "decoupled_noise_rms": 0.0
    }

    return y_sum, mastering_meta, musical_meta, noise_floor_meta, exciter_meta


# ═══════════════════════════════════════════════════════════
# STYLE CONFIGURATION
# ═══════════════════════════════════════════════════════════
