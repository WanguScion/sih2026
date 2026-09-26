"""
segmenter.py
------------
Thin wrapper around a HuggingFace SegFormer model for semantic
segmentation of raster tiles.

Works with any SegFormer checkpoint on the Hugging Face Hub or a local
fine-tuned checkpoint directory — e.g. a model fine-tuned on land-cover /
building-footprint / road classes for remote sensing. Defaults to a
general-purpose ADE20K checkpoint, which is fine for a smoke test but
should be swapped for a remote-sensing-specific model for real GIS work
(see README).
"""

from __future__ import annotations

import logging
from typing import List, Optional

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

DEFAULT_MODEL = "alanoix/segformer_b0_flair_one"

# Substrings matched (case-insensitive) against a model's id2label names to
# auto-detect which output classes represent buildings/structures. Covers
# ADE20K-style label sets ("building;edifice", "house", "skyscraper",
# "hovel", "hut") as well as simpler binary building/background models
# fine-tuned on remote-sensing data (where the class is often just
# "building").
BUILDING_KEYWORDS = ("building", "house", "skyscraper", "hovel", "hut", "roof")


def match_building_classes(id2label: dict, keywords: Optional[tuple] = None) -> set:
    """
    Pure function (no model required): return the set of class ids whose
    label name matches any of `keywords` (default BUILDING_KEYWORDS).
    Factored out of SegFormerSegmenter so it's unit-testable without
    downloading model weights.
    """
    kws = tuple(k.lower() for k in (keywords or BUILDING_KEYWORDS))
    return {
        int(idx) for idx, name in id2label.items()
        if any(kw in name.lower() for kw in kws)
    }


class SegFormerSegmenter:
    def __init__(
        self,
        model_name_or_path: str = DEFAULT_MODEL,
        device: Optional[str] = None,
    ):
        # Imported lazily so importing geoseg.segmenter (e.g. for
        # `match_building_classes`, or building CLI --help) doesn't require
        # torch/transformers unless you actually instantiate a segmenter.
        import torch
        from transformers import SegformerForSemanticSegmentation, SegformerImageProcessor

        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        log.info("Loading SegFormer model '%s' on %s", model_name_or_path, self.device)

        self.processor = SegformerImageProcessor.from_pretrained(model_name_or_path)
        self.model = SegformerForSemanticSegmentation.from_pretrained(model_name_or_path)
        self.model.to(self.device)
        self.model.eval()

        self.id2label = self.model.config.id2label

    @property
    def class_names(self) -> List[str]:
        return [self.id2label[i] for i in sorted(self.id2label)]

    def building_class_ids(self, keywords: Optional[tuple] = None) -> set:
        """
        Return the set of class ids whose label name matches any of
        `keywords` (default BUILDING_KEYWORDS), used to restrict polygon
        generation to building/structure classes only.

        Works for both multi-class scene-segmentation checkpoints (e.g.
        ADE20K, where "building;edifice", "house", "skyscraper" etc. are
        separate classes) and binary/few-class remote-sensing checkpoints
        fine-tuned specifically for building extraction (where the
        positive class is typically just named "building").
        """
        return match_building_classes(self.id2label, keywords)

    def predict_tile(self, tile_array: np.ndarray) -> np.ndarray:
        """
        tile_array: (bands, H, W) uint8/uint16 array read from the GeoTIFF
        (1 or 3 bands). Returns an (H, W) int32 array of predicted class
        ids, resized back to the tile's original pixel dimensions so it
        aligns 1:1 with the source raster grid.
        """
        import torch

        h, w = tile_array.shape[-2], tile_array.shape[-1]
        img = self._to_pil(tile_array)

        with torch.no_grad():
            inputs = self.processor(images=img, return_tensors="pt").to(self.device)
            outputs = self.model(**inputs)
            logits = outputs.logits  # (1, num_classes, h', w')

            upsampled = torch.nn.functional.interpolate(
                logits, size=(h, w), mode="bilinear", align_corners=False
            )
            pred = upsampled.argmax(dim=1)[0].cpu().numpy().astype(np.int32)
        return pred

    @staticmethod
    def _to_pil(tile_array: np.ndarray) -> Image.Image:
        """Convert a (bands, H, W) geo-tile into an RGB PIL image the
        image processor expects, normalizing bit-depth to 8-bit."""
        arr = tile_array
        if arr.dtype != np.uint8:
            arr = arr.astype(np.float32)
            lo, hi = np.percentile(arr, 1), np.percentile(arr, 99)
            if hi <= lo:
                hi = lo + 1
            arr = np.clip((arr - lo) / (hi - lo), 0, 1) * 255
            arr = arr.astype(np.uint8)

        if arr.shape[0] == 1:
            arr = np.repeat(arr, 3, axis=0)
        elif arr.shape[0] > 3:
            arr = arr[:3]

        chw_to_hwc = np.transpose(arr, (1, 2, 0))
        return Image.fromarray(chw_to_hwc, mode="RGB")
