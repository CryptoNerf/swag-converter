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
    # Small enough that the absorption pass decides what to keep by what it
    # would cost to lose, rather than by a blunt size floor that takes an
    # eyelash and a letter stroke along with the grain.
    "min_region_px": 8,
    "min_clusters": 10,
    "max_initial_regions": 420,
    "alpha_weight": 45.0,
    "alpha_residual_weight": 90.0,
    "max_residual": 12.0,
    "min_regions": 2,
    "gradient_stops": 5,
    "flat_tolerance": 2.0,
    "gradient_gain": 1.0,
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
        "max_initial_regions": 800,
        "merge_tolerance": 0.8,
        "curve_tolerance": 0.35,
    },
    "illustration": {
        # Smooth shading: more clusters so gradients have material to merge.
        "init_clusters": 40,
        "max_initial_regions": 1000,
        "merge_tolerance": 0.8,
        "curve_tolerance": 0.40,
    },
    "photo": {
        # Texture everywhere, and the region budget is what decides whether it
        # reads as detail or as smear.  There is deliberately no median filter
        # here any more: absorbing the least valuable regions already discards
        # grain, because grain is small *and* low-contrast, while a median
        # pass cannot tell a grain speck from an eyelash and took both.
        "denoise": 0,
        "init_clusters": 80,
        "max_initial_regions": 1500,
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

#: Quality tiers layered on top of a preset.  ``region_budget_scale`` is the
#: detail dial: how many regions the image is allowed to keep before the
#: cheapest start being absorbed, which is what decides whether texture reads
#: as detail or as smear, and is also what most of the run time buys.
QUALITY: dict[str, dict[str, Any]] = {
    "fast": {
        "merge_tolerance_scale": 2.0,
        "curve_tolerance_scale": 1.8,
        "supersample_cap": 2,
        "region_budget_scale": 0.35,
    },
    "balanced": {
        "merge_tolerance_scale": 1.0,
        "curve_tolerance_scale": 1.0,
        "supersample_cap": 4,
        "region_budget_scale": 1.0,
    },
    "max": {
        "merge_tolerance_scale": 0.4,
        "curve_tolerance_scale": 0.7,
        "supersample_cap": 4,
        "region_budget_scale": 2.0,
    },
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
    settings["max_initial_regions"] = max(
        16, int(round(float(settings["max_initial_regions"]) * tier["region_budget_scale"]))
    )
    settings["preset"] = preset
    settings["quality"] = quality
    if overrides:
        settings.update({key: value for key, value in overrides.items() if value is not None})
    return settings


def adapt_to_content(
    settings: dict[str, Any], flatness: float, carrying_colors: int = 0
) -> dict[str, Any]:
    """Spend colour clusters on colour, not on anti-aliasing.

    Every cluster beyond the colours an image actually holds lands on the
    blend between two of them, and those blends are thin: they shatter a
    letter into fragments that then absorb into the page, which is how body
    copy came out as gibberish, and they survive as grey slivers along every
    edge, which is what makes a plain black mark on white look smudged.

    Two measurements bound it.  ``flatness`` — the share of the picture that
    is one solid colour — says how far to lean away from the preset's number.
    ``carrying_colors`` — how few colours cover almost all of the pixels —
    is a ceiling: a mark in two colours has nothing for a thirteenth cluster
    to describe except the ramp between them.  The headroom above it leaves
    room for shading within a colour, which is why a shaded sphere is not
    capped down to the handful of colours it appears to hold.
    """
    settings = dict(settings)
    share = min(1.0, max(0.0, float(flatness)))
    clusters = int(settings.get("init_clusters", 40))
    floor = int(settings.get("min_clusters", 10))
    leaned = max(floor, int(round(clusters * (1.0 - 0.62 * share))))

    # The ceiling only applies where spare clusters would be spent on blends,
    # which is flat artwork.  On a photograph every cluster describes real
    # variation, and a count of covering balls badly understates what it
    # needs: the same measure that says a lettering poster holds six colours
    # says a photograph of foliage holds twelve.
    gate = float(settings.get("cluster_cap_flatness", 0.35))
    if carrying_colors > 0 and share >= gate:
        headroom = float(settings.get("cluster_headroom", 1.0))
        ceiling = max(4, int(round(carrying_colors * headroom)))
        leaned = min(leaned, ceiling)

    settings["init_clusters"] = max(2, leaned)
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
