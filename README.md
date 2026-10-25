# Sovereign Breathe

Single-file, CPU-only reference implementation of a full mastering chain that
brings AI-generated music sources (Suno-class generators) to commercial grade.
Derived from a production engine that mastered a full 12-track album.

## What it does

- **Resample** to working rate with polyphase anti-alias filtering
- **Restoration splice**: Apollo-class denoiser hook (or fallback hybrid path)
- **Harmonic excitation / BWE**: tanh-driven harmonic donor band with weld-zone
  RMS gain matching, so high-frequency extension survives mastering
- **Envelope-matched air band**: source air (12-16 kHz) drives the extended
  band envelope, no static noise floors, no compressor pumping
- **Spectral containment**: single 4th-order Butterworth crossover, no
  double-filter notches
- **Peak control**: true-peak-aware limiting with inter-sample headroom
- **Deterministic output**: fixed thread counts, fixed seeds, reproducible PCM

## Honest scope

This is the *reference* extract: the production engine also carries per-track
style tables, runtime config, telemetry, and platform-specific modules that
are not public. This repo gives the DSP architecture, not the product.

## Run

    pip install numpy scipy soundfile
    python sovereign_breathe.py --help

## Provenance

Built and battle-tested across a full album cycle; every module survived
ABX-style A/B against commercial reference FLACs (trance, metal, ambient).
The design brief: punchy and clean, no 8-16 kHz haze, no brickwall at 16 kHz,
no inter-module comb artifacts, air band that survives distribution codecs.

## License

MIT
