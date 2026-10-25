"""Conservative three-sample validation of physical pendulum target crossings.

A valid target need only be reached in ONE sample near the top of a swing.
Require close, temporally adjacent measurements on both sides of that
sample to reject a solitary sensor glitch. Safety events are handled elsewhere.
"""
import math
from collections import deque


class TargetCrossingDetector:
    def __init__(self, target_deg: float, *, max_neighbor_gap_s: float = 0.25,
                 near_target_band_deg: float = 3.0) -> None:
        self.target_deg = float(target_deg)
        self.max_neighbor_gap_s = max_neighbor_gap_s
        self.near_target_band_deg = near_target_band_deg
        self._samples: deque[tuple[float, float]] = deque(maxlen=3)

    def reset(self) -> None:
        self._samples.clear()

    def observe(self, sample_time_s: float, signed_angle_deg: float) -> float | None:
        """Return confirmed first over-threshold sample's time, or None.

        Sample time must use Workbench monotonic time, the same clock as the
        experiment duration; never return a time from the MCU clock here.
        """
        if not (math.isfinite(sample_time_s) and math.isfinite(signed_angle_deg)):
            self.reset()
            return None
        angle = abs(signed_angle_deg)
        if self._samples and not 0 < sample_time_s - self._samples[-1][0] <= self.max_neighbor_gap_s:
            self.reset()
        self._samples.append((sample_time_s, angle))
        if len(self._samples) != 3:
            return None

        before, candidate, after = self._samples
        if (candidate[1] >= self.target_deg
                and before[1] >= self.target_deg - self.near_target_band_deg
                and after[1] >= self.target_deg - self.near_target_band_deg
                and abs(candidate[1] - before[1]) <= self.near_target_band_deg
                and abs(candidate[1] - after[1]) <= self.near_target_band_deg):
            return candidate[0]
        return None
