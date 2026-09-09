"""Tracing presets, and what each one trades away.

Numbers here were tuned by measuring SSIM against the source at a range of
settings, not guessed.  The photo preset exists because a textured image
starts with tens of thousands of regions: without a hard ceiling the merge
stage runs for tens of minutes and still ends up discarding the detail.
"""

from __future__ import annotations

from typing import Any


#: Shared defaults; a preset only states what it changes.
BASE: dict[str, Any] = {
    "supersample": 4,
    "resample": "lanczos",
    "aa_radius": 2.0,
    "alpha_floor": 0.25,
    "init_clusters": 40,
    "min_region_px": 40,
    "max_initial_regions": 420,
    "alpha_weight": 45.0,
    "alpha_residual_weight": 90.0,
    "max_residual": 12.0,
    "min_regions": 2,
    "gradient_stops": 5,
    "flat_tolerance": 2.0,
    "gradient_gain": 0.55,
    "stop_prune_tolerance": 1.4,
    "fit_sample_limit": 2500,
    "seam_overlap": 1,
    "base_layer": True,
    "corner_angle": 62.0,
    "smooth_passes": 4,
    "precision": 1,
    "min_path_area": 0.6,
    "denoise": 0,
}

PRESETS: dict[str, dict[str, Any]] = {
    "icon": {
        # Flat artwork: few colours, crisp edges worth preserving exactly.
        "init_clusters": 28,
        "min_region_px": 32,
        "max_initial_regions": 320,
        "merge_tolerance": 0.8,
        "curve_tolerance": 0.35,
    },
    "illustration": {
        # Smooth shading: more clusters so gradients have material to merge.
        "init_clusters": 40,
        "min_region_px": 40,
        "max_initial_regions": 420,
        "merge_tolerance": 0.8,
        "curve_tolerance": 0.40,
    },
    "photo": {
        # Texture everywhere.  Denoise first, then keep enough clusters and a
        # high enough region floor that the result reads as a deliberate
        # stylisation rather than a smear: at 26 regions the subject dissolves,
        # at ~77 the scene stays legible for about two seconds more.
        "denoise": 3,
        "init_clusters": 80,
        "min_region_px": 28,
        "max_initial_regions": 600,
        "min_regions": 60,
        "supersample": 3,
        "merge_tolerance": 0.8,
        "curve_tolerance": 0.5,
        "smooth_passes": 5,
        "max_residual": 20.0,
    },
    "poster": {
        # Deliberately stylised: few big flat shapes.
        "denoise": 3,
        "init_clusters": 16,
        "min_region_px": 220,
        "max_initial_regions": 140,
        "min_regions": 8,
        "supersample": 2,
        "merge_tolerance": 6.0,
        "curve_tolerance": 1.2,
        "smooth_passes": 6,
        "max_residual": 30.0,
        "gradient_gain": 0.4,
    },
}

#: Quality tiers layered on top of a preset.
QUALITY: dict[str, dict[str, Any]] = {
    "fast": {"merge_tolerance_scale": 2.0, "curve_tolerance_scale": 1.8, "supersample_cap": 2},
    "balanced": {"merge_tolerance_scale": 1.0, "curve_tolerance_scale": 1.0, "supersample_cap": 4},
    "max": {"merge_tolerance_scale": 0.4, "curve_tolerance_scale": 0.7, "supersample_cap": 4},
}


def build(preset: str, quality: str = "balanced", overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    if preset not in PRESETS:
        raise ValueError(f"Unknown preset {preset!r}. Choose from: {', '.join(sorted(PRESETS))}")
    if quality not in QUALITY:
        raise ValueError(f"Unknown quality {quality!r}. Choose from: {', '.join(QUALITY)}")
    settings = dict(BASE)
    settings.update(PRESETS[preset])
    tier = QUALITY[quality]
    settings["merge_tolerance"] = float(settings["merge_tolerance"]) * tier["merge_tolerance_scale"]
    settings["curve_tolerance"] = float(settings["curve_tolerance"]) * tier["curve_tolerance_scale"]
    settings["supersample"] = min(int(settings["supersample"]), int(tier["supersample_cap"]))
    settings["preset"] = preset
    settings["quality"] = quality
    if overrides:
        settings.update({key: value for key, value in overrides.items() if value is not None})
    return settings


def adapt_to_size(settings: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    """Scale the super-sampling grid down as the image grows.

    Sub-pixel contours matter most on small artwork; on a large image the
    source pixels are already fine enough, and a 4x grid would mean tracing a
    raster sixteen times the input area.
    """
    settings = dict(settings)
    longest = max(width, height)
    if longest > 1400:
        cap = 1
    elif longest > 700:
        cap = 2
    elif longest > 360:
        cap = 3
    else:
        cap = 4
    settings["supersample"] = max(1, min(int(settings.get("supersample", 4)), cap))
    return settings
