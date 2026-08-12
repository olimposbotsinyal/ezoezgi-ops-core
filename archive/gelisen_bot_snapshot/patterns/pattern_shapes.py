# patterns/pattern_shapes.py
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


@dataclass
class PatternShape:
    name: str
    bullish: bool
    confidence: float = 0.0
    progress: float = 0.0
    breakout: bool = False
    breakout_confirmed: bool = False
    breakout_level: Optional[float] = None
    target_levels: List[float] = field(default_factory=list)
    lines: List[dict] = field(default_factory=list)
    areas: List[dict] = field(default_factory=list)
    label_points: List[Tuple[int, float, str]] = field(default_factory=list)
    quality_score: float = 0.0
    pivot_points: List[Tuple[int, float]] = field(default_factory=list)
