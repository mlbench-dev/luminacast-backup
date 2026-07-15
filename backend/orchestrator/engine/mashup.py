"""
Mashup Engine — variant selection, weighting, repeat prevention.
Handles sequential and shuffle modes for streaming block playback.
"""
import random
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BlockInfo:
    """Lightweight representation of a block for the mashup engine."""
    id: str
    position: int
    block_type: str  # "intro", "product", "closing", etc.
    product_id: Optional[str] = None
    variants: list = field(default_factory=list)


@dataclass
class VariantInfo:
    """Lightweight representation of a variant."""
    id: str
    block_id: str
    weight: float = 1.0
    times_played: int = 0
    purchases_during: int = 0
    performance_score: float = 0.0
    scene_image_key: Optional[str] = None


def calculate_performance_score(purchases_during: int, times_played: int) -> float:
    """
    Calculate variant performance score.
    score = purchases_during / times_played, capped at 1.0.
    Returns 0 when times_played is 0.
    """
    if times_played <= 0:
        return 0.0
    score = purchases_during / times_played
    return min(score, 1.0)


class MashupEngine:
    """
    Sequences blocks and selects variants during streaming.
    Supports sequential and shuffle modes.
    """

    def __init__(self, blocks: list[BlockInfo], mode: str = "sequential", randomness: float = 0.5):
        """
        Args:
            blocks: List of BlockInfo objects, sorted by position.
            mode: "sequential" or "shuffle"
            randomness: 0.0 = always pick best variant, 1.0 = uniform random
        """
        self.blocks = sorted(blocks, key=lambda b: b.position)
        self.mode = mode
        self.randomness = max(0.0, min(1.0, randomness))
        self._current_index = 0
        self._play_history: list[str] = []  # block IDs
        self._variant_history: list[str] = []  # variant IDs
        self._scene_history: list[str] = []  # scene image keys
        self._cycle_count = 0
        self._shuffle_order: list[int] = []

        if not blocks:
            raise ValueError("MashupEngine requires at least one block")

        if mode == "shuffle":
            self._generate_shuffle_order()

    def _generate_shuffle_order(self):
        """Generate shuffled order preserving intro first and closing last."""
        intro_indices = [i for i, b in enumerate(self.blocks) if b.block_type == "intro"]
        closing_indices = [i for i, b in enumerate(self.blocks) if b.block_type == "closing"]
        middle_indices = [i for i, b in enumerate(self.blocks)
                         if b.block_type not in ("intro", "closing")]

        random.shuffle(middle_indices)

        self._shuffle_order = intro_indices + middle_indices + closing_indices
        self._current_index = 0

    def get_next(self) -> Optional[BlockInfo]:
        """Get the next block to play."""
        if not self.blocks:
            return None

        if self.mode == "sequential":
            block = self.blocks[self._current_index % len(self.blocks)]
            self._current_index += 1
            if self._current_index >= len(self.blocks):
                self._current_index = 0
                self._cycle_count += 1
        else:  # shuffle
            if self._current_index >= len(self._shuffle_order):
                self._generate_shuffle_order()
                self._cycle_count += 1
            idx = self._shuffle_order[self._current_index]
            block = self.blocks[idx]
            self._current_index += 1

        self._play_history.append(block.id)
        return block

    def select_variant(self, block: BlockInfo) -> Optional[VariantInfo]:
        """
        Select a variant for the given block using weighted randomness.
        - randomness=0: always pick highest weight/score
        - randomness=1: uniform random
        - Between: weighted by performance score + weight
        """
        if not block.variants:
            return None

        variants = block.variants

        if self.randomness == 0:
            # Deterministic: pick highest weight
            best = max(variants, key=lambda v: v.weight + (v.performance_score or 0))
            self._variant_history.append(best.id)
            return best

        if self.randomness >= 1.0:
            # Uniform random, but avoid immediate repeat
            candidates = [v for v in variants if v.id != (self._variant_history[-1] if self._variant_history else None)]
            if not candidates:
                candidates = variants
            chosen = random.choice(candidates)
            self._variant_history.append(chosen.id)
            return chosen

        # Weighted selection
        weights = []
        for v in variants:
            base_weight = v.weight + (v.performance_score or 0) * 2
            # Reduce weight if recently played
            if self._variant_history and v.id == self._variant_history[-1]:
                base_weight *= 0.1  # Strong penalty for immediate repeat
            # Blend with uniform based on randomness
            uniform = 1.0
            blended = base_weight * (1 - self.randomness) + uniform * self.randomness
            weights.append(max(blended, 0.01))

        total = sum(weights)
        weights = [w / total for w in weights]

        chosen = random.choices(variants, weights=weights, k=1)[0]
        self._variant_history.append(chosen.id)

        if chosen.scene_image_key:
            self._scene_history.append(chosen.scene_image_key)

        return chosen

    def check_scene_variety(self, scene_key: str) -> bool:
        """
        Check if playing this scene would violate the 3x consecutive rule.
        Returns True if it's okay to play, False if it would be 3+ in a row.
        """
        if len(self._scene_history) < 2:
            return True
        return not (self._scene_history[-1] == scene_key and self._scene_history[-2] == scene_key)

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    @property
    def play_count(self) -> int:
        return len(self._play_history)
