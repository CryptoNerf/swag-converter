"""Alpha-correct colour segmentation with gradient-aware region merging.

The image is clustered into colour regions, then neighbouring regions are
merged for as long as a single gradient still explains the union.  That is
what keeps smooth shading smooth instead of banding it into slivers, and it
is the step that decides both the quality and the cost of everything after.

Two properties matter for arbitrary input:

* the silhouette comes from alpha alone, so nothing is ever painted outside
  the artwork - a faint halo stays faint instead of becoming solid paint;
* merging is driven by a priority queue with lazy invalidation and a floor on
  the region count, because a textured photograph produces tens of thousands
  of starting regions and a linear scan over the candidate edges turns that
  into tens of minutes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import heapq
import itertools
from typing import Any, Iterator

import numpy as np
from PIL import Image
from scipy import ndimage
from skimage import color as skcolor

from .paint import Paint, fit_paint


_RESAMPLE = {
    "nearest": Image.Resampling.NEAREST,
    "bilinear": Image.Resampling.BILINEAR,
    "bicubic": Image.Resampling.BICUBIC,
    "lanczos": Image.Resampling.LANCZOS,
}


@dataclass
class Region:
    label: int
    pixels: np.ndarray  # flat indices into the hi-res raster
    area: int
    paint: Paint | None = None
    members: set[int] = field(default_factory=set)


@dataclass
class Segmentation:
    scale: int
    width: int
    height: int
    silhouette: np.ndarray
    coords: np.ndarray
    colors: np.ndarray
    alpha: np.ndarray
    regions: list[Region]
    translucent: bool

    @property
    def inside_indices(self) -> np.ndarray:
        """Flat raster indices of the silhouette pixels, in region-pixel order."""
        cached = getattr(self, "_inside_indices", None)
        if cached is None:
            cached = np.flatnonzero(self.silhouette.ravel())
            self._inside_indices = cached
        return cached


# --------------------------------------------------------------------------
# colour field preparation
# --------------------------------------------------------------------------

def _repair_colors(rgb: np.ndarray, alpha: np.ndarray, aa_radius: float) -> np.ndarray:
    """Replace blended rim colours with the nearest solid colour.

    Interior translucency (glass, smoke, shadows) is preserved: only pixels
    close to genuinely empty space are treated as anti-aliasing.
    """
    outside = alpha < 0.02
    if not outside.any():
        return rgb
    distance_to_outside = ndimage.distance_transform_edt(~outside)
    solid = alpha >= 0.9
    interior = (alpha >= 0.02) & (distance_to_outside > aa_radius)
    keep = solid | interior
    if not keep.any() or keep.all():
        return rgb
    _, indices = ndimage.distance_transform_edt(~keep, return_distances=True, return_indices=True)
    donor = rgb[indices[0], indices[1]]
    repaired = rgb.copy()
    replace = ~keep
    repaired[replace] = donor[replace]
    return repaired


def _coverage(alpha: np.ndarray, aa_radius: float, alpha_floor: float) -> np.ndarray:
    """Alpha remapped so a half-transparent *body* still reads as covered.

    The silhouette is thresholded at 0.5, which is right for an anti-aliased
    rim but would delete a genuinely see-through shape (smoke, glass, a soft
    shadow) whose alpha never reaches 0.5 anywhere.  Pixels are lifted towards
    full coverage only where the whole neighbourhood is translucent, so solid
    glyphs - the overwhelming majority - are left exactly as they were.

    ``alpha_floor`` is what separates a translucent body from a glow.  Several
    Apple glyphs carry a wide halo at roughly 5-15% alpha; promoting that into
    the silhouette is precisely the coloured-shadow defect this pipeline sets
    out to avoid, so anything fainter than the floor stays out.
    """
    outside = alpha < 0.02
    if not outside.any() or outside.all():
        return alpha
    window = max(3, int(2 * aa_radius) + 1)
    local_max = ndimage.maximum_filter(alpha, size=window)
    translucent_zone = (alpha >= alpha_floor) & (local_max < 0.9)
    if not translucent_zone.any():
        return alpha
    distance_to_outside = ndimage.distance_transform_edt(~outside)
    ramp = np.clip(distance_to_outside / max(aa_radius, 1e-6), 0.0, 1.0)
    return np.where(translucent_zone, alpha + (1.0 - alpha) * ramp, alpha).astype(np.float32)


def _upsample(array: np.ndarray, scale: int, method: str, channels: int) -> np.ndarray:
    height, width = array.shape[:2]
    if scale == 1:
        return array
    resample = _RESAMPLE.get(method, Image.Resampling.LANCZOS)
    mode = "RGB" if channels == 3 else "L"
    source = np.clip(array, 0.0, 255.0).astype(np.uint8)
    image = Image.fromarray(source, mode).resize((width * scale, height * scale), resample)
    result = np.asarray(image, dtype=np.float32)
    return result


# --------------------------------------------------------------------------
# clustering
# --------------------------------------------------------------------------

def _kmeans(features: np.ndarray, clusters: int, iterations: int, seed: int) -> np.ndarray:
    """Small, dependency-free k-means++ good enough for <=64 colour clusters."""
    count = len(features)
    clusters = max(1, min(clusters, count))
    generator = np.random.default_rng(seed)
    centres = np.empty((clusters, features.shape[1]), dtype=np.float64)
    centres[0] = features[generator.integers(count)]
    closest = ((features - centres[0]) ** 2).sum(axis=1)
    for index in range(1, clusters):
        total = float(closest.sum())
        if total <= 1e-12:
            centres[index] = features[generator.integers(count)]
        else:
            centres[index] = features[generator.choice(count, p=closest / total)]
        closest = np.minimum(closest, ((features - centres[index]) ** 2).sum(axis=1))
    labels = np.zeros(count, dtype=np.int32)
    for _ in range(iterations):
        distances = ((features[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2)
        updated = np.argmin(distances, axis=1).astype(np.int32)
        if np.array_equal(updated, labels):
            break
        labels = updated
        for index in range(clusters):
            selected = labels == index
            if selected.any():
                centres[index] = features[selected].mean(axis=0)
    return centres


def _assign(features: np.ndarray, centres: np.ndarray, chunk: int = 65536) -> np.ndarray:
    labels = np.empty(len(features), dtype=np.int32)
    for start in range(0, len(features), chunk):
        block = features[start : start + chunk]
        distances = ((block[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2)
        labels[start : start + chunk] = np.argmin(distances, axis=1)
    return labels


# --------------------------------------------------------------------------
# segmentation
# --------------------------------------------------------------------------

def build_segmentation(rgba: np.ndarray, settings: dict[str, Any]) -> Segmentation:
    scale = max(1, int(settings.get("supersample", 4)))
    method = str(settings.get("resample", "lanczos")).lower()
    clusters = max(2, int(settings.get("init_clusters", 40)))
    alpha_weight = float(settings.get("alpha_weight", 45.0))
    aa_radius = float(settings.get("aa_radius", 2.0))
    min_region = max(1, int(settings.get("min_region_px", 40)))
    seed = int(settings.get("seed", 12345))

    height, width = rgba.shape[:2]
    rgb = rgba[:, :, :3].astype(np.float32)
    alpha = rgba[:, :, 3].astype(np.float32) / 255.0
    repaired = _repair_colors(rgb, alpha, aa_radius)

    alpha_floor = float(settings.get("alpha_floor", 0.25))
    coverage = _coverage(alpha, aa_radius, alpha_floor)

    rgb_hi = _upsample(repaired, scale, method, 3)
    alpha_hi = np.clip(_upsample(alpha * 255.0, scale, method, 1) / 255.0, 0.0, 1.0)
    coverage_hi = np.clip(_upsample(coverage * 255.0, scale, method, 1) / 255.0, 0.0, 1.0)
    silhouette = coverage_hi >= 0.5
    if not silhouette.any():
        silhouette = coverage_hi > 0.0
    hi_height, hi_width = alpha_hi.shape

    if not silhouette.any():
        # A fully transparent input has nothing to trace.  Emoji always carry
        # ink, but a general raster can be empty, and k-means on zero samples
        # would otherwise fail with an opaque error deep in the clustering.
        return Segmentation(
            scale=scale,
            width=width,
            height=height,
            silhouette=silhouette,
            coords=np.empty((0, 2), dtype=np.float64),
            colors=np.empty((0, 3), dtype=np.float64),
            alpha=np.empty(0, dtype=np.float64),
            regions=[],
            translucent=False,
        )

    lab_hi = skcolor.rgb2lab(np.clip(rgb_hi / 255.0, 0.0, 1.0)).astype(np.float32)
    inside = silhouette.ravel()
    flat_lab = lab_hi.reshape(-1, 3)[inside]
    flat_alpha = alpha_hi.ravel()[inside]
    flat_rgb = rgb_hi.reshape(-1, 3)[inside]

    # Real interior translucency (not just an anti-aliased rim) has to survive
    # into fill-opacity, so it becomes a clustering dimension.
    translucent = bool((flat_alpha < 0.9).mean() > 0.06)
    if not translucent:
        # Everything left is solid material.  The only sub-unit alpha here is
        # the silhouette's own soft rim, and the renderer re-creates that when
        # it anti-aliases the path edge - baking it into the paint as well
        # would fade every rim twice.
        flat_alpha = np.ones_like(flat_alpha)
    if translucent:
        features = np.column_stack([flat_lab, flat_alpha * alpha_weight]).astype(np.float64)
    else:
        features = flat_lab.astype(np.float64)

    # Centres come from the source-resolution image: up-sampling re-introduces
    # blended pixels along every edge, and clustering those would invent an
    # intermediate colour that belongs to neither side.
    source_inside = coverage >= 0.5
    if not source_inside.any():
        source_inside = coverage > 0.0
    source_lab = skcolor.rgb2lab(np.clip(repaired / 255.0, 0.0, 1.0)).astype(np.float32)[source_inside]
    source_alpha = alpha[source_inside]
    if translucent:
        source_features = np.column_stack([source_lab, source_alpha * alpha_weight]).astype(np.float64)
    else:
        source_features = source_lab.astype(np.float64)
    sample_limit = int(settings.get("cluster_sample_limit", 20000))
    if len(source_features) > sample_limit:
        generator = np.random.default_rng(seed)
        sample = source_features[generator.choice(len(source_features), sample_limit, replace=False)]
    else:
        sample = source_features
    centres = _kmeans(sample, clusters, int(settings.get("kmeans_iterations", 20)), seed)
    cluster_labels = _assign(features, centres)

    label_image = np.zeros(hi_height * hi_width, dtype=np.int32)
    label_image[inside] = cluster_labels + 1
    label_image = label_image.reshape(hi_height, hi_width)

    components = np.zeros_like(label_image)
    next_label = 0
    for cluster in range(1, len(centres) + 1):
        mask = label_image == cluster
        if not mask.any():
            continue
        labelled, found = ndimage.label(mask)
        if found:
            components[mask] = labelled[mask] + next_label
            next_label += found

    # Intricate glyphs (snowflakes, dense flags) can shatter into a thousand
    # disconnected fragments.  Those never merge - the region graph only joins
    # touching regions - so they would drive both run time and node count.
    # Pick the speck threshold straight from the size distribution so the
    # count lands under the cap in a single absorption pass.
    max_initial = max(16, int(settings.get("max_initial_regions", 420)))
    threshold = min_region
    _, counts = np.unique(components.ravel()[inside], return_counts=True)
    interior = np.sort(counts[1:] if (components.ravel()[inside] == 0).any() else counts)
    if len(interior) > max_initial:
        threshold = max(min_region, int(interior[len(interior) - max_initial]) + 1)
    components = _absorb_small(components, features, inside, threshold, hi_height, hi_width)

    coords_y, coords_x = np.divmod(np.flatnonzero(inside), hi_width)
    coords = np.column_stack([(coords_x + 0.5) / scale, (coords_y + 0.5) / scale]).astype(np.float64)
    inside_components = components.ravel()[inside]

    regions: list[Region] = []
    for identifier in np.unique(inside_components):
        if identifier <= 0:
            continue
        pixels = np.flatnonzero(inside_components == identifier)
        label = len(regions)
        regions.append(Region(label=label, pixels=pixels, area=len(pixels), members={label}))

    return Segmentation(
        scale=scale,
        width=width,
        height=height,
        silhouette=silhouette,
        coords=coords,
        colors=flat_rgb.astype(np.float64),
        alpha=flat_alpha.astype(np.float64),
        regions=regions,
        translucent=translucent,
    )


def _component_adjacency(components: np.ndarray) -> dict[int, set[int]]:
    """Neighbour map for every labelled component, in two vectorised passes."""
    graph: dict[int, set[int]] = {}
    for first, second in ((components[:, :-1], components[:, 1:]), (components[:-1, :], components[1:, :])):
        differing = (first != second) & (first > 0) & (second > 0)
        if not differing.any():
            continue
        pairs = np.unique(np.stack([first[differing], second[differing]], axis=1), axis=0)
        for a, b in pairs:
            graph.setdefault(int(a), set()).add(int(b))
            graph.setdefault(int(b), set()).add(int(a))
    return graph


def _absorb_small(
    components: np.ndarray, features: np.ndarray, inside: np.ndarray, min_region: int, height: int, width: int
) -> np.ndarray:
    """Fold sub-threshold specks into the closest-coloured touching region."""
    flat = components.ravel()
    inside_components = flat[inside]
    identifiers, counts = np.unique(inside_components, return_counts=True)
    sizes = {int(identifier): int(count) for identifier, count in zip(identifiers, counts) if identifier > 0}
    small = sorted((identifier for identifier, count in sizes.items() if count < min_region), key=lambda key: sizes[key])
    if not small:
        return components
    graph = _component_adjacency(components)
    # One pass for every component mean.  Selecting each component with a
    # boolean scan instead is O(components x pixels) and dominates the whole
    # conversion once an image has a few thousand specks.
    width_features = features.shape[1]
    top = int(inside_components.max()) + 1
    counts_all = np.bincount(inside_components, minlength=top).astype(np.float64)
    counts_all[counts_all == 0] = 1.0
    sums = np.stack(
        [np.bincount(inside_components, weights=features[:, channel], minlength=top) for channel in range(width_features)],
        axis=1,
    )
    averages = sums / counts_all[:, None]
    means = {identifier: averages[identifier] for identifier in sizes}
    # Redirect through a union-find style map so a chain of absorptions still
    # resolves to one surviving label, then relabel the raster once.
    redirect: dict[int, int] = {}

    def resolve(identifier: int) -> int:
        while identifier in redirect:
            identifier = redirect[identifier]
        return identifier

    pending = set(small)
    for identifier in small:
        if identifier not in pending:
            continue
        neighbours = {resolve(other) for other in graph.get(identifier, set())}
        neighbours.discard(resolve(identifier))
        if not neighbours:
            continue
        large = [other for other in neighbours if other not in pending]
        choices = large or list(neighbours)
        target = min(choices, key=lambda other: float(np.linalg.norm(means[other] - means[identifier])))
        redirect[identifier] = target
        pending.discard(identifier)
        merged_size = sizes[target] + sizes[identifier]
        weight = sizes[identifier] / merged_size
        means[target] = means[target] * (1.0 - weight) + means[identifier] * weight
        sizes[target] = merged_size
        graph.setdefault(target, set()).update(graph.get(identifier, set()))
        graph[target].discard(target)
        if target in pending and merged_size >= min_region:
            pending.discard(target)
    if not redirect:
        return components
    lookup = np.arange(int(flat.max()) + 1, dtype=np.int32)
    for identifier in redirect:
        lookup[identifier] = resolve(identifier)
    return lookup[components]


# --------------------------------------------------------------------------
# gradient-aware merging
# --------------------------------------------------------------------------

def _adjacency(segmentation: Segmentation, components: np.ndarray) -> dict[int, set[int]]:
    """Neighbour map keyed by ``Region.label``.

    ``region_label_image`` stores ``label + 1`` so that 0 can mean "outside";
    the offset is undone here so the graph and the active-region table agree.
    """
    graph: dict[int, set[int]] = {region.label: set() for region in segmentation.regions}
    for first, second in ((components[:, :-1], components[:, 1:]), (components[:-1, :], components[1:, :])):
        differing = (first != second) & (first > 0) & (second > 0)
        if not differing.any():
            continue
        pairs = np.unique(np.stack([first[differing], second[differing]], axis=1), axis=0)
        for a, b in pairs:
            left, right = int(a) - 1, int(b) - 1
            if left in graph and right in graph:
                graph[left].add(right)
                graph[right].add(left)
    return graph


def region_label_image(segmentation: Segmentation) -> np.ndarray:
    hi_height = segmentation.height * segmentation.scale
    hi_width = segmentation.width * segmentation.scale
    image = np.zeros(hi_height * hi_width, dtype=np.int32)
    inside_indices = np.flatnonzero(segmentation.silhouette.ravel())
    for region in segmentation.regions:
        image[inside_indices[region.pixels]] = region.label + 1
    return image.reshape(hi_height, hi_width)


def merge_regions(
    segmentation: Segmentation, settings: dict[str, Any], thresholds: list[float]
) -> Iterator[tuple[float, list[Region]]]:
    """Greedily merge neighbours while a single gradient still explains them.

    The cost of a merge is how much *worse* the joint paint model is than the
    two separate ones, area-weighted.  Merging stops on that cost rather than
    on a region quota, so a dark eye is never dissolved into a face just to
    hit a target count.

    Candidates live in a heap with lazy invalidation: a popped entry is
    discarded when either side has already been merged away, or when its cost
    was computed against a stale version of a region.  A linear ``min()`` over
    the candidate table is O(regions x edges) and takes tens of minutes on a
    photograph; this is O(edges log edges).
    """
    max_residual = float(settings.get("max_residual", 7.5))
    min_regions = max(1, int(settings.get("min_regions", 2)))
    components = region_label_image(segmentation)
    graph = _adjacency(segmentation, components)
    active: dict[int, Region] = {region.label: region for region in segmentation.regions}
    for region in active.values():
        region.paint = fit_paint(
            segmentation.coords[region.pixels],
            segmentation.colors[region.pixels],
            segmentation.alpha[region.pixels],
            settings,
            seed=region.label,
        )

    pending = sorted({float(value) for value in thresholds})
    # Bumped whenever a region changes, so stale heap entries are detectable.
    version: dict[int, int] = {label: 0 for label in active}

    def cost(first: int, second: int) -> tuple[float, Paint]:
        left, right = active[first], active[second]
        pixels = np.concatenate([left.pixels, right.pixels])
        paint = fit_paint(
            segmentation.coords[pixels],
            segmentation.colors[pixels],
            segmentation.alpha[pixels],
            settings,
            seed=first * 7919 + second,
        )
        total = left.area + right.area
        baseline = (left.paint.residual * left.area + right.paint.residual * right.area) / max(total, 1)
        penalty = 0.0 if paint.residual <= max_residual else (paint.residual - max_residual) * 100.0
        return paint.residual - baseline + penalty, paint

    heap: list[tuple[float, int, int, int, int, int, Paint]] = []
    counter = itertools.count()

    def offer(first: int, second: int) -> None:
        if first == second or first not in active or second not in active:
            return
        low, high = (first, second) if first < second else (second, first)
        value, paint = cost(low, high)
        # The counter breaks ties before Python can try to order two Paints.
        heapq.heappush(heap, (value, next(counter), low, high, version[low], version[high], paint))

    for first, neighbours in graph.items():
        for second in neighbours:
            if first < second:
                offer(first, second)

    while pending and heap and len(active) > min_regions:
        value, _, first, second, first_version, second_version, paint = heapq.heappop(heap)
        if first not in active or second not in active:
            continue
        if version[first] != first_version or version[second] != second_version:
            continue  # one side changed after this cost was computed
        while pending and value > pending[0]:
            yield pending.pop(0), _snapshot(active)
        if not pending:
            break
        merged = Region(
            label=first,
            pixels=np.concatenate([active[first].pixels, active[second].pixels]),
            area=active[first].area + active[second].area,
            paint=paint,
            members=active[first].members | active[second].members,
        )
        del active[second]
        active[first] = merged
        version[first] += 1
        neighbours = (graph.pop(second, set()) | graph.get(first, set())) - {first, second}
        graph[first] = neighbours
        for other in neighbours:
            graph[other].discard(second)
            graph[other].add(first)
        for other in neighbours:
            offer(first, other)

    while pending:
        yield pending.pop(0), _snapshot(active)


def _snapshot(active: dict[int, Region]) -> list[Region]:
    return [
        Region(label=region.label, pixels=region.pixels, area=region.area, paint=region.paint, members=set(region.members))
        for region in sorted(active.values(), key=lambda item: -item.area)
    ]
